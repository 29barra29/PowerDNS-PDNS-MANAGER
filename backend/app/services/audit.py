"""Zentraler Audit-Log-Helfer (Bauplan B.7, F7 5.3).

Regeln:
- Audit-Eintraege entstehen NUR ueber ``write_audit`` (Request-Session) bzw. ``write_audit_detached``
  (eigene Session, z. B. Fehler-Eintraege). Rohe ``AuditLog(...)``-Konstruktion ist verboten.
- ``write_audit`` schreibt innerhalb eines SAVEPOINTs (``begin_nested``): scheitert der Audit-Flush,
  wird nur der SAVEPOINT zurueckgerollt – die Session bleibt benutzbar, nachfolgende Schritte
  (``enqueue_event``, PTR-Sync, DynDNS-Status) gehen nicht verloren [D2].
- ``zone_name`` wird nur fuer zonenbezogene Typen gesetzt (``ZONE_SCOPED_RESOURCE_TYPES``) oder wenn der
  Aufrufer ihn ausdruecklich uebergibt – nie fuer Benutzer-/Settings-Eintraege (kein Leak in den Zonenverlauf).
- ``actor_username``/``client_ip`` kommen aus dem Request-Kontext (``core.request_context``, E-F7-1).
- ``details["auth"]`` ist reserviert (F14) und wird vom ``AuditLog``-Konstruktor gesetzt.

Ausserdem: ``csv_safe`` (Formel-Injection-Schutz fuer CSV-Exporte [S13]), ``public_details``
(Allowlist fuer Nicht-Admins [S12], Grundgeruest – F7-BE baut darauf auf), Aufbewahrung
(``get_retention_days``, ``purge_expired_audit_logs``) und der einmalige Backfill von
``audit_logs.zone_name`` fuer Alteintraege (F7 4.4).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import normalize_zone_name
from app.core.request_context import get_actor_username, get_client_ip_ctx
from app.core.timeutil import iso_utc, utcnow_naive
from app.models.models import AuditLog

logger = logging.getLogger(__name__)

# Ressourcentypen, deren Eintraege einer Zone zugeordnet werden (F7 5.2).
ZONE_SCOPED_RESOURCE_TYPES = frozenset({"record", "zone", "dnssec_key", "acme", "dyndns"})

# Aufbewahrung (F7 5.3, system_settings-Keys A.7)
RETENTION_KEY = "audit_retention_days"
LAST_PURGE_AT_KEY = "audit_last_purge_at"
LAST_PURGE_DELETED_KEY = "audit_last_purge_deleted"
BACKFILL_MARKER = "audit_zone_backfill_v1"
RETENTION_MIN_DAYS = 7
RETENTION_MAX_DAYS = 3650
PURGE_BATCH = 5000
BACKFILL_BATCH = 50_000


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def _zone_name_for(resource_type: str, details: Any, zone_name: Optional[str]) -> Optional[str]:
    if zone_name:
        return normalize_zone_name(zone_name)[:255] or None
    if resource_type in ZONE_SCOPED_RESOURCE_TYPES and isinstance(details, dict):
        zone = details.get("zone")
        if isinstance(zone, str):
            return normalize_zone_name(zone)[:255] or None
    return None


def _build(
    action: str,
    resource_type: str,
    resource_name: Optional[str],
    *,
    user_id: Optional[int],
    details: Optional[dict[str, Any]],
    status: str,
    error_message: Optional[str],
    server_name: Optional[str],
    zone_name: Optional[str],
    revert_of_id: Optional[int],
) -> AuditLog:
    actor = get_actor_username()
    ip = get_client_ip_ctx()
    return AuditLog(
        timestamp=utcnow_naive(),  # explizit: unabhaengig von der Zeitzone der Datenbank
        action=action,
        resource_type=resource_type,
        resource_name=(resource_name or "")[:255] or None,
        server_name=(server_name or "")[:100] or None,
        details=details,
        status=status,
        error_message=error_message,
        user_id=user_id,
        zone_name=_zone_name_for(resource_type, details, zone_name),
        revert_of_id=revert_of_id,
        actor_username=actor[:100] if actor else None,
        client_ip=ip[:64] if ip else None,
    )


async def write_audit_detached(
    action: str,
    resource_type: str,
    resource_name: Optional[str] = None,
    *,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    status: str = "error",
    error_message: Optional[str] = None,
    server_name: Optional[str] = None,
    zone_name: Optional[str] = None,
    revert_of_id: Optional[int] = None,
) -> Optional[int]:
    """Schreibt einen Audit-Eintrag in einer EIGENEN Session und committet sofort.

    Noetig fuer Fehler-Eintraege: die Request-Session wird bei HTTPException zurueckgerollt,
    der Eintrag ueber den Fehlversuch soll aber erhalten bleiben. Liefert die neue ID, bei einem
    Fehler ``None`` (geloggt, nie geworfen).
    """
    from app.core.database import async_session

    try:
        obj = _build(
            action, resource_type, resource_name, user_id=user_id, details=details, status=status,
            error_message=error_message, server_name=server_name, zone_name=zone_name,
            revert_of_id=revert_of_id,
        )
        async with async_session() as s:
            s.add(obj)
            await s.commit()
            return obj.id
    except Exception as exc:  # noqa: BLE001 - Audit darf den Request nicht zusaetzlich brechen
        logger.error("Audit-Eintrag (%s) konnte nicht geschrieben werden: %s", action, exc)
        return None


async def write_audit(
    db: AsyncSession,
    action: str,
    resource_type: str,
    resource_name: Optional[str] = None,
    *,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    status: str = "success",
    error_message: Optional[str] = None,
    server_name: Optional[str] = None,
    zone_name: Optional[str] = None,
    revert_of_id: Optional[int] = None,
) -> Optional[AuditLog]:
    """Schreibt einen Audit-Eintrag in die Request-Session (Commit macht ``DbWrite``/``get_db``).

    Erfolg: das geflushte Objekt (mit ``id``). Fehler-Eintraege (``status != "success"``) gehen in
    eine eigene Session (``write_audit_detached``), weil die Request-Session bei der folgenden
    HTTPException zurueckgerollt wird; Rueckgabe dann ``None``.

    Add + Flush laufen in ``begin_nested()``: ein Fehler rollt nur den SAVEPOINT zurueck, wird als
    ERROR geloggt und liefert ``None`` – die Session bleibt benutzbar [D2].
    """
    if status != "success":
        await write_audit_detached(
            action, resource_type, resource_name, user_id=user_id, details=details,
            status=status, error_message=error_message, server_name=server_name,
            zone_name=zone_name, revert_of_id=revert_of_id,
        )
        return None
    # Ausstehende Aenderungen des Aufrufers VOR dem SAVEPOINT flushen: Fehler dort gehoeren dem
    # Aufrufer und werden nicht als Audit-Fehler verschluckt (begin_nested wuerde sonst selbst flushen).
    await db.flush()
    try:
        obj = _build(
            action, resource_type, resource_name, user_id=user_id, details=details, status=status,
            error_message=error_message, server_name=server_name, zone_name=zone_name,
            revert_of_id=revert_of_id,
        )
        async with db.begin_nested():
            db.add(obj)
            await db.flush()
        return obj
    except Exception as exc:  # noqa: BLE001 - SAVEPOINT ist zurueckgerollt, Session bleibt nutzbar
        logger.error("Audit-Eintrag (%s) konnte nicht geschrieben werden: %s", action, exc)
        return None


# ---------------------------------------------------------------------------
# Ausgabe: CSV und Details fuer Nicht-Admins
# ---------------------------------------------------------------------------

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Zellwert fuer CSV-Exporte [S13]: Werte, die mit ``= + - @ TAB CR`` beginnen, bekommen ein ``'``
    vorangestellt (Tabellenprogramme werten sie sonst als Formel). Gilt fuer ALLE Spalten.

    ``None`` -> ``""``; ``dict``/``list`` -> JSON (UTF-8, wie der bisherige Export); Datum -> ISO-UTC;
    Zahlen und Bool bleiben unveraendert (koennen keine Formel tragen).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        s = iso_utc(value) or ""
    elif isinstance(value, date):
        s = value.isoformat()
    elif isinstance(value, (dict, list, tuple)):
        s = json.dumps(value, ensure_ascii=False)
    else:
        s = str(value)
    if s.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + s
    return s


# Allowlist fuer Nicht-Admins (B.7 [S12]); alles andere wird entfernt.
PUBLIC_DETAIL_KEYS = frozenset({
    "version", "zone", "changes", "change_count", "fanout", "after_source", "primary_outcome",
    "revert_of", "forced", "conflicts", "skipped", "source", "mode", "ptr", "auto_ptr", "token_name",
    "type", "ttl", "records", "old", "new", "content",
    # F14: Token-Kontext ohne Prefix (siehe _public_auth)
    "auth",
    # Kennzeichen, die nur die Vollstaendigkeit beschreiben
    "history_incomplete", "history_truncated", "change_keys",
})
# Schluessel, die auf keiner Ebene der erlaubten Werte erscheinen duerfen.
PRIVATE_NESTED_KEYS = frozenset({"client_ip", "ip", "ip_source", "token_prefix"})
PUBLIC_ERROR_TEXT = "PowerDNS-Fehler"
# Feste Status-Texte des Fan-outs (keine Rohtexte von PowerDNS)
_SAFE_ERROR_STATUSES = frozenset({"error: unklar – bitte Zone neu laden"})


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if k not in PRIVATE_NESTED_KEYS}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return value


def public_error_message(error_message: Optional[str], status_code: Optional[int] = None) -> Optional[str]:
    """Generischer Fehlertext fuer Nicht-Admins (keine PowerDNS-Rohtexte)."""
    if not error_message:
        return None
    if status_code:
        return f"{PUBLIC_ERROR_TEXT} (HTTP {status_code})"
    return PUBLIC_ERROR_TEXT


def _public_fanout(fanout: Any) -> Any:
    if not isinstance(fanout, dict):
        return fanout
    out = {}
    for server, status in fanout.items():
        if isinstance(status, str) and status.startswith("error") and status not in _SAFE_ERROR_STATUSES:
            out[server] = f"error: {PUBLIC_ERROR_TEXT}"
        else:
            out[server] = status
    return out


def _public_auth(auth: Any) -> Any:
    if not isinstance(auth, dict):
        return None
    return {k: auth[k] for k in ("via", "token_name") if k in auth}


def public_details(details: Any, *, admin: bool) -> Optional[dict]:
    """Details eines Audit-Eintrags fuer die Anzeige [S12].

    Admins sehen alles. Nicht-Admins nur die Allowlist ``PUBLIC_DETAIL_KEYS``; ``client_ip``, ``ip``,
    ``ip_source`` und ``token_prefix`` werden auf allen Ebenen entfernt (z. B. in ``ptr``), Fehlertexte
    im Fan-out durch "PowerDNS-Fehler" ersetzt, ``auth`` auf ``via``/``token_name`` reduziert.
    """
    if details is None:
        return None
    if admin:
        return details
    if not isinstance(details, dict):
        return None
    out: dict[str, Any] = {}
    for key in PUBLIC_DETAIL_KEYS:
        if key not in details:
            continue
        value = details[key]
        if key == "fanout":
            value = _public_fanout(value)
        elif key == "auth":
            value = _public_auth(value)
            if value is None:
                continue
        out[key] = _strip_private(value)
    return out


# ---------------------------------------------------------------------------
# Aufbewahrung (F7 5.3)
# ---------------------------------------------------------------------------


def parse_retention_days(raw: Optional[str]) -> int:
    """``"0"`` bzw. ``"7".."3650"`` -> Tage; ungueltig/fehlend -> 0 (unbegrenzt)."""
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    if days == 0 or RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS:
        return days
    return 0


async def get_retention_days(db: AsyncSession) -> int:
    from app.services.system_settings import get_setting

    return parse_retention_days(await get_setting(db, RETENTION_KEY))


async def purge_expired_audit_logs(*, now: Optional[datetime] = None) -> int:
    """Loescht Eintraege aelter als die Aufbewahrungsfrist (Batches à ``PURGE_BATCH``, je Batch Commit).

    Aufruf durch den Hintergrund-Worker (hoechstens stuendlich). Ausnahmen werden geloggt und
    weitergeworfen – der Worker faengt sie. Rueckgabe: Anzahl geloeschter Zeilen.
    """
    from app.core.database import async_session
    from app.services.system_settings import set_setting

    try:
        async with async_session() as s:
            days = await get_retention_days(s)
            if days <= 0:
                return 0
            cutoff = (now or utcnow_naive()) - timedelta(days=days)
            total = 0
            while True:
                res = await s.execute(
                    text("DELETE FROM audit_logs WHERE `timestamp` < :c ORDER BY `timestamp` LIMIT :n"),
                    {"c": cutoff, "n": PURGE_BATCH},
                )
                await s.commit()
                deleted = res.rowcount or 0
                total += deleted
                if deleted < PURGE_BATCH:
                    break
                await asyncio.sleep(0)
            await set_setting(s, LAST_PURGE_AT_KEY, iso_utc(utcnow_naive()))
            await set_setting(s, LAST_PURGE_DELETED_KEY, str(total))
            await s.commit()
    except Exception:
        logger.exception("Audit-Bereinigung fehlgeschlagen")
        raise
    if total:
        logger.info("Audit-Bereinigung: %d Eintraege aelter als %d Tage geloescht", total, days)
        await write_audit_detached(
            "AUDIT_PURGE", "audit_log", None, status="success",
            details={"deleted": total, "cutoff": iso_utc(cutoff), "retention_days": days},
        )
    return total


# ---------------------------------------------------------------------------
# Backfill audit_logs.zone_name (F7 4.4, einmalig, Marker audit_zone_backfill_v1)
# ---------------------------------------------------------------------------

# A: Records (ausser Bulk) und ACME ueber details.zone
_BACKFILL_FROM_DETAILS = (
    "UPDATE audit_logs "
    "SET zone_name = LEFT(LOWER(IF(RIGHT(TRIM(JSON_UNQUOTE(JSON_EXTRACT(details,'$.zone'))),1)='.', "
    "TRIM(JSON_UNQUOTE(JSON_EXTRACT(details,'$.zone'))), "
    "CONCAT(TRIM(JSON_UNQUOTE(JSON_EXTRACT(details,'$.zone'))),'.'))),255) "
    "WHERE zone_name IS NULL AND id BETWEEN :lo AND :hi "
    "AND resource_type IN ('record','acme') AND action <> 'BULK_UPDATE' "
    "AND JSON_TYPE(JSON_EXTRACT(details,'$.zone')) = 'STRING' "
    "AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(details,'$.zone'))) <> ''"
)
# B: Zonen, DNSSEC und Bulk ueber resource_name (dort steht die Zone)
_BACKFILL_FROM_RESOURCE = (
    "UPDATE audit_logs "
    "SET zone_name = LEFT(LOWER(IF(RIGHT(TRIM(resource_name),1)='.', TRIM(resource_name), "
    "CONCAT(TRIM(resource_name),'.'))),255) "
    "WHERE zone_name IS NULL AND id BETWEEN :lo AND :hi "
    "AND resource_name IS NOT NULL AND TRIM(resource_name) <> '' "
    "AND (resource_type IN ('zone','dnssec_key') OR (resource_type='record' AND action='BULK_UPDATE'))"
)


async def backfill_audit_zone_names() -> None:
    """Setzt ``zone_name`` fuer Bestandseintraege (vor 3.0) einmalig, in ID-Batches à 50 000.

    Marker ``system_settings.audit_zone_backfill_v1`` (ISO-UTC). Fehler: WARNING + ``MIGRATION_ERRORS``,
    kein Marker (Wiederholung beim naechsten Start), kein Startabbruch. Nur MySQL/MariaDB (JSON-Funktionen).
    """
    from app.core.database import MIGRATION_ERRORS, async_session

    started = time.monotonic()
    updated = 0
    try:
        async with async_session() as s:
            found = (await s.execute(
                text("SELECT 1 FROM system_settings WHERE `key` = :k"), {"k": BACKFILL_MARKER}
            )).first()
            if found:
                return
            lo, hi = (await s.execute(text("SELECT MIN(id), MAX(id) FROM audit_logs"))).one()
            if lo is not None:
                start = int(lo)
                while start <= int(hi):
                    end = start + BACKFILL_BATCH - 1
                    for stmt in (_BACKFILL_FROM_DETAILS, _BACKFILL_FROM_RESOURCE):
                        res = await s.execute(text(stmt), {"lo": start, "hi": end})
                        updated += res.rowcount or 0
                    await s.commit()
                    start = end + 1
            now = utcnow_naive()
            await s.execute(
                text(
                    "INSERT INTO system_settings (`key`, `value`, updated_at) VALUES (:k, :v, :u) "
                    "ON DUPLICATE KEY UPDATE `value` = `value`"
                ),
                {"k": BACKFILL_MARKER, "v": iso_utc(now), "u": now},
            )
            await s.commit()
    except Exception as exc:  # noqa: BLE001 - kein Startabbruch, naechster Start versucht es erneut
        msg = f"{type(getattr(exc, 'orig', None) or exc).__name__}: {getattr(exc, 'orig', None) or exc}"[:500]
        logger.warning("Backfill audit_logs.zone_name fehlgeschlagen (wird wiederholt): %s", msg)
        MIGRATION_ERRORS.append((f"backfill:{BACKFILL_MARKER}", msg))
        return
    logger.info("Backfill audit_logs.zone_name: %d Zeilen in %.1f s", updated, time.monotonic() - started)
