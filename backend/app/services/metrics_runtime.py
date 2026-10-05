"""Laufzeitteil der Prometheus-Metriken (F13 5.5): Konfiguration, Bearer-Pruefung, Gauges beim Scrape.

- ``get_metrics_config``: Env-Token (``METRICS_TOKEN`` >= 24 Zeichen) hat Vorrang und
  aktiviert ``/metrics``; sonst Panel-Einstellungen (``metrics_enabled`` + verschluesselter
  ``metrics_token``). 10 s Cache; DB-Fehler -> letzter Stand bzw. nur Env bzw.
  ``ConfigUnavailable`` (Endpoint antwortet dann 503).
- ``refresh_runtime_gauges``: beim Scrape (kein Hintergrund-Task) Server-Anzahl (geladen + konfiguriert,
  aber nicht geladen), DB-Erreichbarkeit, offene Webhook-Zustellungen (``queued``/``failed``/``in_progress``),
  Laufzeitstatus (Migrationsfehler, Hintergrund-Aufgaben, unlesbare Geheimnisse), PowerDNS-Erreichbarkeit
  (60 s Cache; nicht geladene Server = 0) und Zonenanzahl (300 s Cache).

``services.system_settings`` (F5) wird lazy importiert; ``settings.get_metrics_env_token()``
liefert ``core/config.py`` (Welle 0b).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from app.core import metrics as prom
from app.core.config import settings
from app.services.pdns_client import pdns_manager

logger = logging.getLogger(__name__)

SETTING_KEYS = ("metrics_enabled", "metrics_token", "metrics_pdns_probe")
MIN_ENV_TOKEN_LENGTH = 24
PENDING_STATUSES = ("queued", "failed", "in_progress")

_CFG_TTL = 10.0
UP_TTL = 60.0
ZONES_TTL = 300.0
PROBE_TIMEOUT = 3.0
ZONES_TIMEOUT = 10.0
AUTH_FAIL_LOG_INTERVAL = 60.0
AUTH_FAIL_MAX_IPS = 1000


@dataclass(frozen=True)
class MetricsConfig:
    effective_enabled: bool
    token: Optional[str]  # Klartext (env oder entschluesselt aus DB)
    source: Optional[str]  # "env" | "db" | None
    db_enabled: bool  # metrics_enabled
    pdns_probe: bool


class ConfigUnavailable(Exception):
    """Metrik-Konfiguration nicht lesbar (DB weg, kein Cache, kein Env-Token)."""


_cfg_cache: Optional[tuple[float, MetricsConfig]] = None
_locks: dict[int, asyncio.Lock] = {}
_last_up_refresh = float("-inf")
_last_zones_refresh = float("-inf")
_up_state: dict[str, int] = {}
_auth_fail_log: dict[str, float] = {}
_unreadable_logged = False
_env_state_logged = False


def _now() -> float:
    return time.monotonic()


def _lock() -> asyncio.Lock:
    """Lock je Event-Loop (Tests wechseln die Loop; ein Lock darf nicht ueber Loops geteilt werden)."""
    loop = asyncio.get_running_loop()
    lk = _locks.get(id(loop))
    if lk is None:
        _locks.clear()
        lk = asyncio.Lock()
        _locks[id(loop)] = lk
    return lk


def _env_token() -> Optional[str]:
    """Gueltiger Env-Token (``core.config``: ``METRICS_TOKEN`` >= 24 Zeichen) oder None."""
    return settings.get_metrics_env_token()


def _raw_env_token() -> Optional[str]:
    return settings.METRICS_TOKEN


async def _read_db_settings() -> dict:
    """Liest die Metrik-Keys ueber den F5-Helfer (entschluesselt ``metrics_token``)."""
    from app.core.database import async_session
    from app.services.system_settings import get_settings

    async with async_session() as s:
        return await get_settings(s, list(SETTING_KEYS))


def _is_unreadable(value) -> bool:
    from app.core.secrets import is_unreadable

    return is_unreadable(value)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() == "true"


def invalidate_config_cache() -> None:
    global _cfg_cache
    _cfg_cache = None


async def get_metrics_config(force: bool = False) -> MetricsConfig:
    global _cfg_cache, _unreadable_logged
    env_tok = _env_token()
    now = _now()
    if not force and _cfg_cache is not None and now - _cfg_cache[0] < _CFG_TTL:
        return _cfg_cache[1]
    try:
        vals = await _read_db_settings()
    except Exception as exc:  # noqa: BLE001
        if _cfg_cache is not None:
            logger.debug("Metrik-Konfiguration: DB nicht lesbar (%s) – nutze letzten Stand", type(exc).__name__)
            return _cfg_cache[1]
        if env_tok:
            return MetricsConfig(True, env_tok, "env", False, True)
        raise ConfigUnavailable(str(exc) or type(exc).__name__) from exc

    db_enabled = _parse_bool(vals.get("metrics_enabled"), False)
    probe = _parse_bool(vals.get("metrics_pdns_probe"), True)
    token_raw = vals.get("metrics_token")
    token_db = (token_raw or "").strip() or None
    if env_tok:
        cfg = MetricsConfig(True, env_tok, "env", db_enabled, probe)
    elif db_enabled and token_db:
        cfg = MetricsConfig(True, token_db, "db", True, probe)
    else:
        cfg = MetricsConfig(False, None, None, db_enabled, probe)
        if db_enabled and token_raw is not None and token_db is None and _is_unreadable(token_raw):
            if not _unreadable_logged:
                _unreadable_logged = True
                logger.error("Metrik-Token nicht entschluesselbar – /metrics deaktiviert")
    _cfg_cache = (_now(), cfg)
    return cfg


def check_bearer(header: Optional[str], cfg: MetricsConfig) -> bool:
    """``Authorization: Bearer <token>`` gegen den konfigurierten Token (konstante Laufzeit)."""
    if not header or not cfg.token:
        return False
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), cfg.token.encode())


def log_auth_failure(ip: str) -> None:
    """Warnung bei abgelehntem Scrape – hoechstens 1x je 60 s und IP; Speicher auf 1000 IPs begrenzt."""
    now = _now()
    last = _auth_fail_log.get(ip)
    if last is not None and now - last < AUTH_FAIL_LOG_INTERVAL:
        return
    if last is None and len(_auth_fail_log) >= AUTH_FAIL_MAX_IPS:
        _auth_fail_log.pop(next(iter(_auth_fail_log)))
    _auth_fail_log[ip] = now
    logger.warning("Abgelehnter /metrics-Abruf von %s (fehlender oder falscher Token)", ip)


async def _db_probe() -> tuple[bool, Optional[int]]:
    """(DB erreichbar, offene Webhook-Zustellungen oder None, falls die Tabelle fehlt)."""
    from app.core.database import async_session

    try:
        async with async_session() as s:
            await s.execute(text("SELECT 1"))
            pending: Optional[int] = None
            try:
                stmt = text(
                    "SELECT COUNT(*) FROM webhook_deliveries WHERE status IN ("
                    + ", ".join(f"'{st}'" for st in PENDING_STATUSES) + ")"
                )
                pending = int((await s.execute(stmt)).scalar() or 0)
            except Exception as exc:  # noqa: BLE001 - Tabelle fehlt (vor F6) o. ae.
                logger.debug("Webhook-Queue nicht lesbar: %s", type(exc).__name__)
            return True, pending
    except Exception as exc:  # noqa: BLE001
        logger.debug("Datenbank fuer Metriken nicht erreichbar: %s", type(exc).__name__)
        return False, None


def _unloaded_servers(clients: dict) -> list[str]:
    """Konfigurierte, aber nicht geladene Server (z. B. API-Key nicht entschluesselbar)."""
    unloaded = getattr(pdns_manager, "unloaded", None) or {}
    return sorted(n for n in unloaded if n not in clients)


def _refresh_status_gauges() -> None:
    """Migrationsfehler, Hintergrund-Aufgaben und unlesbare Geheimnisse (prozesslokal, ohne I/O)."""
    from app.core import secrets as secret_store
    from app.core.database import MIGRATION_ERRORS
    from app.services import background

    prom.MIGRATION_ERRORS.set(len(MIGRATION_ERRORS))
    state = background.state()
    prom.BACKGROUND_TASK_RUNNING.clear()
    for name, info in (state.get("tasks") or {}).items():
        prom.BACKGROUND_TASK_RUNNING.labels(str(name)).set(1 if info.get("running") else 0)
    prom.SECRETS_UNREADABLE_READS.clear()
    for fld, count in sorted(secret_store.runtime_unreadable_counts().items()):
        prom.SECRETS_UNREADABLE_READS.labels(str(fld)).set(int(count or 0))


async def refresh_runtime_gauges(cfg: MetricsConfig) -> None:
    """Gauges beim Scrape aktualisieren (Caches siehe Modul-Docstring). Wirft nie."""
    global _last_up_refresh, _last_zones_refresh
    async with _lock():
        try:
            clients = dict(pdns_manager.get_all_clients())
            not_loaded = _unloaded_servers(clients)
            prom.PDNS_SERVERS.set(len(clients) + len(not_loaded))
            try:
                _refresh_status_gauges()
            except Exception as exc:  # noqa: BLE001 - Statuswerte sind optional
                logger.debug("Status-Gauges nicht aktualisiert: %s", type(exc).__name__)
            db_up, pending = await _db_probe()
            prom.DATABASE_UP.set(1 if db_up else 0)
            if pending is not None:
                prom.WEBHOOK_PENDING.set(pending)
            if not cfg.pdns_probe:
                return
            now = _now()
            if now - _last_up_refresh >= UP_TTL:
                names = list(clients)
                results = await asyncio.gather(
                    *(clients[n].get_server_info(timeout=PROBE_TIMEOUT) for n in names), return_exceptions=True
                )
                _up_state.clear()
                prom.PDNS_SERVER_UP.clear()
                for n, r in zip(names, results):
                    up = 0 if isinstance(r, BaseException) else 1
                    _up_state[n] = up
                    prom.PDNS_SERVER_UP.labels(n).set(up)
                for n in not_loaded:
                    _up_state[n] = 0
                    prom.PDNS_SERVER_UP.labels(n).set(0)
                _last_up_refresh = now
            if now - _last_zones_refresh >= ZONES_TTL:
                names = [n for n in clients if _up_state.get(n) == 1]
                results = await asyncio.gather(
                    *(clients[n].list_zones(timeout=ZONES_TIMEOUT) for n in names), return_exceptions=True
                )
                prom.PDNS_ZONES.clear()
                for n, r in zip(names, results):
                    if isinstance(r, BaseException) or not isinstance(r, list):
                        continue
                    prom.PDNS_ZONES.labels(n).set(len(r))
                _last_zones_refresh = now
        except Exception as exc:  # noqa: BLE001 - Scrape liefert dann die letzten Werte
            logger.warning("Metrik-Gauges konnten nicht aktualisiert werden: %s", exc)


def log_env_token_state() -> None:
    """Einmalige Warnung beim Start, wenn ``METRICS_TOKEN`` gesetzt, aber zu kurz ist."""
    global _env_state_logged
    if _env_state_logged:
        return
    raw = (_raw_env_token() or "").strip()
    if raw and len(raw) < MIN_ENV_TOKEN_LENGTH:
        _env_state_logged = True
        logger.warning(
            "METRICS_TOKEN ist kürzer als 24 Zeichen und wird ignoriert – /metrics bleibt über das Panel steuerbar."
        )


def reset_for_tests() -> None:
    global _cfg_cache, _last_up_refresh, _last_zones_refresh, _unreadable_logged, _env_state_logged
    _cfg_cache = None
    _last_up_refresh = float("-inf")
    _last_zones_refresh = float("-inf")
    _up_state.clear()
    _auth_fail_log.clear()
    _locks.clear()
    _unreadable_logged = False
    _env_state_logged = False
