"""Admin-Konfiguration von SSO (OIDC, LDAP/AD) – F10 3.3, 5.10; eigener Router (Bauplan B.13, ``ROUTER_ORDER = 102``).

Alle Endpunkte: Admin mit Browser-Session (``get_admin_session_user``) – ein geleakter Admin-Token darf den Login
nicht auf einen fremden Anmeldedienst umbiegen.

- ``GET /settings/sso``: Einstellungen, Secrets nur als Maske (+ ``*_set``/``*_unreadable``), Zahl verknuepfter Konten,
  dazu ``general.session_max_age`` (Sekunden, ``AUTH_COOKIE_MAX_AGE``) fuer die Warnung zur Sitzungsdauer (E-F10-2).
- ``PUT /settings/sso``: Pruefen und Speichern ueber ``services/sso_settings.update``. Aendert der Request sensible
  Felder (``SsoUpdateResult.changed_sensitive``: Anbieter-Ziel, Vertrauensanker, JIT, Gruppen-/Rollenzuordnung,
  lokale Anmeldung …), ist vorher ``verify_step_up`` Pflicht (Body-Feld ``step_up``; lokale Konten Passwort + ggf.
  TOTP, externe Konten Anmeldung hoechstens 10 min alt) [S8]. Danach geht eine Benachrichtigung an alle aktiven
  lokalen Admins (best effort, nach dem Commit, Fehler nur im Log). Audit ``SSO_SETTINGS_UPDATE`` (nie Werte von
  Secrets) mit ``step_up: "password"|"fresh_session"``.
- ``POST /settings/sso/test``: Formularwerte ueber die gespeicherten legen (nichts wird gespeichert); das gespeicherte
  Secret wird nur benutzt, wenn alle Zielfelder unveraendert sind (``guard_secret_retarget`` in
  ``sso_settings.build_test_config``, sonst 400) [S3]. Antwort immer 200 (``success``/``error``). Audit
  ``SSO_SETTINGS_TEST`` mit Ziel, Issuer bzw. Server, Ergebnis – ohne Werte, ohne Testpasswort.
  Ein LDAP-Test MIT Testpasswort ist eine Passwortpruefung gegen das Verzeichnis: Er laeuft durch die
  Login-Fehlversuchszaehler (IP und Testbenutzername, wie die Anmeldung [S2]); ein nicht bestaetigtes Passwort zaehlt
  als Fehlversuch, gesperrt -> 429 (WS-W3-NACHARBEIT, Review-Fund L-1: kein Passwort-Orakel, keine AD-Sperren durch
  beliebig viele Testversuche).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.auth import get_admin_session_user, verify_step_up
from app.core.client_ip import get_client_ip
from app.core.config import settings as app_settings
from app.core.database import DbRead, DbWrite
from app.core.login_rate_limit import is_login_rate_limited, record_failed_login
from app.core.timeutil import utcnow
from app.models.models import User
from app.schemas.sso import SsoSettingsUpdate, SsoTestRequest
from app.services import sso_ldap, sso_oidc, sso_settings
from app.services.audit import write_audit

logger = logging.getLogger(__name__)

ROUTER_ORDER = 102

router = APIRouter(prefix="/settings", tags=["Settings"])

SAVED_MESSAGE = "SSO-Einstellungen gespeichert"
TEST_FAILED_MESSAGE = "SSO-Test fehlgeschlagen"
TEST_RATE_LIMIT_DETAIL = "Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen."


def _settings_out(cfg: sso_settings.SsoConfig, *, linked: dict[str, int]) -> dict:
    """``settings_out`` plus ``general.session_max_age``: der SSO-Tab warnt bei mehr als 86400 s (Plan E-F10-2)."""
    out = sso_settings.settings_out(cfg, linked=linked)
    out["general"]["session_max_age"] = int(app_settings.AUTH_COOKIE_MAX_AGE)
    return out


@router.get("/sso")
async def get_sso_settings(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """SSO-Einstellungen (F10 3.3.1 + Plan-Felder); Secrets maskiert."""
    cfg = await sso_settings.load_sso_config(db)
    return _settings_out(cfg, linked=await sso_settings.count_linked_accounts(db))


@router.put("/sso")
async def update_sso_settings(
    data: SsoSettingsUpdate,
    request: Request,
    db: DbWrite,
    background_tasks: BackgroundTasks,
    admin: User = Depends(get_admin_session_user),
):
    """SSO-Einstellungen speichern; sensible Aenderungen nur nach Step-up [S8]."""
    # Erst alle Pruefungen ohne Schreiben (dry_run): so ist bekannt, ob sensible Felder betroffen sind.
    preview = await sso_settings.update(db, data, dry_run=True)
    step_up: Optional[str] = None
    if preview.changed_sensitive:
        step_up = await verify_step_up(db, admin, request, data.step_up)
    res = await sso_settings.update(db, data)
    if res.changed_sensitive and step_up is None:   # Stand hat sich zwischen Vorschau und Speichern geaendert
        step_up = await verify_step_up(db, admin, request, data.step_up)
    if res.changed:
        details = res.audit_details()
        if step_up is not None:
            details["step_up"] = step_up
        await write_audit(db, "SSO_SETTINGS_UPDATE", "system", "sso", user_id=admin.id, details=details)
    if res.changed_sensitive and res.changed:
        background_tasks.add_task(
            notify_local_admins, actor=admin.username, changed_keys=sorted(res.changed), when=utcnow(),
        )
    linked = await sso_settings.count_linked_accounts(db)
    return {
        "message": SAVED_MESSAGE,
        "settings": _settings_out(res.config, linked=linked),
        "warnings": res.warnings,
    }


def _test_target_details(req: SsoTestRequest, cfg: sso_settings.SsoConfig) -> dict:
    """Audit-Details des Tests ohne Werte (Ziel, Issuer bzw. Server, ungespeicherte Formularwerte)."""
    if req.target == "oidc":
        return {"target": "oidc", "issuer": (cfg.oidc.issuer or "")[:255] or None,
                "unsaved_values": req.oidc is not None}
    return {"target": "ldap", "servers": [u[:255] for u in cfg.ldap.server_urls][:5],
            "unsaved_values": req.ldap is not None, "test_user": bool((req.test_username or "").strip()),
            "password_checked": bool(req.test_password)}


def _password_confirmed(result: dict) -> bool:
    """Hat der LDAP-Test das Testpasswort bestaetigt (``details.user.password_ok is True``)?"""
    details = result.get("details") if isinstance(result, dict) else None
    user = details.get("user") if isinstance(details, dict) else None
    return bool(result.get("success")) and isinstance(user, dict) and user.get("password_ok") is True


@router.post("/sso/test")
async def test_sso_settings(
    req: SsoTestRequest,
    request: Request,
    db: DbWrite,
    admin: User = Depends(get_admin_session_user),
):
    """Verbindungstest mit den Formularwerten (F10 3.3.3); Antwort 200 mit ``success`` (429 bei gesperrtem Zaehler)."""
    # LDAP mit Testpasswort = Passwortpruefung: Login-Zaehler wie bei der Anmeldung (L-1)
    password_test = req.target == "ldap" and bool(req.test_password)
    test_name = (req.test_username or "").strip()
    client_ip = get_client_ip(request) or "unknown"
    if password_test and is_login_rate_limited(client_ip, test_name):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=TEST_RATE_LIMIT_DETAIL)
    # SecretReentryRequired (Ziel geaendert, Secret nicht neu eingegeben) -> 400 ueber den globalen Handler [S3]
    cfg = await sso_settings.build_test_config(db, req)
    try:
        if req.target == "oidc":
            result = await sso_oidc.test_configuration(cfg.oidc, sso_settings.redirect_uri(cfg))
        else:
            result = await sso_ldap.test_connection(cfg.ldap, req.test_username, req.test_password)
    except Exception as exc:  # noqa: BLE001 - kein Stacktrace an den Client
        logger.warning("SSO-Test (%s) fehlgeschlagen: %s", req.target, type(exc).__name__)
        result = {"success": False, "message": None, "error": f"Unerwarteter Fehler: {type(exc).__name__}",
                  "warnings": [], "details": {}}
    ok = bool(result.get("success"))
    if password_test and not _password_confirmed(result):
        # falsches Passwort, unbekannter Benutzer, Verzeichnis gestoert: zaehlt wie ein Fehlversuch [S2]
        record_failed_login(client_ip, test_name)
    details = _test_target_details(req, cfg)
    details["success"] = ok
    await write_audit(
        db, "SSO_SETTINGS_TEST", "system", "sso", user_id=admin.id, details=details,
        status="success" if ok else "error", error_message=None if ok else TEST_FAILED_MESSAGE,
    )
    return result


# ---------------------------------------------------------------------------------------------
# Benachrichtigung der lokalen Admins (best effort, Hintergrund nach dem Commit)
# ---------------------------------------------------------------------------------------------
def _mail_texts(app_name: str, actor: str, changed_keys: list[str], when: datetime) -> tuple[str, str, str]:
    stamp = when.strftime("%d.%m.%Y %H:%M")
    keys = ", ".join(changed_keys[:40]) + (" …" if len(changed_keys) > 40 else "")
    subject = f"{app_name}: SSO-Einstellungen geändert"
    text = (
        f"SSO-Einstellungen geändert von {actor} am {stamp} UTC.\n\n"
        f"Geänderte Einstellungen: {keys}\n\n"
        "War das nicht beabsichtigt, prüfe bitte sofort Einstellungen → Anmeldung / SSO und das Audit-Log.\n"
    )
    body_html = (
        f"<p>SSO-Einstellungen geändert von <strong>{html.escape(actor)}</strong> am {html.escape(stamp)} UTC.</p>"
        f"<p>Geänderte Einstellungen: {html.escape(keys)}</p>"
        "<p>War das nicht beabsichtigt, prüfe bitte sofort Einstellungen → Anmeldung / SSO und das Audit-Log.</p>"
    )
    return subject, body_html, text


async def notify_local_admins(*, actor: str, changed_keys: list[str], when: datetime) -> int:
    """Mail an alle aktiven lokalen Admins mit E-Mail-Adresse; Rueckgabe: Zahl der versendeten Mails.

    Eigene DB-Session (die Request-Session ist geschlossen). Ohne aktives SMTP passiert nichts; jeder Fehler wird
    nur geloggt – die Aenderung selbst ist zu diesem Zeitpunkt bereits gespeichert.
    """
    from app.core.database import async_session
    from app.services.email_service import get_smtp_settings, send_email
    from app.services.system_settings import get_setting

    sent = 0
    try:
        async with async_session() as s:
            smtp = await get_smtp_settings(s)
            if not smtp.get("enabled") or not smtp.get("host"):
                logger.info("SSO-Aenderung: keine Admin-Benachrichtigung (SMTP nicht eingerichtet)")
                return 0
            rows = (await s.execute(
                select(User.email).where(
                    User.role == "admin", User.is_active.is_(True), User.auth_source == "local",
                    User.email.is_not(None),
                )
            )).all()
            app_name = ((await get_setting(s, "app_name")) or app_settings.APP_NAME or "PDNS Manager").strip()
        subject, body_html, text = _mail_texts(app_name, actor, changed_keys, when)
        for (email,) in rows:
            if not (email or "").strip():
                continue
            try:
                await run_in_threadpool(send_email, smtp, email, subject, body_html, text)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSO-Aenderung: Benachrichtigung nicht versendet (%s)", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - Hintergrund-Task darf nie abstuerzen
        logger.warning("SSO-Aenderung: Admin-Benachrichtigung fehlgeschlagen (%s)", type(exc).__name__)
    return sent
