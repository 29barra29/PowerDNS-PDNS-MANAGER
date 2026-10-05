"""API routes for DNS record management.

Schreibende Endpunkte: Fan-out auf alle schreibbaren Server (``services/fanout.py``), Audit ueber
``write_audit`` (Commit vor der Antwort per ``DbWrite``), Webhook-Ereignis per ``await enqueue_event``
(Outbox, Bauplan B.8). Die Payload-Daten sind noch v1 (server/zone/name/type …); F7-BE ergaenzt
``changes``/``fanout`` nach F6 5.3.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, assert_zone_access
from app.core.database import DbRead, DbWrite
from app.models.models import AuditLog, User
from app.schemas.dns import (
    RecordCreate, RecordDelete, BulkRecordUpdate, MessageResponse, RecordUpdate
)
from app.services import fanout
from app.services.audit import write_audit
from app.services.pdns_client import pdns_manager, PowerDNSAPIError, RecordNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records"])

# Fan-out-Helfer liegen seit 3.0 in services/fanout.py (Bauplan B.5); die alten Namen bleiben als Aliase.
_writable_targets_for_zone = fanout.writable_targets_for_zone
_read_only_error = fanout.read_only_error
_summarize_results = fanout.summarize_results


def _zone_not_found_for(name: str, exc: PowerDNSAPIError) -> bool:
    """Alias mit der alten Signatur (``name`` wird nicht mehr gebraucht)."""
    return fanout.zone_not_found_for(exc)


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None,
    user_id: int = None, zone_name: Optional[str] = None,
) -> Optional[AuditLog]:
    """Audit-Eintrag (resource_type ``record``) ueber ``write_audit``.

    Erfolg: Eintrag in der Request-Session (Rueckgabe mit ``id`` fuer ``audit_log_id``); Fehler: eigene
    Session (``write_audit_detached``), weil die Request-Session bei der folgenden HTTPException
    zurueckgerollt wird – Rueckgabe dann ``None``.
    """
    return await write_audit(
        db, action, "record", resource_name, user_id=user_id, details=details, status=status,
        error_message=error_message, server_name=server_name, zone_name=zone_name,
    )


async def _assert_lua_allowed(db: AsyncSession, user: User, rtypes) -> None:
    """LUA-Records nur laut Policy ``lua_records_policy`` (Default: nur Admins, F15 3.1)."""
    if any((t or "").upper() == "LUA" for t in rtypes):
        from app.services.lua_records import assert_lua_write_allowed

        await assert_lua_write_allowed(db, user)


@router.get("/{server_name}/{zone_id:path}")
async def list_records(
    server_name: str,
    zone_id: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """List all records in a zone (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        zone = await client.get_zone(zone_id)
        rrsets = zone.get("rrsets", [])

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
            "rrsets": rrsets,
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


# WICHTIG: /bulk muss VOR der allgemeinen POST-Route stehen. Starlette nimmt den ersten
# passenden Treffer, und {zone_id:path} wuerde sonst auch ".../bulk" als Zonennamen schlucken.
@router.post("/{server_name}/{zone_id:path}/bulk", response_model=MessageResponse)
async def bulk_update_records(
    server_name: str,
    zone_id: str,
    bulk: BulkRecordUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Bulk record operations. Fans out to all writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    await _assert_lua_allowed(db, current_user, [r.type for r in bulk.create])

    rrsets = []
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
    value_deletes = []  # Einzelwerte (mit content) brauchen ein Lesen des RRsets je Server
    for record in bulk.delete:
        if record.content is not None:
            value_deletes.append(record)
            continue
        rrsets.append({
            "name": record.name,
            "type": record.type,
            "changetype": "DELETE",
        })

    if not rrsets and not value_deletes:
        raise HTTPException(status_code=400, detail="No records to process")

    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False

    for srv_name, client in targets:
        try:
            for vd in value_deletes:
                await client.delete_record(zone_id, vd.name, vd.type, content=vd.content)
            if rrsets:
                await client.update_records(zone_id, rrsets)
            results[srv_name] = "saved"
            if srv_name == server_name:
                primary_success = True
        except RecordNotFoundError as e:
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e
            continue
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    audit = await _log_action(
        db, "BULK_UPDATE", zone_id, server_name,
        {
            "created": len(bulk.create),
            "deleted": len(bulk.delete),
            "fanout": _summarize_results(results, info),
        },
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
        zone_name=zone_id,
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.bulk", actor=current_user, zone=zone_id, server=server_name,
        data={"server": server_name, "zone": zone_id, "created": len(bulk.create), "deleted": len(bulk.delete)},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=f"Bulk update completed: {len(bulk.create)} created/updated, {len(bulk.delete)} deleted",
        details={
            "created": len(bulk.create),
            "deleted": len(bulk.delete),
            "fanout": _summarize_results(results, info),
        },
    )


@router.post("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def create_record(
    server_name: str,
    zone_id: str,
    record: RecordCreate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Create or replace a record set in a zone.

    Writes are fanned out to every active server with ``allow_writes=True``
    that hosts this zone, so independent PowerDNS instances stay in sync.
    Servers without the zone are skipped silently. Read-only servers are
    refused before any write happens.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    await _assert_lua_allowed(db, current_user, [record.type])
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False
    final_records: list[dict] = []

    for srv_name, client in targets:
        try:
            zone = await client.get_zone(zone_id)
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e
            continue

        existing_records = []
        for rr in zone.get("rrsets", []):
            if rr.get("name") == record.name and rr.get("type") == record.type:
                existing_records = rr.get("records", [])
                break

        combined_records = list(existing_records)
        for r in record.records:
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

        try:
            await client.update_records(zone_id, rrsets)
            results[srv_name] = "saved"
            if srv_name == server_name:
                primary_success = True
                final_records = combined_records
        except PowerDNSAPIError as e:
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    audit = await _log_action(
        db, "CREATE", record.name, server_name,
        {
            "zone": zone_id,
            "type": record.type,
            "ttl": record.ttl,
            "records": [r["content"] for r in final_records],
            "fanout": _summarize_results(results, info),
        },
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
        zone_name=zone_id,
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.created", actor=current_user, zone=zone_id, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": record.name, "type": record.type},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=f"Record '{record.name}' ({record.type}) created/updated in zone '{zone_id}'",
        details=_summarize_results(results, info),
    )


@router.delete("/{server_name}/{zone_id:path}/delete", response_model=MessageResponse)
async def delete_record(
    server_name: str,
    zone_id: str,
    record: RecordDelete,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Delete a record set (or a single value, if ``content`` is set). Fans out to all writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False

    for srv_name, client in targets:
        try:
            await client.delete_record(zone_id, record.name, record.type, content=record.content)
            results[srv_name] = "deleted"
            if srv_name == server_name:
                primary_success = True
        except RecordNotFoundError as e:
            # Zone vorhanden, aber Wert/RRset nicht: auf Peers ueberspringen, auf dem
            # Primaer-Server als 404 an den Nutzer melden (nicht als "Zone fehlt").
            results[srv_name] = f"skipped ({e.detail})"
            if srv_name == server_name:
                primary_error = e
            continue
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    audit = await _log_action(
        db, "DELETE", record.name, server_name,
        {"zone": zone_id, "type": record.type, "content": record.content, "fanout": _summarize_results(results, info)},
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
        zone_name=zone_id,
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.deleted", actor=current_user, zone=zone_id, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": record.name, "type": record.type,
              "content": record.content},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=(
            f"Value '{record.content}' removed from record '{record.name}' ({record.type}) in zone '{zone_id}'"
            if record.content is not None
            else f"Record '{record.name}' ({record.type}) deleted from zone '{zone_id}'"
        ),
        details=_summarize_results(results, info),
    )


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_record(
    server_name: str,
    zone_id: str,
    update: RecordUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Update a specific record's content or TTL. Fans out to writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    await _assert_lua_allowed(db, current_user, [update.type])
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False
    primary_not_found = False

    for srv_name, client in targets:
        try:
            zone = await client.get_zone(zone_id)
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e
            continue

        existing_records = []
        for rr in zone.get("rrsets", []):
            if rr.get("name") == update.name and rr.get("type") == update.type:
                existing_records = rr.get("records", [])
                break

        updated_records = []
        found = False
        for ex in existing_records:
            if ex.get("content") == update.old_content:
                updated_records.append({"content": update.new_content, "disabled": update.disabled})
                found = True
            else:
                updated_records.append({"content": ex.get("content"), "disabled": ex.get("disabled", False)})

        if update.type == "SOA" and existing_records and not found:
            # SOA: nur ein Eintrag vorgesehen -> einfach ersetzen
            updated_records = [{"content": update.new_content, "disabled": update.disabled}]
            found = True

        if not found:
            results[srv_name] = "skipped (no matching content)"
            if srv_name == server_name:
                primary_not_found = True
            continue

        rrsets = [
            {
                "name": update.name,
                "type": update.type,
                "ttl": update.ttl,
                "changetype": "REPLACE",
                "records": updated_records,
            }
        ]

        try:
            await client.update_records(zone_id, rrsets)
            results[srv_name] = "saved"
            if srv_name == server_name:
                primary_success = True
        except PowerDNSAPIError as e:
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    audit = await _log_action(
        db, "UPDATE", update.name, server_name,
        {
            "zone": zone_id,
            "type": update.type,
            "old": update.old_content,
            "new": update.new_content,
            "fanout": _summarize_results(results, info),
        },
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
        zone_name=zone_id,
    )

    if not primary_success:
        if primary_not_found:
            raise HTTPException(status_code=404, detail=f"Original record content not found in {update.name}")
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.updated", actor=current_user, zone=zone_id, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": update.name, "type": update.type},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=f"Record '{update.name}' ({update.type}) updated in zone '{zone_id}'",
        details=_summarize_results(results, info),
    )
