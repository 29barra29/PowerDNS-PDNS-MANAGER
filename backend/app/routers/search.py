"""API-Routen fuer Suche und Audit-Log.

Suche (F8 5.1, F14 3.10): Die sichtbaren Zonen kommen ausschliesslich aus ``_allowed_zones_for`` (=
``core.auth.effective_zone_filter``: Benutzer-Zonenrechte geschnitten mit dem Token-Scope). Der ACL-Filter
laeuft VOR der Kappung auf ``max_results`` (sonst verdraengen fremde Treffer die eigenen), ohne Zonenrecht
gibt es keinen PowerDNS-Aufruf. Die serveruebergreifende Suche fragt alle Server parallel ab und gibt
keine internen Fehlertexte aus.
"""
import asyncio
import csv
import io
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.core.timeutil import iso_utc
from app.core.database import DbRead
from app.core.auth import get_current_user, get_admin_user, effective_zone_filter
from app.core.names import normalize_zone_name
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
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
@router.get("/audit-log", tags=["Audit"])
async def get_audit_log(
    db: DbRead,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str = Query(None, description="Filter by action (CREATE, UPDATE, DELETE, etc.)"),
    resource_type: str = Query(None, description="Filter by resource type (zone, record, dnssec_key)"),
    server_name: str = Query(None, description="Filter by server name"),
    admin: User = Depends(get_admin_user),
):
    """Get audit log entries (Admin only)."""
    query = select(AuditLog).order_by(desc(AuditLog.timestamp))

    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if server_name:
        query = query.where(AuditLog.server_name == server_name)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "count": len(logs),
        "offset": offset,
        "limit": limit,
        "entries": [
            {
                "id": log.id,
                "timestamp": iso_utc(log.timestamp),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_name": log.resource_name,
                "server_name": log.server_name,
                "user_id": log.user_id,
                "details": log.details,
                "status": log.status,
                "error_message": log.error_message,
            }
            for log in logs
        ],
    }


@router.get("/audit-log/export", tags=["Audit"])
async def export_audit_log_csv(
    db: DbRead,
    action: str = Query(None, description="Filter: CREATE, UPDATE, …"),
    resource_type: str = Query(None, description="zone, record, …"),
    server_name: str = Query(None),
    max_rows: int = Query(10_000, ge=1, le=50_000),
    admin: User = Depends(get_admin_user),
):
    """Audit-Log als UTF-8-CSV (Excel: Trennzeichen Semikolon). Nur Admin."""
    query = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if server_name:
        query = query.where(AuditLog.server_name == server_name)
    query = query.limit(max_rows)
    result = await db.execute(query)
    logs = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(
        [
            "id",
            "timestamp_utc",
            "action",
            "resource_type",
            "resource_name",
            "server_name",
            "user_id",
            "status",
            "error_message",
            "details_json",
        ]
    )
    for log in logs:
        det = log.details
        det_s = "" if det is None else (json.dumps(det, ensure_ascii=False) if isinstance(det, (dict, list)) else str(det))
        w.writerow(
            [
                log.id,
                (iso_utc(log.timestamp) or ""),
                log.action,
                log.resource_type,
                (log.resource_name or "") if log.resource_name is not None else "",
                (log.server_name or "") if log.server_name is not None else "",
                log.user_id or "",
                log.status,
                (log.error_message or "") if log.error_message is not None else "",
                det_s,
            ]
        )
    # BOM für Excel mit UTF-8
    out = "\ufeff" + buf.getvalue()
    return Response(
        content=out.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-log.csv"',
        },
    )
