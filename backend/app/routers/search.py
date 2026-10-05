"""API-Routen fuer Suche und Audit-Log.

Audit-Log (F7 3.6–3.10, nur Admin): Filter mit Gesamtzahl, Einzeleintrag, CSV-Export mit denselben Filtern
(alle Zellen ueber ``csv_safe`` gegen Formel-Injection [S13]) und die Aufbewahrungs-Einstellung (nur per
Browser-Session). Reihenfolge der Routen: Liste, Export, Settings GET/PUT, zuletzt ``/{entry_id:int}``.

Suche (F8 5.1, F14 3.10): Die sichtbaren Zonen kommen ausschliesslich aus ``_allowed_zones_for`` (=
``core.auth.effective_zone_filter``: Benutzer-Zonenrechte geschnitten mit dem Token-Scope). Der ACL-Filter
laeuft VOR der Kappung auf ``max_results`` (sonst verdraengen fremde Treffer die eigenen), ohne Zonenrecht
gibt es keinen PowerDNS-Aufruf. Die serveruebergreifende Suche fragt alle Server parallel ab und gibt
keine internen Fehlertexte aus.
"""
import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from app.core.config import settings
from app.core.timeutil import iso_utc
from app.core.database import DbRead, DbWrite
from app.core.auth import get_current_user, get_admin_user, get_admin_session_user, effective_zone_filter
from app.core.names import normalize_zone_name
from app.schemas.history import (
    AuditLogEntry, AuditLogListResponse, AuditSettingsResponse, AuditSettingsSaved, AuditSettingsUpdate,
)
from app.services import audit as audit_service
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.services.record_history import (
    build_audit_filters as _build_filters,
    load_reverted_by,
    load_usernames,
    serialize_audit_entry,
)
from app.models.models import AuditLog, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Search & Audit"])

SearchObjectType = Literal["all", "zone", "record", "comment"]
# Ueberabfrage fuer Nicht-Admins: der ACL-Filter laeuft vor der Kappung (F8 5.1).
_ACL_OVERFETCH_FACTOR = 10
_ACL_OVERFETCH_MIN = 1000
_ACL_OVERFETCH_MAX = 5000
SEARCH_SERVER_ERROR = "Suche auf diesem Server fehlgeschlagen"


def _record_belongs_to_zone(record_zone: str, allowed_zones: set[str]) -> bool:
    """PowerDNS-Suchergebnis hat 'zone_id' bzw. 'zone'. Vergleich normalisiert (lower + Trailing-Dot)."""
    if not record_zone:
        return False
    return normalize_zone_name(str(record_zone)) in allowed_zones


async def _allowed_zones_for(db: AsyncSession, user: User) -> set[str] | None:
    """Erlaubte Zonen (normalisiert) oder ``None`` = Vollzugriff (Admin ohne Token-Scope).

    Einzige Quelle fuer die Such-ACL; delegiert an ``effective_zone_filter`` (User-ACL ∩ Token-Scope).
    """
    return await effective_zone_filter(db, user)


async def _search_with_acl(
    client,
    q: str,
    max_results: int,
    object_type: str,
    allowed: set[str] | None,
) -> tuple[list[dict], bool]:
    """ACL-Filter VOR der Kappung. ``allowed=None`` -> Vollzugriff. Liefert (Treffer, truncated)."""
    if allowed is None:
        results = await client.search(q, max_results, object_type)
        return results, len(results) >= max_results
    if not allowed:
        return [], False  # ohne Zonenrecht kein PowerDNS-Aufruf
    fetch = min(max(max_results * _ACL_OVERFETCH_FACTOR, _ACL_OVERFETCH_MIN), _ACL_OVERFETCH_MAX)
    raw = await client.search(q, fetch, object_type)
    filtered = [
        r for r in raw
        if _record_belongs_to_zone(r.get("zone_id") or r.get("zone") or r.get("name"), allowed)
    ]
    truncated = len(filtered) > max_results or len(raw) >= fetch
    return filtered[:max_results], truncated


# ========================
# Search
# ========================
@router.get("/search/{server_name}", tags=["Search"])
async def search_records(
    server_name: str,
    db: DbRead,
    q: str = Query(..., description="Search query", min_length=1, max_length=200),
    max_results: int = Query(100, ge=1, le=1000),
    object_type: SearchObjectType = Query("all", description="Filter: all, zone, record, comment"),
    current_user: User = Depends(get_current_user),
):
    """Suche nach Zonen und Records auf einem Server (ACL-gefiltert vor der Kappung)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    allowed = await _allowed_zones_for(db, current_user)
    try:
        results, truncated = await _search_with_acl(client, q, max_results, object_type, allowed)
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "server": server_name,
        "query": q,
        "count": len(results),
        "truncated": truncated,
        "results": results,
    }


@router.get("/search", tags=["Search"])
async def search_all_servers(
    db: DbRead,
    q: str = Query(..., description="Search query", min_length=1, max_length=200),
    max_results: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
):
    """Suche ueber alle Server (parallel, ACL-gefiltert). Fehler je Server ohne interne Details."""
    allowed = await _allowed_zones_for(db, current_user)

    async def _one(name: str, client) -> tuple[str, dict]:
        try:
            results, truncated = await _search_with_acl(client, q, max_results, "all", allowed)
            return name, {"count": len(results), "truncated": truncated, "results": results}
        except Exception as e:  # noqa: BLE001 - Ursache nur ins Log (kann URL/Interna enthalten)
            logger.warning("Suche auf %s fehlgeschlagen: %s", name, e)
            return name, {"count": 0, "truncated": False, "results": [], "error": SEARCH_SERVER_ERROR}

    pairs = await asyncio.gather(*[_one(n, c) for n, c in pdns_manager.get_all_clients().items()])
    return {
        "query": q,
        "servers": dict(pairs),
    }


# ========================
# Audit Log – nur Admin
# ========================
AUDIT_LIST_MAX = 500
AUDIT_FULL_MAX = 100
CSV_HEADER = [
    "id", "timestamp_utc", "action", "resource_type", "resource_name", "server_name", "user_id", "status",
    "error_message", "details_json",
    # 3.0: am Ende angehaengt (bestehende Spalten bleiben in Reihenfolge und Namen)
    "zone_name", "username", "revert_of_id",
]
AuditStatus = Literal["success", "error"]


def build_audit_filters(
    *,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    server_name: Optional[str] = None,
    zone: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    q: Optional[str] = None,
) -> list:
    """Filter fuer Audit-Log-Liste und CSV-Export (``q`` auch ueber Aktion, Server und Zone)."""
    return _build_filters(
        zone=zone, action=action, resource_type=resource_type, server_name=server_name, user_id=user_id,
        status=status, date_from=date_from, date_to=date_to, q=q, q_admin_columns=True,
    )


@router.get("/audit-log", tags=["Audit"], response_model=AuditLogListResponse)
async def get_audit_log(
    db: DbRead,
    limit: int = Query(50, ge=1, le=AUDIT_LIST_MAX),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None, max_length=300, description="Aktion oder Komma-Liste (CREATE,UPDATE,…)"),
    resource_type: Optional[str] = Query(None, max_length=50, description="zone, record, dnssec_key, …"),
    server_name: Optional[str] = Query(None, max_length=100),
    zone: Optional[str] = Query(None, max_length=255, description="Zone (normalisiert)"),
    user_id: Optional[int] = Query(None),
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    full: bool = Query(False, description="ungekuerzte Details (limit max. 100)"),
    admin: User = Depends(get_admin_user),
):
    """Audit-Log mit Filtern und Gesamtzahl (Admin). Ohne ``full`` werden ``details.changes`` auf 20 gekuerzt."""
    if full:
        limit = min(limit, AUDIT_FULL_MAX)
    conds = build_audit_filters(
        action=action, resource_type=resource_type, server_name=server_name, zone=zone, user_id=user_id,
        status=status_filter, date_from=date_from, date_to=date_to, q=q,
    )
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conds))
    logs = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    usernames = await load_usernames(db, [log.user_id for log in logs])
    reverted = await load_reverted_by(db, [log.id for log in logs])
    return {
        "count": len(logs),
        "total": int(total or 0),
        "offset": offset,
        "limit": limit,
        "entries": [serialize_audit_entry(log, usernames, reverted, full=full) for log in logs],
    }


@router.get("/audit-log/export", tags=["Audit"])
async def export_audit_log_csv(
    db: DbRead,
    action: Optional[str] = Query(None, max_length=300, description="Filter: CREATE, UPDATE, … (Komma-Liste)"),
    resource_type: Optional[str] = Query(None, max_length=50, description="zone, record, …"),
    server_name: Optional[str] = Query(None, max_length=100),
    zone: Optional[str] = Query(None, max_length=255),
    user_id: Optional[int] = Query(None),
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    max_rows: int = Query(10_000, ge=1, le=50_000),
    admin: User = Depends(get_admin_user),
):
    """Audit-Log als UTF-8-CSV (Excel: Trennzeichen Semikolon, BOM). Nur Admin; Filter wie die Liste.

    Jede Zelle laeuft durch ``csv_safe`` (Werte mit ``= + - @ TAB CR`` am Anfang bekommen ein ``'``) [S13];
    ``details_json`` bleibt ungekuerzt.
    """
    conds = build_audit_filters(
        action=action, resource_type=resource_type, server_name=server_name, zone=zone, user_id=user_id,
        status=status_filter, date_from=date_from, date_to=date_to, q=q,
    )
    logs = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(max_rows)
    )).scalars().all()
    usernames = await load_usernames(db, [log.user_id for log in logs])

    safe = audit_service.csv_safe
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(CSV_HEADER)
    for log in logs:
        w.writerow([
            safe(log.id),
            safe(iso_utc(log.timestamp)),
            safe(log.action),
            safe(log.resource_type),
            safe(log.resource_name),
            safe(log.server_name),
            safe(log.user_id),
            safe(log.status),
            safe(log.error_message),
            safe(log.details),
            safe(getattr(log, "zone_name", None)),
            safe(usernames.get(log.user_id) if log.user_id is not None else None),
            safe(getattr(log, "revert_of_id", None)),
        ])
    # BOM für Excel mit UTF-8
    out = "\ufeff" + buf.getvalue()
    return Response(
        content=out.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-log.csv"',
        },
    )


async def _audit_settings(db: AsyncSession) -> dict:
    from app.services.system_settings import get_settings

    raw = await get_settings(db, [audit_service.LAST_PURGE_AT_KEY, audit_service.LAST_PURGE_DELETED_KEY])
    try:
        deleted = int(str(raw.get(audit_service.LAST_PURGE_DELETED_KEY)).strip())
    except (TypeError, ValueError):
        deleted = None
    total, oldest = (await db.execute(select(func.count(AuditLog.id), func.min(AuditLog.timestamp)))).one()
    return {
        "retention_days": await audit_service.get_retention_days(db),
        "last_purge_at": raw.get(audit_service.LAST_PURGE_AT_KEY) or None,
        "last_purge_deleted": deleted,
        "worker_enabled": bool(settings.BACKGROUND_WORKERS_ENABLED),
        "total_entries": int(total or 0),
        "oldest_entry_at": iso_utc(oldest),
    }


@router.get("/audit-log/settings", tags=["Audit"], response_model=AuditSettingsResponse)
async def get_audit_settings(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Aufbewahrung und Zustand der Bereinigung (Admin, nur Browser-Session)."""
    return await _audit_settings(db)


@router.put("/audit-log/settings", tags=["Audit"], response_model=AuditSettingsSaved)
async def update_audit_settings(
    body: AuditSettingsUpdate,
    db: DbWrite,
    admin: User = Depends(get_admin_session_user),
):
    """Aufbewahrung setzen (0 = unbegrenzt, sonst 7–3650 Tage). Geloescht wird beim naechsten Worker-Lauf."""
    from app.services.system_settings import set_setting

    old = await audit_service.get_retention_days(db)
    new = int(body.retention_days)
    await set_setting(db, audit_service.RETENTION_KEY, str(new))
    await audit_service.write_audit(
        db, "AUDIT_SETTINGS_UPDATE", "settings", "audit_retention", user_id=admin.id,
        details={"retention_days": {"from": old, "to": new}},
    )
    return {"message": "Aufbewahrung gespeichert", "settings": await _audit_settings(db)}


@router.get("/audit-log/{entry_id:int}", tags=["Audit"], response_model=AuditLogEntry)
async def get_audit_log_entry(
    entry_id: int,
    db: DbRead,
    admin: User = Depends(get_admin_user),
):
    """Ein Audit-Eintrag ungekuerzt (Admin)."""
    log = await db.get(AuditLog, entry_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Audit-Eintrag nicht gefunden")
    usernames = await load_usernames(db, [log.user_id])
    reverted = await load_reverted_by(db, [log.id])
    return serialize_audit_entry(log, usernames, reverted, full=True)
