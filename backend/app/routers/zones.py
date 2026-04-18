"""API routes for zone management."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user, get_admin_user, assert_zone_access
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.schemas.dns import (
    ZoneCreate, ZoneUpdate, ZoneResponse, ZoneListResponse,
    ZoneImport, MessageResponse,
)
from app.models.models import AuditLog, User, UserZoneAccess, ServerConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zones", tags=["Zones"])


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None,
    user_id: int = None,
):
    """Helper to create audit log entries (mit user_id)."""
    log = AuditLog(
        action=action,
        resource_type="zone",
        resource_name=resource_name,
        server_name=server_name,
        details=details,
        status=status,
        error_message=error_message,
        user_id=user_id,
    )
    db.add(log)
    await db.flush()


@router.get("/{server_name}", response_model=ZoneListResponse)
async def list_zones(
    server_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List zones on a server. Non-admin users only see their assigned zones."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    allowed_zones = None
    if current_user.role != "admin":
        result = await db.execute(
            select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == current_user.id)
        )
        allowed_zones = set(row[0] for row in result.all())

    try:
        zones_data = await client.list_zones()
        zones = []
        for z in zones_data:
            zone_name = z.get("name", "")

            if allowed_zones is not None and zone_name not in allowed_zones:
                continue

            zones.append(ZoneResponse(
                id=z.get("id", zone_name),
                name=zone_name,
                kind=z.get("kind", ""),
                serial=z.get("serial", 0),
                notified_serial=z.get("notified_serial"),
                dnssec=z.get("dnssec", False),
                account=z.get("account"),
                last_check=z.get("last_check"),
                masters=z.get("masters", []),
            ))
        return ZoneListResponse(server=server_name, zones=zones)
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/{zone_id:path}/detail")
async def get_zone(
    server_name: str,
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific zone with all records (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        zone = await client.get_zone(zone_id)
        return zone
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


async def _update_zone_soa_and_dnssec(client, server_name: str, zone_name: str, zone_data: ZoneCreate):
    """Update SOA and optionally enable DNSSEC after zone creation. Logs warnings on failure."""
    from datetime import datetime as dt
    try:
        zone_details = await client.get_zone(zone_name)
        soa_rrset = next((rr for rr in zone_details.get("rrsets", []) if rr["type"] == "SOA"), None)
        if soa_rrset and len(zone_data.nameservers) > 0:
            primary_ns = zone_data.nameservers[0]
            if not primary_ns.endswith('.'):
                primary_ns = primary_ns + '.'
            if len(zone_data.nameservers) > 1:
                rname = zone_data.nameservers[1]
                if not rname.endswith('.'):
                    rname = rname + '.'
            else:
                rname = f"hostmaster.{zone_name.rstrip('.')}."
            serial = dt.now().strftime("%Y%m%d") + "01"
            new_soa_content = f"{primary_ns} {rname} {serial} 10800 3600 604800 3600"
            await client.add_record(zone_id=zone_name, name=zone_name, record_type="SOA", content=[new_soa_content], ttl=3600)
    except Exception as e:
        logger.warning(f"Failed to update SOA for {zone_name} on {server_name}: {e}")
    if zone_data.enable_dnssec:
        try:
            await client.enable_dnssec(zone_name)
        except Exception as e:
            logger.warning(f"DNSSEC enable failed for {zone_name} on {server_name}: {e}")


def _allow_writes_column():
    """Spalte allow_writes kann bei alter DB fehlen."""
    return getattr(ServerConfig, "allow_writes", None)


@router.post("", response_model=MessageResponse)
async def create_zone(
    zone_data: ZoneCreate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new zone (Admin only). Only servers with allow_writes=True are used."""
    if zone_data.servers:
        target_servers = zone_data.servers
    else:
        col = _allow_writes_column()
        if col is not None:
            r = await db.execute(select(ServerConfig.name).where(ServerConfig.is_active == True, col == True))  # noqa: E712
            target_servers = [row[0] for row in r.all()]
        else:
            target_servers = pdns_manager.list_servers()
    if not target_servers:
        raise HTTPException(
            status_code=400,
            detail="Kein DNS-Server mit Schreibrechten. In Einstellungen → DNS-Server bei mindestens einem Server „Auf diesem Server speichern“ aktivieren."
        )
    results = {}
    zone_name = zone_data.name
    payload = {
        "name": zone_name,
        "kind": zone_data.kind,
        "nameservers": zone_data.nameservers,
        "soa_edit_api": zone_data.soa_edit_api,
    }
    if zone_data.masters:
        payload["masters"] = zone_data.masters

    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            await client.create_zone(payload)
            await _update_zone_soa_and_dnssec(client, server_name, zone_name, zone_data)
            results[server_name] = "created"
            await _log_action(db, "CREATE", zone_name, server_name, {
                "kind": zone_data.kind,
                "nameservers": zone_data.nameservers,
                "dnssec": zone_data.enable_dnssec,
            }, user_id=admin.id)
        except PowerDNSAPIError as e:
            if e.status_code == 409 or "already exists" in (e.detail or "").lower() or "Conflict" in (e.detail or ""):
                results[server_name] = "synced"
                await _log_action(db, "CREATE", zone_name, server_name,
                                  {"action": "synced (zone already present)"},
                                  user_id=admin.id)
            else:
                results[server_name] = f"error: {e.detail}"
                await _log_action(db, "CREATE", zone_name, server_name,
                                  status="error", error_message=e.detail,
                                  user_id=admin.id)
        except ValueError as e:
            results[server_name] = f"error: {str(e)}"

    return MessageResponse(
        message=f"Zone '{zone_name}' creation completed",
        details=results,
    )


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_zone(
    server_name: str,
    zone_id: str,
    zone_data: ZoneUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update zone metadata (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        update_data = zone_data.model_dump(exclude_none=True)
        await client.update_zone(zone_id, update_data)

        await _log_action(db, "UPDATE", zone_id, server_name, update_data, user_id=current_user.id)

        return MessageResponse(
            message=f"Zone '{zone_id}' updated successfully on '{server_name}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "UPDATE", zone_id, server_name,
            status="error", error_message=e.detail,
            user_id=current_user.id,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def delete_zone(
    server_name: str,
    zone_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a zone (Admin only)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        await client.delete_zone(zone_id)

        await _log_action(db, "DELETE", zone_id, server_name, user_id=admin.id)

        return MessageResponse(
            message=f"Zone '{zone_id}' deleted successfully from '{server_name}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "DELETE", zone_id, server_name,
            status="error", error_message=e.detail,
            user_id=admin.id,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/notify", response_model=MessageResponse)
async def notify_zone(
    server_name: str,
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send NOTIFY to all slaves for a zone (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
        await client.notify_zone(zone_id)
        return MessageResponse(message=f"NOTIFY sent for zone '{zone_id}' on '{server_name}'")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/{zone_id:path}/export")
async def export_zone(
    server_name: str,
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a zone in BIND/AXFR format (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
        zonefile = await client.get_zone_axfr(zone_id)
        return {
            "zone": zone_id,
            "server": server_name,
            "format": "bind",
            "content": zonefile,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/import", response_model=MessageResponse)
async def import_zone(
    import_data: ZoneImport,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a zone from BIND zonefile format (Admin only). Only servers with allow_writes=True are used."""
    col = _allow_writes_column()
    if col is not None:
        r = await db.execute(select(ServerConfig.name).where(ServerConfig.is_active == True, col == True))  # noqa: E712
        target_servers = [row[0] for row in r.all()]
    else:
        target_servers = pdns_manager.list_servers()
    if not target_servers:
        raise HTTPException(
            status_code=400,
            detail="Kein DNS-Server mit Schreibrechten. In Einstellungen → DNS-Server „Auf diesem Server speichern“ aktivieren."
        )

    results = {}
    created = False

    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)

            if not created:
                payload = {
                    "name": import_data.name,
                    "kind": import_data.kind,
                    "nameservers": import_data.nameservers,
                    "zone": import_data.content,
                    "soa_edit_api": "DEFAULT",
                }

                await client.create_zone(payload)
                created = True
                results[server_name] = "imported"

                await _log_action(db, "IMPORT", import_data.name, server_name, {
                    "kind": import_data.kind,
                    "content_length": len(import_data.content),
                }, user_id=admin.id)
            else:
                try:
                    await client.rectify_zone(import_data.name)
                except Exception:
                    pass
                results[server_name] = "synced"
                await _log_action(db, "IMPORT", import_data.name, server_name, {
                    "action": "synced (gemeinsame Datenbank)",
                }, user_id=admin.id)

        except PowerDNSAPIError as e:
            results[server_name] = f"error: {e.detail}"
            await _log_action(
                db, "IMPORT", import_data.name, server_name,
                status="error", error_message=e.detail,
                user_id=admin.id,
            )

    return MessageResponse(
        message=f"Zone '{import_data.name}' import completed",
        details=results,
    )
