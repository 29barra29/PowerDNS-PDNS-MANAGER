"""API routes for DNS record management.

Schreibende Endpunkte (Bauplan B.5/B.7, F7 5.4):

- Fan-out ueber ``fanout.apply_rrsets`` in **Betriebsart B**: je Server wird dessen eigener Stand gelesen und ein
  Builder mit 2.4.1-Semantik erzeugt die RRsets fuer genau diesen Server (create = Bestand + neue Werte, update =
  Wert ersetzen, delete = Wert bzw. RRset entfernen). Peer-eigene Werte bleiben so erhalten [D1]. Primary zuerst,
  bei Primary-Fehler keine Peer-Writes (f52); Transportfehler am Primary werden per Re-Read nachgeprueft [D3].
- Audit v2 (``services/record_history``): der Vorher-Zustand stammt aus genau dem Lesezugriff, auf dem der
  Primary-Payload beruht (``PrimaryCapture``), der Nachher-Zustand per Re-Read (Fallback: berechnet).
  ``primary_outcome`` steht in den Details. Commit vor der Antwort per ``DbWrite``.
- Webhook-Ereignis per ``await enqueue_event`` (Outbox, B.8) mit den Daten aus F6 5.3 (``changes``, ``fanout``;
  die v1-Felder bleiben).
- PTR-Pflege (F11, Plan B.6a) an allen vier Einhaengepunkten (create/update/delete/bulk) ueber
  ``bulk_service.sync_ptr_for_changes`` -> ``ptr.sync_for_changes`` mit den Audit-v2-``changes``: nur nach
  Primary-Erfolg, vor dem Erfolgs-Audit; Ergebnis in ``details.ptr`` (Antwort: vollstaendig, Audit/Webhook:
  kompakt).
- Bulk-Editor (F1): ``POST …/bulk/preview`` (Trockenlauf) und ``POST …/bulk`` delegieren an ``services/bulk.py``
  (Plan je Server, Betriebsart B, ``expected``-Sperre, Audit ``BULK_UPDATE`` v2, Webhook ``record.bulk``).
"""
import logging
from typing import Any, Iterable, Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, assert_zone_access
from app.core.database import DbRead, DbWrite
from app.core.names import normalize_zone_name
from app.models.models import AuditLog, User
from app.schemas.bulk import BulkPreviewRequest, BulkPreviewResponse, BulkRecordUpdate
from app.schemas.dns import RecordCreate, RecordDelete, MessageResponse, RecordUpdate
from app.services import bulk as bulk_service
from app.services import fanout
from app.services.audit import write_audit
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.services.record_history import (
    PrimaryCapture,
    RRKey,
    describe_change,
    rr_key,
    webhook_changes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records"])

# Fan-out-Helfer liegen seit 3.0 in services/fanout.py (Bauplan B.5); die alten Namen bleiben als Aliase.
_writable_targets_for_zone = fanout.writable_targets_for_zone
_read_only_error = fanout.read_only_error
_summarize_results = fanout.summarize_results

NO_TARGET_DETAIL = "No writable server accepted the change"  # Text wie 2.4.1 (Skript-Kompatibilitaet)


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


# =============================================================================
# Gemeinsame Bausteine der Schreib-Endpoints
# =============================================================================
async def _run_fanout(
    db: AsyncSession,
    *,
    server_name: str,
    zone_id: str,
    targets: fanout.Targets,
    info: dict[str, str],
    keys: Iterable[RRKey],
    builder: fanout.RRsetBuilder,
    read_rrset: Optional[tuple[str, str]] = None,
    success_status: str = fanout.STATUS_SAVED,
) -> tuple[fanout.FanoutResult, PrimaryCapture, Any]:
    """Betriebsart B mit Erfassung des Vorher-Zustands am Primary. Rueckgabe ``(fan, capture, primary_client)``."""
    capture = PrimaryCapture(server_name, keys)
    fan = await fanout.apply_rrsets(
        db, server_name, zone_id, build=capture.wrap(builder), targets=targets, info=info,
        read_rrset=read_rrset, success_status=success_status,
    )
    return fan, capture, dict(targets).get(server_name)


def _primary_failure(fan: fanout.FanoutResult, *, zone_id: str, server_name: str,
                     not_found_detail: Optional[str] = None) -> HTTPException:
    """HTTP-Fehler fuer einen gescheiterten Primary (Statuscodes wie 2.4.1)."""
    status_text = fan.primary_status
    if status_text == fanout.STATUS_NO_MATCH and not_found_detail:
        return HTTPException(status_code=404, detail=not_found_detail)
    if status_text == fanout.STATUS_ZONE_MISSING:
        return HTTPException(status_code=404,
                             detail=f"Zone '{zone_id}' existiert auf Server '{server_name}' nicht")
    if fan.primary_error is not None:
        return HTTPException(status_code=fan.primary_error.status_code or 502, detail=fan.primary_error.detail)
    return HTTPException(status_code=502, detail=NO_TARGET_DETAIL)


def _find_rrset(zone_json: Any, name: str, rtype: str) -> Optional[dict]:
    key = rr_key(name, rtype)
    rrsets = zone_json if isinstance(zone_json, list) else ((zone_json or {}).get("rrsets") or [])
    for rr in rrsets:
        if rr_key(str(rr.get("name", "")), str(rr.get("type", ""))) == key:
            return rr
    return None


def _missing_value_detail(zone_json: Any, value_deletes: Iterable[Any]) -> str:
    """404-Text fuer den ersten nicht vorhandenen Wert bzw. das fehlende RRset (Texte wie 2.4.1)."""
    pending: dict[RRKey, list] = {}
    for vd in value_deletes:
        key = rr_key(vd.name, vd.type)
        if key not in pending:
            rr = _find_rrset(zone_json, vd.name, vd.type)
            if rr is None:
                return f"RRset {vd.name} {vd.type} nicht vorhanden"
            pending[key] = [r.get("content") for r in rr.get("records") or []]
        if vd.content not in pending[key]:
            return f"Wert '{vd.content}' nicht im RRset {vd.name} {vd.type} vorhanden"
        pending[key] = [c for c in pending[key] if c != vd.content]
    return "Zu löschender Wert nicht vorhanden"


def _primary_payload(fan: fanout.FanoutResult, server_name: str) -> list[dict]:
    return list(fan.per_server_rrsets.get(server_name) or [])


async def _ptr_hook(db: AsyncSession, user: User, server_name: str, requested: Optional[bool], change: Any, *,
                    action: str, zone: str, ttl_default: int = 3600) -> Optional[bulk_service.PtrOutcome]:
    """PTR-Pflege nach Primary-Erfolg (F11 5.12, B.6a); traegt das kompakte Ergebnis in die Audit-Details ein.

    ``action``/``zone`` erscheinen im ``PTR_SYNC``-Audit und -Webhook (Review-Fund L-5)."""
    ptr = await bulk_service.sync_ptr_for_changes(db, user, server_name, requested, change.changes,
                                                  ttl_default=ttl_default, action=action, zone=zone)
    if ptr is not None:
        change.details["ptr"] = ptr.compact
    return ptr


def _ptr_data(ptr: Optional[bulk_service.PtrOutcome]) -> dict:
    """Webhook-Zusatz ``data.ptr`` (kompakt) – leer ohne PTR-Pflege."""
    return {"ptr": ptr.compact} if ptr is not None else {}


def _with_ptr(summary: dict, ptr: Optional[bulk_service.PtrOutcome]) -> dict:
    """Antwort-``details``: Fan-out-Map plus ``ptr`` (Liste der PtrResults), falls PTR-Pflege lief."""
    out = dict(summary)
    if ptr is not None:
        out["ptr"] = ptr.results
    return out


# =============================================================================
# Endpunkte
# =============================================================================
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


# WICHTIG: /bulk/preview und /bulk muessen VOR der allgemeinen POST-Route stehen. Starlette nimmt den ersten
# passenden Treffer, und {zone_id:path} wuerde sonst auch ".../bulk" bzw. ".../bulk/preview" als Zonennamen
# schlucken (F1 3.0). /bulk/preview kollidiert nicht mit /bulk (dessen Muster endet auf "/bulk").
@router.post("/{server_name}/{zone_id:path}/bulk/preview", response_model=BulkPreviewResponse)
async def preview_bulk_records(
    server_name: str,
    zone_id: str,
    req: BulkPreviewRequest,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Vorschau einer Bulk-Aenderung (F1 3.2): Diff je RRset, Probleme, fertiger Request-Body fuer ``/bulk``.

    Nur mit Schreibrecht auf die Zone (keine Probe-Requests fuer Leser); schreibt nichts, kein Audit/Webhook.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    return await bulk_service.preview_bulk(db, current_user, server_name, zone_id, req)


@router.post("/{server_name}/{zone_id:path}/bulk", response_model=MessageResponse)
async def bulk_update_records(
    server_name: str,
    zone_id: str,
    bulk: BulkRecordUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Bulk-Aenderung (F1 3.3): je Server ein Plan und ein atomarer PATCH, Primary zuerst.

    ``create`` = REPLACE, ``delete`` (RRset oder Wert), ``merge``, ``set_ttl``, ``set_disabled``; ``expected``
    -> 409 bei zwischenzeitlicher Aenderung (``force`` nur per API). Audit ``BULK_UPDATE`` v2 mit allen
    ``changes``, Webhook ``record.bulk``, PTR-Pflege ueber ``manage_ptr``.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    return await bulk_service.apply_bulk(db, current_user, server_name, zone_id, bulk)


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
    that hosts this zone (Primary zuerst). Je Server werden die neuen Werte mit dessen
    Bestand zusammengefuehrt. Servers without the zone are skipped silently. Read-only
    servers are refused before any write happens.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    await _assert_lua_allowed(db, current_user, [record.type])
    zone_norm = normalize_zone_name(zone_id)
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    key = rr_key(record.name, record.type)
    fan, capture, primary_client = await _run_fanout(
        db, server_name=server_name, zone_id=zone_id, targets=targets, info=info, keys=[key],
        builder=fanout.create_builder(record.name, record.type, record.ttl, record.records),
        read_rrset=(record.name, record.type),
    )
    payload = _primary_payload(fan, server_name)
    final_records = (payload[0].get("records") or []) if payload else []
    legacy = {"type": record.type, "ttl": record.ttl, "records": [r["content"] for r in final_records]}
    change = await describe_change(fan, capture, primary_client, zone_id, zone_norm, legacy=legacy)

    if not fan.primary_success:
        exc = _primary_failure(fan, zone_id=zone_id, server_name=server_name)
        await _log_action(
            db, "CREATE", record.name, server_name, change.details, status="error",
            error_message=str(exc.detail), user_id=current_user.id, zone_name=zone_norm,
        )
        raise exc

    ptr = await _ptr_hook(db, current_user, server_name, record.manage_ptr, change, action="CREATE", zone=zone_norm,
                          ttl_default=record.ttl)
    audit = await _log_action(
        db, "CREATE", record.name, server_name, change.details,
        user_id=current_user.id, zone_name=zone_norm,
    )

    before_snap = (capture.before or {}).get(key)
    existing = {r["content"] for r in (before_snap or {}).get("records") or []}
    added: list[str] = []
    for r in record.records:
        if r.content not in existing and r.content not in added:
            added.append(r.content)

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.created", actor=current_user, zone=zone_norm, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": record.name, "type": record.type,
              "ttl": record.ttl, "added": added, **webhook_changes(change.changes), "fanout": fan.summary,
              **_ptr_data(ptr)},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=f"Record '{record.name}' ({record.type}) created/updated in zone '{zone_id}'",
        details=_with_ptr(fan.summary, ptr),
    )


@router.delete("/{server_name}/{zone_id:path}/delete", response_model=MessageResponse)
async def delete_record(
    server_name: str,
    zone_id: str,
    record: RecordDelete,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Delete a record set (or a single value, if ``content`` is set). Fans out to all writable peers.

    Der Vorher-Zustand (auch beim Loeschen des ganzen RRsets ohne ``content``) steht nach dem Fan-out in
    ``capture.before[key]`` bzw. ``change.before[key]`` bereit (F11: PTR-Pflege).
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    key = rr_key(record.name, record.type)
    fan, capture, primary_client = await _run_fanout(
        db, server_name=server_name, zone_id=zone_id, targets=targets, info=info, keys=[key],
        builder=fanout.delete_builder(record.name, record.type, record.content),
        read_rrset=(record.name, record.type), success_status=fanout.STATUS_DELETED,
    )
    legacy = {"type": record.type, "content": record.content}
    change = await describe_change(fan, capture, primary_client, zone_id, zone_norm, legacy=legacy)

    if not fan.primary_success:
        exc = _primary_failure(
            fan, zone_id=zone_id, server_name=server_name,
            not_found_detail=_missing_value_detail(capture.zone_json, [record]) if record.content is not None else None,
        )
        await _log_action(
            db, "DELETE", record.name, server_name, change.details, status="error",
            error_message=str(exc.detail), user_id=current_user.id, zone_name=zone_norm,
        )
        raise exc

    ptr = await _ptr_hook(db, current_user, server_name, record.manage_ptr, change, action="DELETE", zone=zone_norm)
    audit = await _log_action(
        db, "DELETE", record.name, server_name, change.details,
        user_id=current_user.id, zone_name=zone_norm,
    )

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.deleted", actor=current_user, zone=zone_norm, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": record.name, "type": record.type,
              "content": record.content, **webhook_changes(change.changes), "fanout": fan.summary,
              **_ptr_data(ptr)},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=(
            f"Value '{record.content}' removed from record '{record.name}' ({record.type}) in zone '{zone_id}'"
            if record.content is not None
            else f"Record '{record.name}' ({record.type}) deleted from zone '{zone_id}'"
        ),
        details=_with_ptr(fan.summary, ptr),
    )


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_record(
    server_name: str,
    zone_id: str,
    update: RecordUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Update a specific record's content or TTL. Fans out to writable peers (Wert je Server ersetzen)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    await _assert_lua_allowed(db, current_user, [update.type])
    zone_norm = normalize_zone_name(zone_id)
    targets, info = await _writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise _read_only_error(server_name)

    key = rr_key(update.name, update.type)
    fan, capture, primary_client = await _run_fanout(
        db, server_name=server_name, zone_id=zone_id, targets=targets, info=info, keys=[key],
        builder=fanout.update_builder(update.name, update.type, update.old_content, update.new_content,
                                      update.ttl, update.disabled),
        read_rrset=(update.name, update.type),
    )
    legacy = {"type": update.type, "old": update.old_content, "new": update.new_content}
    change = await describe_change(fan, capture, primary_client, zone_id, zone_norm, legacy=legacy)

    if not fan.primary_success:
        exc = _primary_failure(fan, zone_id=zone_id, server_name=server_name,
                               not_found_detail=f"Original record content not found in {update.name}")
        await _log_action(
            db, "UPDATE", update.name, server_name, change.details, status="error",
            error_message=str(exc.detail), user_id=current_user.id, zone_name=zone_norm,
        )
        raise exc

    ptr = await _ptr_hook(db, current_user, server_name, update.manage_ptr, change, action="UPDATE", zone=zone_norm,
                          ttl_default=update.ttl)
    audit = await _log_action(
        db, "UPDATE", update.name, server_name, change.details,
        user_id=current_user.id, zone_name=zone_norm,
    )

    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.updated", actor=current_user, zone=zone_norm, server=server_name,
        data={"server": server_name, "zone": zone_id, "name": update.name, "type": update.type,
              "ttl": update.ttl, "old_content": update.old_content, "new_content": update.new_content,
              **webhook_changes(change.changes), "fanout": fan.summary, **_ptr_data(ptr)},
        audit_log_id=audit.id if audit else None,
    )

    return MessageResponse(
        message=f"Record '{update.name}' ({update.type}) updated in zone '{zone_id}'",
        details=_with_ptr(fan.summary, ptr),
    )
