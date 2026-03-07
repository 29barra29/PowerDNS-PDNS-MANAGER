"""API routes for search and audit log."""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.core.database import get_db
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.models.models import AuditLog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Search & Audit"])


# ========================
# Search
# ========================
@router.get("/search/{server_name}", tags=["Search"])
async def search_records(
    server_name: str,
    q: str = Query(..., description="Search query"),
    max_results: int = Query(100, ge=1, le=1000),
    object_type: str = Query("all", description="Filter: all, zone, record"),
):
    """Search for zones and records on a specific server."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        results = await client.search(q, max_results, object_type)
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
    q: str = Query(..., description="Search query"),
    max_results: int = Query(100, ge=1, le=1000),
):
    """Search for zones and records across all servers."""
    all_results = {}
    
    for name, client in pdns_manager.get_all_clients().items():
        try:
            results = await client.search(q, max_results)
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
# Audit Log
# ========================
@router.get("/audit-log", tags=["Audit"])
async def get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str = Query(None, description="Filter by action (CREATE, UPDATE, DELETE, etc.)"),
    resource_type: str = Query(None, description="Filter by resource type (zone, record, dnssec_key)"),
    server_name: str = Query(None, description="Filter by server name"),
    db: AsyncSession = Depends(get_db),
):
    """Get audit log entries with optional filtering."""
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
                "details": log.details,
                "status": log.status,
                "error_message": log.error_message,
            }
            for log in logs
        ],
    }
