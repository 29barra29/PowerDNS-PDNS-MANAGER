"""API routes for search and audit log."""
import csv
import io
import json
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.core.database import get_db
from app.core.auth import get_current_user, get_admin_user
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.models.models import AuditLog, User, UserZoneAccess

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Search & Audit"])


def _record_belongs_to_zone(record_zone: str, allowed_zones: set[str]) -> bool:
    """Heuristik: PowerDNS-Suchergebnis hat 'zone_id' bzw. 'zone'. Wir matchen normalisiert."""
    if not record_zone:
        return False
    z = record_zone.strip().lower()
    if not z.endswith("."):
        z += "."
    return z in allowed_zones


async def _allowed_zones_for(db: AsyncSession, user: User) -> set[str] | None:
    """Liefert die Menge erlaubter Zonen-Namen (mit Trailing Dot) – oder None für Admins (Vollzugriff)."""
    if user.role == "admin":
        return None
    result = await db.execute(
        select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == user.id)
    )
    return {row[0] for row in result.all()}


# ========================
# Search
# ========================
@router.get("/search/{server_name}", tags=["Search"])
async def search_records(
    server_name: str,
    q: str = Query(..., description="Search query", min_length=1, max_length=200),
    max_results: int = Query(100, ge=1, le=1000),
    object_type: str = Query("all", description="Filter: all, zone, record"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search for zones and records on a specific server (Auth, ACL-gefiltert)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        results = await client.search(q, max_results, object_type)
        allowed = await _allowed_zones_for(db, current_user)
        if allowed is not None:
            results = [
                r for r in results
                if _record_belongs_to_zone(r.get("zone_id") or r.get("zone") or r.get("name"), allowed)
            ]
        return {
            "server": server_name,
            "query": q,
            "count": len(results),
            "results": results,
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/search", tags=["Search"])
async def search_all_servers(
    q: str = Query(..., description="Search query", min_length=1, max_length=200),
    max_results: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search across all servers (Auth, ACL-gefiltert)."""
    all_results = {}
    allowed = await _allowed_zones_for(db, current_user)

    for name, client in pdns_manager.get_all_clients().items():
        try:
            results = await client.search(q, max_results)
            if allowed is not None:
                results = [
                    r for r in results
                    if _record_belongs_to_zone(r.get("zone_id") or r.get("zone") or r.get("name"), allowed)
                ]
            all_results[name] = {
                "count": len(results),
                "results": results,
            }
        except Exception as e:
            all_results[name] = {
                "count": 0,
                "results": [],
                "error": str(e),
            }

    return {
        "query": q,
        "servers": all_results,
    }


# ========================
# Audit Log – nur Admin
# ========================
@router.get("/audit-log", tags=["Audit"])
async def get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str = Query(None, description="Filter by action (CREATE, UPDATE, DELETE, etc.)"),
    resource_type: str = Query(None, description="Filter by resource type (zone, record, dnssec_key)"),
    server_name: str = Query(None, description="Filter by server name"),
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
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
    action: str = Query(None, description="Filter: CREATE, UPDATE, …"),
    resource_type: str = Query(None, description="zone, record, …"),
    server_name: str = Query(None),
    max_rows: int = Query(10_000, ge=1, le=50_000),
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
                log.timestamp.isoformat() if log.timestamp else "",
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
