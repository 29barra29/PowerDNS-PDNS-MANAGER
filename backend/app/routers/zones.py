"""API routes for zone management."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.schemas.dns import (
    ZoneCreate, ZoneUpdate, ZoneResponse, ZoneListResponse,
    ZoneImport, MessageResponse,
)
from app.models.models import AuditLog, User, UserZoneAccess

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zones", tags=["Zones"])


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None
):
    """Helper to create audit log entries."""
    log = AuditLog(
        action=action,
        resource_type="zone",
        resource_name=resource_name,
        server_name=server_name,
        details=details,
        status=status,
        error_message=error_message,
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
    
    # Für nicht-Admins: zugewiesene Zonen laden
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
            
            # Filter: Nicht-Admins sehen nur ihre Zonen
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
async def get_zone(server_name: str, zone_id: str):
    """Get a specific zone with all records."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        zone = await client.get_zone(zone_id)
        return zone
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("", response_model=MessageResponse)
async def create_zone(zone_data: ZoneCreate, db: AsyncSession = Depends(get_db)):
    """Create a new zone on one or more servers."""
    # Determine target servers
    target_servers = zone_data.servers if zone_data.servers else pdns_manager.list_servers()
    
    if not target_servers:
        raise HTTPException(
            status_code=400,
            detail="No PowerDNS servers configured. Set PDNS_SERVERS environment variable."
        )
    
    results = {}
    created = False
    zone_name = zone_data.name
    
    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            
            if not created:
                # Erstelle die Zone auf dem ersten erreichbaren Server
                payload = {
                    "name": zone_name,
                    "kind": zone_data.kind,
                    "nameservers": zone_data.nameservers,
                    "soa_edit_api": zone_data.soa_edit_api,
                }
                if zone_data.masters:
                    payload["masters"] = zone_data.masters
                
                result = await client.create_zone(payload)
                created = True
                
                # Fetch zone details to get current SOA and update it correctly
                try:
                    from datetime import datetime as dt
                    
                    zone_details = await client.get_zone(zone_name)
                    soa_rrset = next((rr for rr in zone_details.get("rrsets", []) if rr["type"] == "SOA"), None)
                    
                    if soa_rrset and len(zone_data.nameservers) > 0:
                        # Use first nameserver as primary NS in SOA
                        primary_ns = zone_data.nameservers[0]
                        if not primary_ns.endswith('.'):
                            primary_ns = primary_ns + '.'
                        
                        # Use second NS as rname if available, else hostmaster.zone.
                        if len(zone_data.nameservers) > 1:
                            rname = zone_data.nameservers[1]
                            if not rname.endswith('.'):
                                rname = rname + '.'
                        else:
                            clean_zone = zone_name.rstrip('.')
                            rname = f"hostmaster.{clean_zone}."
                        
                        # Generate a proper serial in YYYYMMDD01 format
                        # Don't use the auto-generated one since PowerDNS may set it to 0
                        serial = dt.now().strftime("%Y%m%d") + "01"
                        
                        # Build SOA: primary_ns rname serial refresh retry expire minimum
                        new_soa_content = f"{primary_ns} {rname} {serial} 10800 3600 604800 3600"
                        
                        await client.add_record(
                            zone_id=zone_name,
                            name=zone_name,
                            record_type="SOA",
                            content=[new_soa_content],
                            ttl=3600
                        )
                except Exception as e:
                    logger.warning(f"Failed to update default SOA for {zone_name}: {e}")

                # Enable DNSSEC if requested
                if zone_data.enable_dnssec:
                    try:
                        await client.enable_dnssec(zone_name)
                    except Exception as e:
                        logger.warning(f"DNSSEC enable failed for {zone_name} on {server_name}: {e}")
                
                results[server_name] = "created"
                await _log_action(db, "CREATE", zone_name, server_name, {
                    "kind": zone_data.kind,
                    "nameservers": zone_data.nameservers,
                    "dnssec": zone_data.enable_dnssec,
                })
            else:
                # Andere Server teilen die gleiche DB → Zone existiert bereits
                # Nur einen Rectify/Refresh ausführen
                try:
                    await client.rectify_zone(zone_name)
                    results[server_name] = "synced"
                    await _log_action(db, "CREATE", zone_name, server_name, {
                        "action": "synced (gemeinsame Datenbank)",
                    })
                except Exception:
                    # Rectify nicht unterstützt/fehlgeschlagen, Zone ist trotzdem da
                    results[server_name] = "synced"
                    await _log_action(db, "CREATE", zone_name, server_name, {
                        "action": "synced (gemeinsame Datenbank)",
                    })
            
        except PowerDNSAPIError as e:
            results[server_name] = f"error: {e.detail}"
            await _log_action(
                db, "CREATE", zone_name, server_name,
                status="error", error_message=e.detail,
            )
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
    db: AsyncSession = Depends(get_db),
):
    """Update zone metadata."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        update_data = zone_data.model_dump(exclude_none=True)
        await client.update_zone(zone_id, update_data)
        
        await _log_action(db, "UPDATE", zone_id, server_name, update_data)
        
        return MessageResponse(
            message=f"Zone '{zone_id}' updated successfully on '{server_name}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "UPDATE", zone_id, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def delete_zone(
    server_name: str,
    zone_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a zone from a server."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        await client.delete_zone(zone_id)
        
        await _log_action(db, "DELETE", zone_id, server_name)
        
        return MessageResponse(
            message=f"Zone '{zone_id}' deleted successfully from '{server_name}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "DELETE", zone_id, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/notify", response_model=MessageResponse)
async def notify_zone(server_name: str, zone_id: str):
    """Send NOTIFY to all slaves for a zone."""
    try:
        client = pdns_manager.get_client(server_name)
        await client.notify_zone(zone_id)
        return MessageResponse(message=f"NOTIFY sent for zone '{zone_id}' on '{server_name}'")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/{zone_id:path}/export")
async def export_zone(server_name: str, zone_id: str):
    """Export a zone in BIND/AXFR format."""
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
    db: AsyncSession = Depends(get_db),
):
    """Import a zone from BIND zonefile format.
    
    This creates the zone first, then parses and adds the records.
    """
    target_servers = pdns_manager.list_servers()
    if not target_servers:
        raise HTTPException(status_code=400, detail="No PowerDNS servers configured.")
    
    results = {}
    created = False
    
    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            
            if not created:
                # Import auf dem ersten Server
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
                })
            else:
                # Andere Server: Zone existiert bereits in der DB
                try:
                    await client.rectify_zone(import_data.name)
                except Exception:
                    pass
                results[server_name] = "synced"
                await _log_action(db, "IMPORT", import_data.name, server_name, {
                    "action": "synced (gemeinsame Datenbank)",
                })
            
        except PowerDNSAPIError as e:
            results[server_name] = f"error: {e.detail}"
            await _log_action(
                db, "IMPORT", import_data.name, server_name,
                status="error", error_message=e.detail,
            )
    
    return MessageResponse(
        message=f"Zone '{import_data.name}' import completed",
        details=results,
    )
