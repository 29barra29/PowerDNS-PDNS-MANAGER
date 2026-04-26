"""API routes for DNS record management."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user, assert_zone_access
from app.services.pdns_client import pdns_manager, PowerDNSAPIError, PowerDNSClient
from app.schemas.dns import (
    RecordCreate, RecordDelete, BulkRecordUpdate, MessageResponse, RecordUpdate
)
from app.models.models import AuditLog, User, ServerConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records"])


async def _writable_targets_for_zone(
    db: AsyncSession, zone_id: str, primary: str
) -> tuple[list[tuple[str, PowerDNSClient]], dict[str, str]]:
    """Determine which servers should receive a write for ``zone_id``.

    Semantics:
    - The server in the URL (``primary``) is the one the user clicked. It MUST
      have ``allow_writes=True``, otherwise the caller raises 403.
    - All OTHER active servers with ``allow_writes=True`` are added as fan-out
      targets so independent databases stay in sync (e.g. two PowerDNS
      instances with separate MariaDB backends).
    - If ``allow_writes`` column is missing in the DB (very old install),
      we treat every server as writable for backward compatibility.

    Returns ``(targets, info_messages)``. ``info_messages`` is a per-server
    dict of human-readable warnings (e.g. "server X is read-only and was
    skipped"). The caller stores these alongside per-server results.
    """
    info: dict[str, str] = {}

    has_column = hasattr(ServerConfig, "allow_writes")
    configs: list[ServerConfig] = []
    try:
        result = await db.execute(
            select(ServerConfig).where(ServerConfig.is_active == True)  # noqa: E712
        )
        configs = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load server_configs for fan-out: %s", exc)
        configs = []

    by_name = {c.name: c for c in configs}

    def _is_writable(name: str) -> bool:
        cfg = by_name.get(name)
        if cfg is None:
            # Server only known via env -> treat as writable
            return True
        if not has_column:
            return True
        return bool(getattr(cfg, "allow_writes", True))

    if not _is_writable(primary):
        info[primary] = "read-only"
        return ([], info)

    # primary first, then the rest of writable peers
    targets: list[tuple[str, PowerDNSClient]] = []
    seen: set[str] = set()
    try:
        targets.append((primary, pdns_manager.get_client(primary)))
        seen.add(primary)
    except ValueError as exc:
        info[primary] = str(exc)
        return ([], info)

    # All other active+writable servers known in the manager
    for name in pdns_manager.list_servers():
        if name in seen:
            continue
        if not _is_writable(name):
            info[name] = "read-only"
            continue
        try:
            targets.append((name, pdns_manager.get_client(name)))
            seen.add(name)
        except ValueError:
            continue

    return (targets, info)


def _zone_not_found_for(name: str, exc: PowerDNSAPIError) -> bool:
    """PowerDNS returns 404/422 when the zone doesn't exist on that server.
    We treat this as "skip silently" during fan-out, because in mixed setups
    not every writable server hosts every zone.
    """
    if exc.status_code in (404, 422):
        return True
    detail = (exc.detail or "").lower()
    return "could not find domain" in detail or "no such zone" in detail


def _read_only_error(server_name: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=(
            f"Server '{server_name}' ist auf 'Speichern: Nein' gesetzt. "
            "Wechsle in der Zonenliste auf einen Server mit aktivem Speichern, "
            "oder aktiviere 'Speichern' für diesen Server in den Einstellungen → DNS-Server."
        ),
    )


def _summarize_results(results: dict[str, str], info: dict[str, str]) -> dict:
    """Combine per-server outcomes into a UI-friendly structure."""
    out = {}
    out.update(results)
    for k, v in info.items():
        if k not in out:
            out[k] = f"skipped ({v})"
    return out


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None,
    user_id: int = None,
):
    """Helper to create audit log entries (mit user_id)."""
    log = AuditLog(
        action=action,
        resource_type="record",
        resource_name=resource_name,
        server_name=server_name,
        details=details,
        status=status,
        error_message=error_message,
        user_id=user_id,
    )
    db.add(log)
    await db.flush()


@router.get("/{server_name}/{zone_id:path}")
async def list_records(
    server_name: str,
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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


@router.post("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def create_record(
    server_name: str,
    zone_id: str,
    record: RecordCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace a record set in a zone.

    Writes are fanned out to every active server with ``allow_writes=True``
    that hosts this zone, so independent PowerDNS instances stay in sync.
    Servers without the zone are skipped silently. Read-only servers are
    refused before any write happens.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
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

    await _log_action(
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
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_service import deliver_webhooks_background
    deliver_webhooks_background(
        current_user.id,
        "record.created",
        {"server": server_name, "zone": zone_id, "name": record.name, "type": record.type},
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a record set from a zone. Fans out to all writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False

    for srv_name, client in targets:
        try:
            await client.delete_record(zone_id, record.name, record.type)
            results[srv_name] = "deleted"
            if srv_name == server_name:
                primary_success = True
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    await _log_action(
        db, "DELETE", record.name, server_name,
        {"zone": zone_id, "type": record.type, "fanout": _summarize_results(results, info)},
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_service import deliver_webhooks_background
    deliver_webhooks_background(
        current_user.id,
        "record.deleted",
        {"server": server_name, "zone": zone_id, "name": record.name, "type": record.type},
    )

    return MessageResponse(
        message=f"Record '{record.name}' ({record.type}) deleted from zone '{zone_id}'",
        details=_summarize_results(results, info),
    )


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_record(
    server_name: str,
    zone_id: str,
    update: RecordUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a specific record's content or TTL. Fans out to writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)
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

    await _log_action(
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
    )

    if not primary_success:
        if primary_not_found:
            raise HTTPException(status_code=404, detail=f"Original record content not found in {update.name}")
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_service import deliver_webhooks_background
    deliver_webhooks_background(
        current_user.id,
        "record.updated",
        {"server": server_name, "zone": zone_id, "name": update.name, "type": update.type},
    )

    return MessageResponse(
        message=f"Record '{update.name}' ({update.type}) updated in zone '{zone_id}'",
        details=_summarize_results(results, info),
    )



@router.post("/{server_name}/{zone_id:path}/bulk", response_model=MessageResponse)
async def bulk_update_records(
    server_name: str,
    zone_id: str,
    bulk: BulkRecordUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bulk record operations. Fans out to all writable peers."""
    await assert_zone_access(db, current_user, zone_id, write=True)

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
    for record in bulk.delete:
        rrsets.append({
            "name": record.name,
            "type": record.type,
            "changetype": "DELETE",
        })

    if not rrsets:
        raise HTTPException(status_code=400, detail="No records to process")

    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    results: dict[str, str] = {}
    primary_error: PowerDNSAPIError | None = None
    primary_success = False

    for srv_name, client in targets:
        try:
            await client.update_records(zone_id, rrsets)
            results[srv_name] = "saved"
            if srv_name == server_name:
                primary_success = True
        except PowerDNSAPIError as e:
            if _zone_not_found_for(srv_name, e):
                results[srv_name] = "skipped (zone not present)"
                continue
            results[srv_name] = f"error: {e.detail}"
            if srv_name == server_name:
                primary_error = e

    await _log_action(
        db, "BULK_UPDATE", zone_id, server_name,
        {
            "created": len(bulk.create),
            "deleted": len(bulk.delete),
            "fanout": _summarize_results(results, info),
        },
        status="success" if primary_success else "error",
        error_message=None if primary_success else (primary_error.detail if primary_error else "no writable target accepted the change"),
        user_id=current_user.id,
    )

    if not primary_success:
        if primary_error is not None:
            raise HTTPException(status_code=primary_error.status_code, detail=primary_error.detail)
        raise HTTPException(status_code=502, detail="No writable server accepted the change")

    from app.services.webhook_service import deliver_webhooks_background
    deliver_webhooks_background(
        current_user.id,
        "record.bulk",
        {"server": server_name, "zone": zone_id, "created": len(bulk.create), "deleted": len(bulk.delete)},
    )

    return MessageResponse(
        message=f"Bulk update completed: {len(bulk.create)} created/updated, {len(bulk.delete)} deleted",
        details={
            "created": len(bulk.create),
            "deleted": len(bulk.delete),
            "fanout": _summarize_results(results, info),
        },
    )
