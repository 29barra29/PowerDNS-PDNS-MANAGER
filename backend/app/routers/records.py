"""API routes for DNS record management."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.schemas.dns import (
    RecordCreate, RecordDelete, BulkRecordUpdate, MessageResponse, RecordUpdate
)
from app.models.models import AuditLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records"])


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None
):
    """Helper to create audit log entries."""
    log = AuditLog(
        action=action,
        resource_type="record",
        resource_name=resource_name,
        server_name=server_name,
        details=details,
        status=status,
        error_message=error_message,
    )
    db.add(log)
    await db.flush()


@router.get("/{server_name}/{zone_id:path}")
async def list_records(server_name: str, zone_id: str):
    """List all records in a zone."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        zone = await client.get_zone(zone_id)
        rrsets = zone.get("rrsets", [])
        
        # Format records for easier consumption
        records = []
        for rrset in rrsets:
            for record in rrset.get("records", []):
                records.append({
                    "name": rrset.get("name"),
                    "type": rrset.get("type"),
                    "ttl": rrset.get("ttl"),
                    "content": record.get("content"),
                    "disabled": record.get("disabled", False),
                })
        
        return {
            "zone": zone_id,
            "server": server_name,
            "record_count": len(records),
            "records": records,
            "rrsets": rrsets,  # Also include raw rrsets
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def create_record(
    server_name: str,
    zone_id: str,
    record: RecordCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create or replace a record set in a zone. Appends to existing if same type/name."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        # Bestehende rrsets holen, um existierende Records nicht zu überschreiben
        # (z.B. wenn man einen zweiten TXT oder TLSA Eintrag für dieselbe Subdomain macht)
        zone = await client.get_zone(zone_id)
        existing_records = []
        for rr in zone.get("rrsets", []):
            if rr.get("name") == record.name and rr.get("type") == record.type:
                existing_records = rr.get("records", [])
                break
                
        # Neue Records mit den alten zusammenführen
        new_contents = [r.content for r in record.records]
        combined_records = list(existing_records)
        
        for r in record.records:
            # Nur hinzufügen, wenn der Inhalt nicht schon exakt so existiert
            if not any(ex.get("content") == r.content for ex in combined_records):
                combined_records.append({"content": r.content, "disabled": r.disabled})
        
        rrsets = [
            {
                "name": record.name,
                "type": record.type,
                "ttl": record.ttl,
                "changetype": "REPLACE",
                "records": combined_records,
            }
        ]
        
        await client.update_records(zone_id, rrsets)
        
        await _log_action(db, "CREATE", record.name, server_name, {
            "zone": zone_id,
            "type": record.type,
            "ttl": record.ttl,
            "records": [r["content"] for r in combined_records],
        })
        
        return MessageResponse(
            message=f"Record '{record.name}' ({record.type}) created/updated in zone '{zone_id}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "CREATE", record.name, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete("/{server_name}/{zone_id:path}/delete", response_model=MessageResponse)
async def delete_record(
    server_name: str,
    zone_id: str,
    record: RecordDelete,
    db: AsyncSession = Depends(get_db),
):
    """Delete a record set from a zone."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        await client.delete_record(zone_id, record.name, record.type)
        
        await _log_action(db, "DELETE", record.name, server_name, {
            "zone": zone_id,
            "type": record.type,
        })
        
        return MessageResponse(
            message=f"Record '{record.name}' ({record.type}) deleted from zone '{zone_id}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "DELETE", record.name, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_record(
    server_name: str,
    zone_id: str,
    update: RecordUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a specific record's content or TTL."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        zone = await client.get_zone(zone_id)
        existing_records = []
        for rr in zone.get("rrsets", []):
            if rr.get("name") == update.name and rr.get("type") == update.type:
                existing_records = rr.get("records", [])
                break
                
        # Find the record to update
        updated_records = []
        found = False
        for ex in existing_records:
            if ex.get("content") == update.old_content:
                updated_records.append({"content": update.new_content, "disabled": update.disabled})
                found = True
            else:
                updated_records.append({"content": ex.get("content"), "disabled": ex.get("disabled", False)})
                
        if update.type == "SOA" and existing_records and not found:
            # Für SOA überschreiben wir es einfach, da es nur einen geben sollte
            updated_records = [{"content": update.new_content, "disabled": update.disabled}]
            found = True

        if not found:
            raise HTTPException(status_code=404, detail=f"Original record content not found in {update.name}")

        rrsets = [
            {
                "name": update.name,
                "type": update.type,
                "ttl": update.ttl,
                "changetype": "REPLACE",
                "records": updated_records,
            }
        ]
        
        await client.update_records(zone_id, rrsets)
        
        await _log_action(db, "UPDATE", update.name, server_name, {
            "zone": zone_id,
            "type": update.type,
            "old": update.old_content,
            "new": update.new_content,
        })
        
        return MessageResponse(
            message=f"Record '{update.name}' ({update.type}) updated in zone '{zone_id}'"
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "UPDATE", update.name, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)



@router.post("/{server_name}/{zone_id:path}/bulk", response_model=MessageResponse)
async def bulk_update_records(
    server_name: str,
    zone_id: str,
    bulk: BulkRecordUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Perform bulk record operations (create and delete multiple records at once)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    rrsets = []
    
    # Create/Replace records
    for record in bulk.create:
        rrsets.append({
            "name": record.name,
            "type": record.type,
            "ttl": record.ttl,
            "changetype": "REPLACE",
            "records": [
                {"content": r.content, "disabled": r.disabled}
                for r in record.records
            ],
        })
    
    # Delete records
    for record in bulk.delete:
        rrsets.append({
            "name": record.name,
            "type": record.type,
            "changetype": "DELETE",
        })
    
    if not rrsets:
        raise HTTPException(status_code=400, detail="No records to process")
    
    try:
        await client.update_records(zone_id, rrsets)
        
        await _log_action(db, "BULK_UPDATE", zone_id, server_name, {
            "created": len(bulk.create),
            "deleted": len(bulk.delete),
        })
        
        return MessageResponse(
            message=f"Bulk update completed: {len(bulk.create)} created/updated, {len(bulk.delete)} deleted",
            details={
                "created": len(bulk.create),
                "deleted": len(bulk.delete),
            },
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "BULK_UPDATE", zone_id, server_name,
            status="error", error_message=e.detail,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)
