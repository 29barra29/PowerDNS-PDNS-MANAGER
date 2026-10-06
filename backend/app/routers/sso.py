"""SSO-Browser-Flows (F10 3.1, 5.7): Anbieterliste, OIDC-Start und -Callback, Konto-Verknuepfung mit OIDC/LDAP.

Eigener Router (Bauplan B.13, ``ROUTER_ORDER = 25``, direkt nach ``routers/auth.py``), Prefix ``/auth``:

- ``GET /auth/sso/providers`` (oeffentlich): Buttons und Schalter der Login-Seite, ohne Secrets und ohne Issuer.
- ``GET /auth/oidc/start`` (oeffentlich): State-Cookie setzen und zum Anbieter weiterleiten.
- ``GET /auth/oidc/callback`` (oeffentlich): State pruefen (konstantzeitig, einmalig), Code tauschen, ID-Token
  pruefen, Konto finden/anlegen bzw. verknuepfen, Session-Cookie oder 2FA-Zwischenschritt setzen. Jeder Fehler endet
  in einem Redirect auf ``/login?sso_error=<code>`` (bei Verknuepfung ``/settings?tab=integrations&sso_error=<code>``);
  vorher wird die Session zurueckgerollt (keine halben JIT-/Profil-Aenderungen) und ``LOGIN_FAILED`` bzw.
  ``USER_SSO_LINK`` (Status ``error``) losgeloest auditiert. Ist 2FA aktiv, das Geheimnis aber nicht lesbar
  (``totp_secret_state != ok``), gibt es keine Anmeldung (``sso_error=totp_unreadable``, Plan [S4]).
- ``POST /auth/me/sso/oidc/link`` und ``POST /auth/me/sso/ldap/link`` (nur Browser-Session): Selbst-Verknuepfung nach
  erneuter Eingabe von Passwort (+ TOTP). Fehlversuche zaehlen in den Login-Zaehlern je (IP, Benutzername); beim
  LDAP-Link zusaetzlich je (IP, Verzeichnis-Benutzername) – ein gesperrter Name loest keinen LDAP-Bind aus [S2].

OIDC ist kein Passwort-Raten: Callback-Fehler zaehlen nicht in den Login-Zaehlern, ein Erfolg loescht sie nicht.
Die oeffentlichen OIDC-Pfade haben aber eine eigene Drosselung (Review Welle 2): Fehlschlaege mit Aufwand (Audit,
Discovery, Token-Tausch) von Start und Callback zaehlen je IP (IPv6 /64); ab ``OIDC_FAIL_MAX`` im Fenster gibt es nur noch ``sso_error=rate_limited`` ohne Audit,
ohne Discovery und ohne Token-Tausch. Ist OIDC aus, endet der Callback sofort in ``disabled`` (kein Audit).
Fehler-Audits vor der State-Pruefung (ohne gueltiges State-Cookie) und Discovery-Fehler werden je (IP, Code)
hoechstens einmal in ``AUDIT_DEDUPE_TTL`` geschrieben; fehlgeschlagene Discovery wird ``DISCOVERY_NEGATIVE_TTL``
Sekunden negativ gecacht (kein ausgehender Request je Anfrage bei nicht erreichbarem IdP).
Nie geloggt oder auditiert: ``code``, ``state``, ``nonce``, Tokens, ``error_description``, Passwoerter.
"""
from __future__ import annotations

import hmac
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core import metrics as prom
from app.core.auth import (
    create_access_token,
    create_two_factor_pending_token,
    decode_oidc_state_token,
    get_session_user,
    password_version,
    totp_secret_state,
    totp_verify_once,
    verify_password,
    _consume_oidc_state,
)
from app.core.client_ip import get_client_ip
from app.core.database import DbRead, DbWrite
from app.core.login_rate_limit import is_login_rate_limited, record_failed_login
from app.core.rate_limit import SlidingWindowLimiter, ip_key
from app.models.models import User
from app.schemas.sso import LdapLinkRequest, SsoLinkStart
from app.services import sso_ldap, sso_oidc, sso_settings, user_guard
from app.services.audit import write_audit, write_audit_detached
from app.services.login_session import (
    OIDC_STATE_COOKIE,
    OIDC_STATE_COOKIE_PATH,
    TWO_FACTOR_COOKIE,
    TWO_FACTOR_COOKIE_PATH,
    complete_login,
    delete_transient_cookie,
    set_session_cookie,
    set_transient_cookie,
    transient_cookie_secure,
    user_to_dict,
)
from app.services.sso_oidc import OidcError
from app.services.sso_provisioning import ProvisioningError, link_external_identity, resolve_external_user

logger = logging.getLogger(__name__)

ROUTER_ORDER = 25

router = APIRouter(prefix="/auth", tags=["SSO"])

# Fehlercodes der Redirects (login.ssoError.<code> im Frontend)
SSO_ERROR_CODES = frozenset({
    "disabled", "config", "discovery", "state", "idp_error", "token", "id_token", "no_account", "not_allowed",
    "account_disabled", "link_conflict", "link_failed", "totp_unreadable", "internal", "rate_limited",
})
_SSO_DETAIL_RE = re.compile(r"[a-z_]{1,40}")
_MAX_CODE_LEN = 4096
LOGIN_ERROR_BASE = "/login?sso_error="
LINK_ERROR_BASE = "/settings?tab=integrations&sso_error="
LOGIN_SUCCESS_URL = "/"
SSO_2FA_URL = "/login?sso_2fa=1"
LINK_SUCCESS_URL = "/settings?tab=integrations&sso_linked=oidc"

# Texte der Verknuepfung (F10 3.1.4/3.1.5)
RATE_LIMIT_DETAIL = "Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen."
ALREADY_LINKED_DETAIL = "Dieses Konto ist bereits mit einem Anmeldedienst verknüpft"
OIDC_LINK_DISABLED_DETAIL = "Die Verknüpfung mit OIDC ist nicht aktiviert"
LDAP_LINK_DISABLED_DETAIL = "Die Verknüpfung mit LDAP ist nicht aktiviert"
CURRENT_PASSWORD_WRONG = "Aktuelles Passwort ist falsch"
TOTP_WRONG = "Falscher TOTP-Code"
TOTP_UNREADABLE_LINK = (
    "2FA-Geheimnis kann nicht entschlüsselt werden – die Verknüpfung ist erst möglich, nachdem ein Admin die 2FA "
    "zurückgesetzt hat."
)
IDP_UNREACHABLE_DETAIL = "Der Anmeldedienst ist nicht erreichbar – bitte später erneut versuchen"
LDAP_UNAVAILABLE_DETAIL = "Der Anmeldedienst (LDAP) ist nicht erreichbar. Bitte später erneut versuchen."
LDAP_CONFIG_DETAIL = "Die LDAP-Anmeldung ist fehlerhaft konfiguriert – bitte den Administrator informieren."
LDAP_LINK_BAD_CREDENTIALS = "LDAP-Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch"
LDAP_LINK_NOT_ALLOWED = "Dein Verzeichnis-Konto ist nicht für dieses Panel freigegeben"
LDAP_LINK_CONFLICT = "Dieses Verzeichnis-Konto ist bereits mit einem anderen Panel-Konto verknüpft"
LDAP_LINKED_MESSAGE = "Konto mit LDAP verknüpft"


# Drosselung der oeffentlichen OIDC-Fehlerpfade (Review Welle 2): Fehlschlaege je IP-Schluessel
OIDC_FAIL_MAX = 20
OIDC_FAIL_WINDOW_SEC = 300
AUDIT_DEDUPE_TTL = 300
DISCOVERY_NEGATIVE_TTL = 60
_MAX_TRACKED = 10_000
_fail_limiter = SlidingWindowLimiter(OIDC_FAIL_MAX, OIDC_FAIL_WINDOW_SEC, max_keys=20_000)
_audited_at: dict[tuple, float] = {}
_discovery_failed: dict[str, tuple[float, OidcError]] = {}


def reset_for_tests() -> None:
    """Drossel, Audit-Dedupe und Negativ-Cache leeren (nur fuer Tests)."""
    _fail_limiter.reset()
    _audited_at.clear()
    _discovery_failed.clear()


def _ip_bucket(client_ip: str) -> Optional[str]:
    """Schluessel der Drossel (IPv6 /64); unbekannte bzw. zu lange Angaben zaehlen nicht."""
    ip = (client_ip or "").strip()
    if not ip or ip == "unknown" or len(ip) > 64:
        return None
    return ip_key(ip)


def _is_throttled(ipk: Optional[str]) -> bool:
    return ipk is not None and _fail_limiter.is_limited(ipk)


def _note_failure(ipk: Optional[str]) -> None:
    if ipk is not None:
        _fail_limiter.hit(ipk)


def _first_in_window(key: tuple) -> bool:
    """True, wenn ``key`` in den letzten ``AUDIT_DEDUPE_TTL`` Sekunden noch nicht auditiert wurde (und merkt ihn)."""
    now = time.monotonic()
    last = _audited_at.get(key)
    if last is not None and now - last < AUDIT_DEDUPE_TTL:
        return False
    if len(_audited_at) >= _MAX_TRACKED:
        cutoff = now - AUDIT_DEDUPE_TTL
        for k in [k for k, ts in _audited_at.items() if ts < cutoff]:
            _audited_at.pop(k, None)
        while len(_audited_at) >= _MAX_TRACKED:
            _audited_at.pop(next(iter(_audited_at)))
    _audited_at[key] = now
    return True


async def _provider_metadata(issuer: str) -> dict:
    """Discovery mit kurzem Negativ-Cache: ein nicht erreichbarer IdP wird nicht bei jeder Anfrage erneut gefragt."""
    now = time.monotonic()
    cached = _discovery_failed.get(issuer)
    if cached is not None:
        if now < cached[0]:
            raise cached[1]
        _discovery_failed.pop(issuer, None)
    try:
        return await sso_oidc.get_provider_metadata(issuer)
    except OidcError as exc:
        if exc.code == "discovery":
            if len(_discovery_failed) >= 100:
                _discovery_failed.clear()
            _discovery_failed[issuer] = (now + DISCOVERY_NEGATIVE_TTL, exc)
        raise


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _clean_detail(value: Optional[str]) -> Optional[str]:
    """IdP-Fehlercode nur, wenn er harmlos ist (``[a-z_]{1,40}``) – nie ``error_description``."""
    if value and _SSO_DETAIL_RE.fullmatch(value):
        return value
    return None


def fail_redirect(code: str, *, intent: str = "login", detail: Optional[str] = None) -> RedirectResponse:
    """303 auf die Login- bzw. Einstellungsseite mit ``sso_error``; loescht das State-Cookie (F10 5.7)."""
    if code not in SSO_ERROR_CODES:
        code = "internal"
    base = LINK_ERROR_BASE if intent == "link" else LOGIN_ERROR_BASE
    detail = _clean_detail(detail)
    url = base + code + (f"&sso_detail={detail}" if detail else "")
    resp = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    delete_transient_cookie(resp, OIDC_STATE_COOKIE, path=OIDC_STATE_COOKIE_PATH)
    return _no_store(resp)


def _success_redirect(url: str) -> RedirectResponse:
    resp = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    delete_transient_cookie(resp, OIDC_STATE_COOKIE, path=OIDC_STATE_COOKIE_PATH)
    return _no_store(resp)


# ---------------------------------------------------------------------------------------------
# Oeffentlich: Anbieterliste und OIDC-Start
# ---------------------------------------------------------------------------------------------
@router.get("/sso/providers", include_in_schema=False)
async def sso_providers(db: DbRead):
    """Login-Seite: OIDC-Button (nur vollstaendig konfiguriert), LDAP-Hinweis, Verknuepfungs-Schalter.

    Keine Discovery (die Login-Seite wartet nicht auf den IdP), keine Secrets, kein Issuer.
    """
    cfg = await sso_settings.load_sso_config(db)
    return _no_store(JSONResponse(sso_settings.public_providers(cfg)))


@router.get("/oidc/start", include_in_schema=False)
async def oidc_start(request: Request, db: DbRead):
    """Startet die OIDC-Anmeldung: State-Cookie (10 min, HttpOnly, SameSite=Lax) + 302 zum Anbieter."""
    client_ip = get_client_ip(request) or "unknown"
    ipk = _ip_bucket(client_ip)
    if _is_throttled(ipk):
        return fail_redirect("rate_limited")
    cfg = await sso_settings.load_sso_config(db)
    if not cfg.oidc.enabled:
        return fail_redirect("disabled")
    if not sso_settings.oidc_ready(cfg):
        return fail_redirect("config")
    try:
        metadata = await _provider_metadata(cfg.oidc.issuer)
        url, cookie = sso_oidc.create_authorization_request(cfg, metadata, intent="login")
    except OidcError as exc:
        if exc.code != "discovery":
            logger.warning("OIDC-Start: Konfiguration unvollstaendig (%s)", exc.log_detail)
            return fail_redirect("config")
        logger.warning("OIDC-Start: Discovery fehlgeschlagen (%s)", exc.log_detail)
        _note_failure(ipk)
        if _first_in_window(("start", ipk or client_ip, "discovery")):
            prom.record_login("oidc", "failure")
            await write_audit_detached(
                "LOGIN_FAILED", "user", "oidc", status="error", error_message=exc.log_detail[:200] or None,
                details={"ip": client_ip, "method": "oidc", "reason": "discovery"},
            )
        return fail_redirect("discovery")
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    set_transient_cookie(resp, OIDC_STATE_COOKIE, cookie, path=OIDC_STATE_COOKIE_PATH,
                         max_age=sso_oidc.STATE_TTL_SECONDS, secure=transient_cookie_secure(cfg.base_url))
    return _no_store(resp)


# ---------------------------------------------------------------------------------------------
# Oeffentlich: OIDC-Callback (F10 3.1.3, 3.1.6, 5.7)
# ---------------------------------------------------------------------------------------------
class _CallbackFail(Exception):
    """Abbruch des Callbacks mit Redirect-Code; ``log`` landet (gekuerzt) als ``error_message`` im Audit.

    ``audit=False`` nur fuer Schritt 1 (Fehler meldet der Anbieter selbst, z. B. Abbruch durch den Nutzer).
    """

    def __init__(self, code: str, log: str = "", *, detail: Optional[str] = None, user_id: Optional[int] = None,
                 audit: bool = True):
        super().__init__(code)
        self.code = code
        self.log = log
        self.detail = detail
        self.user_id = user_id
        self.audit = audit


@dataclass
class _CallbackCtx:
    intent: str = "login"
    issuer: Optional[str] = None
    subject: Optional[str] = None
    username_hint: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    state_ok: bool = False   # State-Cookie und -Wert geprueft (Schritt 2)


async def _audit_callback_failure(ctx: _CallbackCtx, fail: _CallbackFail, client_ip: str) -> None:
    """Fehler-Audit (losgeloest, die Request-Session ist zurueckgerollt)."""
    error_message = (fail.log or fail.code)[:200]
    user_id = fail.user_id if fail.user_id is not None else ctx.user_id
    if ctx.intent == "link":
        await write_audit_detached(
            "USER_SSO_LINK", "user", (ctx.username or "oidc")[:255], user_id=user_id, status="error",
            error_message=error_message,
            details={"source": "oidc", "reason": fail.code, "external_issuer": ctx.issuer,
                     "external_id": (ctx.subject or "")[:64] or None},
        )
        return
    prom.record_login("oidc", "denied" if fail.code in ("no_account", "not_allowed", "account_disabled",
                                                        "totp_unreadable") else "failure")
    await write_audit_detached(
        "LOGIN_FAILED", "user", (ctx.username_hint or "oidc")[:255], user_id=user_id, status="error",
        error_message=error_message,
        details={"ip": client_ip, "method": "oidc", "reason": fail.code, "issuer": ctx.issuer,
                 "sub": (ctx.subject or "")[:64] or None},
    )


def _map_oidc_error(exc: OidcError) -> str:
    return exc.code if exc.code in ("config", "discovery", "token", "id_token") else "id_token"


@router.get("/oidc/callback", include_in_schema=False)
async def oidc_callback(
    request: Request,
    db: DbWrite,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    iss: Optional[str] = None,
):
    """Rueckkehr vom Anbieter. Erfolg: Session-Cookie + 303 ``/`` (bzw. 2FA-Schritt oder Einstellungen)."""
    client_ip = get_client_ip(request) or "unknown"
    ipk = _ip_bucket(client_ip)
    payload = decode_oidc_state_token(request.cookies.get(OIDC_STATE_COOKIE))
    ctx = _CallbackCtx(intent=payload["it"] if payload else "login",
                       issuer=payload.get("iss") if payload else None,
                       user_id=payload.get("uid") if payload else None)
    if _is_throttled(ipk):
        # Gedrosselt: kein Audit, keine Discovery, kein Token-Tausch, State bleibt unverbraucht
        return fail_redirect("rate_limited", intent=ctx.intent)
    try:
        return await _callback(request, db, ctx, payload, code=code, state=state, error=error, iss=iss)
    except _CallbackFail as fail:
        await _safe_rollback(db)
        if fail.audit:   # nur Fehlschlaege mit Aufwand zaehlen (OIDC aus/IdP-Abbruch kosten nichts)
            _note_failure(ipk)
            await _audit_callback_failure_throttled(ctx, fail, client_ip, ipk)
        return fail_redirect(fail.code, intent=ctx.intent, detail=fail.detail)
    except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Stacktrace im Browser-Flow
        logger.exception("OIDC-Callback fehlgeschlagen (%s)", type(exc).__name__)
        await _safe_rollback(db)
        _note_failure(ipk)
        await _audit_callback_failure_throttled(ctx, _CallbackFail("internal", type(exc).__name__), client_ip, ipk)
        return fail_redirect("internal", intent=ctx.intent)


async def _audit_callback_failure_throttled(ctx: _CallbackCtx, fail: _CallbackFail, client_ip: str,
                                            ipk: Optional[str]) -> None:
    """Vor der State-Pruefung (ohne gueltiges State-Cookie) kann jeder anonym Fehler ausloesen: dort hoechstens ein
    Audit je (IP, Code) und ``AUDIT_DEDUPE_TTL``. Nach gueltigem State wird jeder Fehler auditiert (die Drossel
    begrenzt die Menge)."""
    if not ctx.state_ok and not _first_in_window(("callback", ipk or client_ip, fail.code)):
        return
    await _audit_callback_failure(ctx, fail, client_ip)


async def _safe_rollback(db) -> None:
    try:
        await db.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rollback nach OIDC-Fehler fehlgeschlagen: %s", type(exc).__name__)


async def _callback(request: Request, db, ctx: _CallbackCtx, payload: Optional[dict], *, code: Optional[str],
                    state: Optional[str], error: Optional[str], iss: Optional[str]):
    # 0. OIDC aus (Standard): sofort Schluss, ohne Audit – sonst koennte jeder anonym Audit-Zeilen erzeugen
    cfg = await sso_settings.load_sso_config(db)
    if not cfg.oidc.enabled:
        raise _CallbackFail("disabled", "OIDC ist deaktiviert", audit=False)
    # 1. Fehler vom Anbieter (error_description wird nie gelesen)
    if error:
        raise _CallbackFail("idp_error", f"IdP-Fehler {_clean_detail(error) or '(unbekannt)'}",
                            detail=_clean_detail(error), audit=False)
    # 2. State: Cookie vorhanden, Wert passt (konstantzeitig), einmalig
    if payload is None or not state or len(state) > 512 \
            or not hmac.compare_digest(state.encode("utf-8"), str(payload["st"]).encode("utf-8")) \
            or not _consume_oidc_state(payload["st"]):
        raise _CallbackFail("state", "State fehlt, passt nicht oder wurde schon verwendet")
    ctx.state_ok = True
    # 3. RFC 9207: iss-Parameter muss zum erwarteten Issuer passen
    if iss is not None and iss != payload["iss"]:
        raise _CallbackFail("idp_error", "iss-Parameter passt nicht")
    # 4. Konfiguration und Anbieter
    if not sso_settings.oidc_ready(cfg):
        raise _CallbackFail("config", "OIDC-Konfiguration unvollstaendig")
    try:
        metadata = await _provider_metadata(cfg.oidc.issuer)
    except OidcError as exc:
        raise _CallbackFail("discovery", exc.log_detail) from None
    if payload["iss"] != metadata.get("issuer"):
        raise _CallbackFail("state", "Issuer seit dem Start geaendert")
    # 5. Code
    if not code or len(code) > _MAX_CODE_LEN:
        raise _CallbackFail("idp_error", "code fehlt")
    # 6. Token-Tausch, ID-Token, UserInfo
    try:
        profile = await sso_oidc.complete_authorization(cfg, metadata, code=code, state_payload=payload)
    except OidcError as exc:
        raise _CallbackFail(_map_oidc_error(exc), exc.log_detail) from None
    ctx.issuer, ctx.subject, ctx.username_hint = profile.issuer, profile.subject, profile.username_hint or None
    policy = sso_settings.policy_from(cfg.oidc, "oidc")
    if ctx.intent == "link":
        return await _complete_link(request, db, ctx, payload, cfg, profile, policy)
    # 7. Anmeldung
    try:
        res = await resolve_external_user(db, profile, policy)
    except ProvisioningError as pe:
        raise _CallbackFail(pe.code, f"Anmeldung abgelehnt ({pe.code})", user_id=pe.user_id) from None
    user = res.user
    ctx.user_id = user.id
    if cfg.general.require_totp and getattr(user, "totp_enabled", False):
        if totp_secret_state(user) != "ok":
            # [S4] UnreadableSecret ist "" -> ohne diese Pruefung waere der Login ohne 2FA moeglich
            raise _CallbackFail("totp_unreadable", "2FA-Geheimnis nicht lesbar", user_id=user.id)
        resp = _success_redirect(SSO_2FA_URL)
        set_transient_cookie(resp, TWO_FACTOR_COOKIE, create_two_factor_pending_token(user.id, method="oidc"),
                             path=TWO_FACTOR_COOKIE_PATH, max_age=300, secure=transient_cookie_secure(cfg.base_url))
        prom.record_login("oidc", "2fa_required")
        return resp   # JIT/Profil werden committet, LOGIN folgt im 2FA-Schritt
    return await complete_login(db, user, request, method="oidc", response=_success_redirect(LOGIN_SUCCESS_URL),
                                clear_fails=False, audit_extra=res.audit_extra)


async def _complete_link(request: Request, db, ctx: _CallbackCtx, payload: dict, cfg, profile, policy):
    """Abschluss einer OIDC-Verknuepfung (F10 3.1.6) – Beweis: Re-Auth beim Start + State-Cookie + unveraendertes pwv."""
    uid = payload.get("uid")
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none() if uid else None
    if user is not None:
        ctx.username = user.username
    if user is None or not user.is_active or not user_guard.is_local_account(user) \
            or payload.get("pwv") != password_version(user.hashed_password):
        raise _CallbackFail("link_failed", "Konto fehlt, ist nicht mehr lokal oder das Passwort wurde geaendert")
    if not sso_settings.public_providers(cfg)["linking"]["oidc"]:
        raise _CallbackFail("link_failed", "Verknuepfung mit OIDC ist nicht aktiviert")
    try:
        info = await link_external_identity(db, user, profile, policy)
    except ProvisioningError as pe:
        code = pe.code if pe.code in ("link_conflict", "not_allowed") else "link_failed"
        raise _CallbackFail(code, f"Verknuepfung abgelehnt ({pe.code})") from None
    except HTTPException as exc:
        raise _CallbackFail("link_failed", str(exc.detail)[:200]) from None
    await write_audit(
        db, "USER_SSO_LINK", "user", user.username, user_id=user.id,
        details={"source": "oidc", "external_issuer": profile.issuer, "external_id": profile.subject[:64],
                 "passkeys_removed": info["passkeys_removed"], "role_changed": info["role_changed"]},
    )
    return await complete_login(db, user, request, method="oidc", response=_success_redirect(LINK_SUCCESS_URL),
                                clear_fails=False, audit_extra={"linked": True})


# ---------------------------------------------------------------------------------------------
# Konto-Verknuepfung (nur Browser-Session)
# ---------------------------------------------------------------------------------------------
def _rate_limited() -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_DETAIL)


def _reauth_local(user: User, current_password: str, totp_code: Optional[str], client_ip: str) -> None:
    """Passwort (+ TOTP) des lokalen Kontos pruefen; Fehlversuche zaehlen je (IP, Benutzername) [S2]."""
    if not verify_password(current_password, user.hashed_password):
        record_failed_login(client_ip, user.username)
        raise HTTPException(status_code=400, detail=CURRENT_PASSWORD_WRONG)
    if getattr(user, "totp_enabled", False):
        if totp_secret_state(user) != "ok":
            raise HTTPException(status_code=400, detail=TOTP_UNREADABLE_LINK)
        if not totp_verify_once(user.id, (user.totp_secret or "").strip(), totp_code or ""):
            record_failed_login(client_ip, user.username)
            raise HTTPException(status_code=400, detail=TOTP_WRONG)


@router.post("/me/sso/oidc/link")
async def oidc_link_start(
    data: SsoLinkStart,
    request: Request,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Startet die Verknuepfung des eigenen (lokalen) Kontos mit OIDC: liefert die Autorisierungs-URL.

    Das State-Cookie bindet ``uid`` und den Passwort-Fingerabdruck (``pwv``); der Abschluss laeuft im Callback.
    """
    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip, current_user.username):
        raise _rate_limited()
    if not user_guard.is_local_account(current_user):
        raise HTTPException(status_code=400, detail=ALREADY_LINKED_DETAIL)
    cfg = await sso_settings.load_sso_config(db)
    if not sso_settings.public_providers(cfg)["linking"]["oidc"]:
        raise HTTPException(status_code=400, detail=OIDC_LINK_DISABLED_DETAIL)
    _reauth_local(current_user, data.current_password, data.totp_code, client_ip)
    await user_guard.assert_keeps_local_admin(db, current_user, removing=True)
    try:
        metadata = await _provider_metadata(cfg.oidc.issuer)
        url, cookie = sso_oidc.create_authorization_request(cfg, metadata, intent="link", user=current_user)
    except OidcError as exc:
        logger.warning("OIDC-Verknuepfung: Anbieter nicht erreichbar (%s)", exc.log_detail)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=IDP_UNREACHABLE_DETAIL) from None
    resp = JSONResponse({"authorization_url": url})
    set_transient_cookie(resp, OIDC_STATE_COOKIE, cookie, path=OIDC_STATE_COOKIE_PATH,
                         max_age=sso_oidc.STATE_TTL_SECONDS, secure=transient_cookie_secure(cfg.base_url))
    return _no_store(resp)


@router.post("/me/sso/ldap/link")
async def ldap_link(
    data: LdapLinkRequest,
    request: Request,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Verknuepft das eigene (lokale) Konto mit einem LDAP-Konto (Bind mit den Verzeichnis-Zugangsdaten).

    Erfolg: Zufallspasswort, Passkeys entfernt, neues Session-Cookie (alte Sessions sind ungueltig).
    """
    client_ip = get_client_ip(request) or "unknown"
    ldap_name = data.ldap_username.strip()
    # Beide Zaehler vor jeder Pruefung: Panel-Konto (Passwort/TOTP) und Verzeichnis-Name (LDAP-Bind) [S2]
    if is_login_rate_limited(client_ip, current_user.username) or is_login_rate_limited(client_ip, ldap_name):
        raise _rate_limited()
    if not user_guard.is_local_account(current_user):
        raise HTTPException(status_code=400, detail=ALREADY_LINKED_DETAIL)
    cfg = await sso_settings.load_sso_config(db)
    if not sso_settings.public_providers(cfg)["linking"]["ldap"]:
        raise HTTPException(status_code=400, detail=LDAP_LINK_DISABLED_DETAIL)
    _reauth_local(current_user, data.current_password, data.totp_code, client_ip)
    await user_guard.assert_keeps_local_admin(db, current_user, removing=True)
    try:
        ident = await sso_ldap.authenticate(cfg.ldap, ldap_name, data.ldap_password)
    except sso_ldap.LdapUnavailable:
        # [S2] zaehlt wie ein Fehlversuch: kein unbegrenztes Binden waehrend eines Ausfalls
        record_failed_login(client_ip, ldap_name)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=LDAP_UNAVAILABLE_DETAIL) from None
    except sso_ldap.LdapConfigError as exc:
        # [S2] LdapConfigError kann nach erfolgreichem Benutzer-Bind kommen (ID-Attribut fehlt): ohne Zaehler
        # waere 503 ein Passwort-Orakel fuer das Verzeichnis-Konto
        record_failed_login(client_ip, ldap_name)
        logger.error("LDAP-Konfigurationsfehler bei der Verknuepfung: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=LDAP_CONFIG_DETAIL) from None
    if ident is None:
        record_failed_login(client_ip, ldap_name)
        raise HTTPException(status_code=400, detail=LDAP_LINK_BAD_CREDENTIALS)
    try:
        info = await link_external_identity(db, current_user, ident.profile,
                                            sso_settings.policy_from(cfg.ldap, "ldap"))
    except ProvisioningError as pe:
        await write_audit(
            db, "USER_SSO_LINK", "user", current_user.username, user_id=current_user.id, status="error",
            error_message=pe.code, details={"source": "ldap", "reason": pe.code},
        )
        if pe.code == "link_conflict":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LDAP_LINK_CONFLICT) from None
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LDAP_LINK_NOT_ALLOWED) from None
    await write_audit(
        db, "USER_SSO_LINK", "user", current_user.username, user_id=current_user.id,
        details={"source": "ldap", "external_issuer": ident.profile.issuer,
                 "external_id": ident.profile.subject[:64], "passkeys_removed": info["passkeys_removed"],
                 "role_changed": info["role_changed"]},
    )
    token = create_access_token(data={"sub": str(current_user.id), "role": current_user.role}, user=current_user)
    resp = JSONResponse({"message": LDAP_LINKED_MESSAGE, "user": await user_to_dict(current_user, db)})
    return set_session_cookie(resp, token)
