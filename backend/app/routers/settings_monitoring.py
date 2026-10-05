"""Admin-Einstellungen "Monitoring" (F12 3.2/3.3, F13 3.4-3.7, Bauplan B.13: ROUTER_ORDER 104, Prefix ``/settings``).

- ``GET/PUT /settings/propagation``: externe DNS-Abfragen des Propagations-Checks (Opt-in, Resolver-Liste).
- ``GET/PUT /settings/metrics``, ``POST/DELETE /settings/metrics/token``: Prometheus-Endpunkt ``/metrics`` und sein
  Scrape-Token (verschluesselt gespeichert, nur einmal im Klartext). ``METRICS_TOKEN`` in der Umgebung (>= 24
  Zeichen) sperrt Schalter und Token-Verwaltung (409).
- ``GET /settings/monitoring/status``: Kurzstatus fuer Admins (Hintergrund-Aufgaben, Migrationsfehler, nicht
  geladene Server, Geheimnisse) – ``/health`` liefert diese Details nur lokal [S14].

Alle Endpunkte verlangen eine Admin-Browser-Session (``get_admin_session_user``; Panel-Tokens sind gesperrt).
Schreibende Endpunkte nutzen ``DbWrite``; Audit ueber ``write_audit`` (nie mit Token-Klartext).
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_admin_session_user
from app.core.config import settings
from app.core.database import DbRead, DbWrite
from app.core.timeutil import iso_utc, utcnow
from app.models.models import User
from app.schemas.propagation import (
    MetricsSettingsIn,
    MetricsSettingsOut,
    MetricsTokenCreated,
    MonitoringStatus,
    PropagationSettingsIn,
    PropagationSettingsOut,
)
from app.services.audit import write_audit

ROUTER_ORDER = 104

router = APIRouter(prefix="/settings", tags=["Settings"])

METRICS_TOKEN_PREFIX = "dnsmgr_metrics_"
KEY_METRICS_ENABLED = "metrics_enabled"
KEY_METRICS_TOKEN = "metrics_token"
KEY_METRICS_HINT = "metrics_token_hint"
KEY_METRICS_CREATED = "metrics_token_created_at"
KEY_METRICS_PROBE = "metrics_pdns_probe"
METRICS_KEYS = (KEY_METRICS_ENABLED, KEY_METRICS_TOKEN, KEY_METRICS_HINT, KEY_METRICS_CREATED, KEY_METRICS_PROBE)

MSG_ENV_SETTINGS = "Metriken werden über die Umgebungsvariable METRICS_TOKEN gesteuert."
MSG_ENV_TOKEN = "METRICS_TOKEN ist gesetzt – der Token wird über die Umgebung verwaltet."
MSG_NEED_TOKEN = "Bitte zuerst einen Scrape-Token erzeugen."
MSG_NO_TOKEN = "Es ist kein Scrape-Token gespeichert."
MSG_TOKEN_DELETED = "Scrape-Token gelöscht, /metrics deaktiviert."
MSG_TOKEN_WARNING = "Dieser Token wird nur jetzt angezeigt."


# ---------------------------------------------------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------------------------------------------------
def _propagation_out(cfg) -> PropagationSettingsOut:
    from app.services import propagation as prop

    return PropagationSettingsOut(enabled=cfg.enabled, check_authoritative=cfg.check_authoritative, ipv6=cfg.ipv6,
                                  resolvers=list(cfg.resolvers), default_resolvers=list(prop.DEFAULT_RESOLVERS))


@router.get("/propagation", response_model=PropagationSettingsOut)
async def get_propagation_settings(db: DbRead, admin: User = Depends(get_admin_session_user)):
    from app.services import propagation as prop

    return _propagation_out(await prop.load_settings(db))


@router.put("/propagation", response_model=PropagationSettingsOut)
async def update_propagation_settings(
    body: PropagationSettingsIn, db: DbWrite, admin: User = Depends(get_admin_session_user),
):
    from app.services import propagation as prop

    try:
        changed = await prop.save_settings(db, enabled=body.enabled, check_authoritative=body.check_authoritative,
                                           ipv6=body.ipv6, resolvers=body.resolvers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if changed:
        await write_audit(db, "PROPAGATION_SETTINGS_UPDATE", "settings", "propagation", user_id=admin.id,
                          details={"changed": changed})
    prop.invalidate_caches()
    return _propagation_out(await prop.load_settings(db))


# ---------------------------------------------------------------------------------------------------------------------
# Metriken
# ---------------------------------------------------------------------------------------------------------------------
def _env_override() -> bool:
    return settings.get_metrics_env_token() is not None


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() == "true"


async def _public_base_url(db) -> Optional[str]:
    """Oeffentliche Basis-URL (``app_base_url``, sonst ``WEBAUTHN_ORIGIN``) – nur http(s), ohne Slash am Ende."""
    from app.services.system_settings import get_setting

    for cand in ((await get_setting(db, "app_base_url")) or "", settings.WEBAUTHN_ORIGIN or ""):
        c = str(cand).strip()
        if c.lower().startswith(("http://", "https://")):
            return c.rstrip("/")
    return None


async def _metrics_values(db) -> dict:
    from app.services.system_settings import get_settings

    return await get_settings(db, list(METRICS_KEYS))


def _token_state(vals: dict) -> tuple[bool, bool]:
    """(Token gespeichert und lesbar, Token gespeichert aber unlesbar)."""
    from app.core import secrets as secret_store

    raw = vals.get(KEY_METRICS_TOKEN)
    if raw is None:
        return False, False
    if secret_store.is_unreadable(raw):
        return False, True
    return bool(str(raw).strip()), False


async def _metrics_out(db) -> MetricsSettingsOut:
    vals = await _metrics_values(db)
    token_set, unreadable = _token_state(vals)
    enabled = _bool(vals.get(KEY_METRICS_ENABLED), False)
    env = _env_override()
    base = await _public_base_url(db)
    return MetricsSettingsOut(
        enabled=enabled, effective_enabled=env or (enabled and token_set), env_override=env, token_set=token_set,
        token_unreadable=unreadable,
        token_hint=(vals.get(KEY_METRICS_HINT) or None) if (token_set or unreadable) else None,
        token_created_at=(vals.get(KEY_METRICS_CREATED) or None) if (token_set or unreadable) else None,
        pdns_probe=_bool(vals.get(KEY_METRICS_PROBE), True), endpoint_path="/metrics",
        scrape_url=f"{base}/metrics" if base else None,
    )


def _invalidate_metrics() -> None:
    from app.services import metrics_runtime

    metrics_runtime.invalidate_config_cache()


@router.get("/metrics", response_model=MetricsSettingsOut)
async def get_metrics_settings(db: DbRead, admin: User = Depends(get_admin_session_user)):
    return await _metrics_out(db)


@router.put("/metrics", response_model=MetricsSettingsOut)
async def update_metrics_settings(body: MetricsSettingsIn, db: DbWrite,
                                  admin: User = Depends(get_admin_session_user)):
    from app.services.system_settings import set_settings

    vals = await _metrics_values(db)
    token_set, _unreadable = _token_state(vals)
    if body.enabled is not None and _env_override():
        raise HTTPException(409, MSG_ENV_SETTINGS)
    if body.enabled is True and not token_set:
        raise HTTPException(409, MSG_NEED_TOKEN)
    changed: dict[str, dict] = {}
    writes: dict[str, object] = {}
    old_enabled = _bool(vals.get(KEY_METRICS_ENABLED), False)
    old_probe = _bool(vals.get(KEY_METRICS_PROBE), True)
    if body.enabled is not None and body.enabled != old_enabled:
        changed["enabled"] = {"from": old_enabled, "to": body.enabled}
        writes[KEY_METRICS_ENABLED] = body.enabled
    if body.pdns_probe is not None and body.pdns_probe != old_probe:
        changed["pdns_probe"] = {"from": old_probe, "to": body.pdns_probe}
        writes[KEY_METRICS_PROBE] = body.pdns_probe
    if writes:
        await set_settings(db, writes)
        await write_audit(db, "METRICS_SETTINGS_UPDATE", "settings", "metrics", user_id=admin.id,
                          details={"changed": changed})
    _invalidate_metrics()
    return await _metrics_out(db)


@router.post("/metrics/token", status_code=201, response_model=MetricsTokenCreated)
async def create_metrics_token(db: DbWrite, admin: User = Depends(get_admin_session_user)):
    from app.services.system_settings import get_setting, set_settings

    if _env_override():
        raise HTTPException(409, MSG_ENV_TOKEN)
    rotated = (await get_setting(db, KEY_METRICS_TOKEN)) is not None
    token = METRICS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    hint = token[:19] + "…"
    created_at = iso_utc(utcnow())
    await set_settings(db, {KEY_METRICS_TOKEN: token, KEY_METRICS_HINT: hint, KEY_METRICS_CREATED: created_at})
    await write_audit(db, "METRICS_TOKEN_CREATE", "settings", "metrics", user_id=admin.id,
                      details={"hint": hint, "rotated": rotated})
    _invalidate_metrics()
    return MetricsTokenCreated(token=token, token_hint=hint, token_created_at=created_at, warning=MSG_TOKEN_WARNING)


@router.delete("/metrics/token")
async def delete_metrics_token(db: DbWrite, admin: User = Depends(get_admin_session_user)):
    from app.services.system_settings import delete_setting, get_setting, set_setting

    if _env_override():
        raise HTTPException(409, MSG_ENV_TOKEN)
    if (await get_setting(db, KEY_METRICS_TOKEN)) is None:
        raise HTTPException(404, MSG_NO_TOKEN)
    old_hint = await get_setting(db, KEY_METRICS_HINT)
    for key in (KEY_METRICS_TOKEN, KEY_METRICS_HINT, KEY_METRICS_CREATED):
        await delete_setting(db, key)
    await set_setting(db, KEY_METRICS_ENABLED, False)
    await write_audit(db, "METRICS_TOKEN_DELETE", "settings", "metrics", user_id=admin.id,
                      details={"hint": old_hint})
    _invalidate_metrics()
    return {"message": MSG_TOKEN_DELETED}


# ---------------------------------------------------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------------------------------------------------
@router.get("/monitoring/status", response_model=MonitoringStatus)
async def monitoring_status(admin: User = Depends(get_admin_session_user)):
    """Kurzstatus der Laufzeit (ohne DB-Zugriff): Worker, Migrationsfehler, nicht geladene Server, Geheimnisse."""
    from app.core import secrets as secret_store
    from app.core.database import MIGRATION_ERRORS
    from app.services import background
    from app.services.pdns_client import pdns_manager

    bg = background.state()
    report = secret_store.startup_report()
    runtime_unreadable = sum(int(v or 0) for v in secret_store.runtime_unreadable_counts().values())
    sec = {
        "mode": secret_store.current_mode(),
        "fallback_reason": getattr(report, "fallback_reason", None),
        "key_source": getattr(report, "key_source", None),
        "unreadable_values": len(getattr(report, "unreadable", None) or []),
        "runtime_unreadable": runtime_unreadable,
    }
    not_loaded = dict(getattr(pdns_manager, "unloaded", {}) or {})
    workers_ok = (not bg.get("enabled")) or all(t.get("running") for t in (bg.get("tasks") or {}).values())
    ok = (not MIGRATION_ERRORS and not not_loaded and sec["mode"] == "encrypted"
          and not sec["unreadable_values"] and not runtime_unreadable and workers_ok)
    return MonitoringStatus(
        checked_at=iso_utc(utcnow()), ok=ok, version=settings.APP_VERSION, background=bg,
        migration_errors=len(MIGRATION_ERRORS), servers_not_loaded=not_loaded, secrets=sec,
    )
