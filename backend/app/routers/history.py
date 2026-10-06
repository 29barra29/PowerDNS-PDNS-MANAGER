"""Zonenverlauf und Rollback (F7 3.2–3.5, Bauplan B.7).

Routen (Prefix ``/api/v1/zones``, eingebunden VOR ``zones.router`` ueber ``ROUTER_ORDER = 50``)::

    GET  /{server}/{zone}/history                              Liste (Lese- oder Schreibrecht)
    GET  /{server}/{zone}/history/{id:int}/rollback-preview    Vorschau (Schreibrecht)
    POST /{server}/{zone}/history/{id:int}/rollback            Zuruecksetzen (Schreibrecht, DbWrite)
    GET  /{server}/{zone}/history/{id:int}                     Einzeleintrag (Lese- oder Schreibrecht)

Sicherheit:
- Erste Zeile jedes Endpunkts ist ``assert_zone_access`` (inkl. Token-Scope, F14).
- Die Zone eines Eintrags kommt immer aus ``audit_logs.zone_name``; ein Eintrag einer fremden Zone ergibt 404.
- Nicht-Admins sehen ``details`` nur ueber ``public_details`` und einen generischen Fehlertext; Eintraege vor der
  letzten endgueltigen Zonenloeschung (fruehere Zone gleichen Namens) sind fuer sie unsichtbar (404) [S12, D12].
  Admins sehen sie mit ``before_recreate``; zuruecksetzen laesst sich keiner davon (``zone_recreated``).
- Rollback schreibt ueber den URL-Server als Primary und alle schreibbaren Peers (Fan-out Betriebsart A mit
  ``before_state`` fuer die Nachpruefung bei Transportfehlern), LUA-Ziele unterliegen der LUA-Policy (F15).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import assert_zone_access, get_current_user, has_zone_access, is_effective_admin
from app.core.database import DbRead, DbWrite
from app.core.names import normalize_zone_name
from app.models.models import AuditLog, User
from app.schemas.dns import MessageResponse
from app.schemas.history import HistoryEntry, HistoryListResponse, RollbackPreviewResponse, RollbackRequest
from app.services import fanout
from app.services.audit import write_audit
from app.services.pdns_client import PowerDNSAPIError, pdns_manager
from app.services.record_history import (
    BLOCK_MESSAGES,
    ROLLBACK_CONFLICT_MESSAGE,
    PrimaryCapture,
    RollbackItem,
    build_audit_filters,
    describe_change,
    exclusion_reason,
    inverse_rrsets,
    last_final_zone_delete_id,
    load_reverted_by,
    load_usernames,
    plan_rollback,
    read_rrsets,
    rollback_block_reason,
    rr_key,
    serialize_history_entry,
    webhook_changes,
)

logger = logging.getLogger(__name__)

ROUTER_ORDER = 50
router = APIRouter(prefix="/zones", tags=["History"])

ENTRY_NOT_FOUND = "Audit-Eintrag nicht gefunden"
PDNS_UNREACHABLE = "PowerDNS-Server ist derzeit nicht erreichbar"
NO_TARGET_DETAIL = "Kein schreibbarer Server hat die Änderung übernommen"
NOOP_MESSAGE = "Nichts zu tun: Der aktuelle Zustand entspricht bereits dem Zustand vor der Änderung"
MAX_ACTORS = 200


# =============================================================================
# Hilfen
# =============================================================================
async def can_write_zone(db: AsyncSession, user: User, zone_id: str) -> bool:
    """Schreibrecht des Aufrufers (inkl. Token-Scope/Lese-Token) ohne Exception."""
    return await has_zone_access(db, user, zone_id, write=True)


async def assert_lua_allowed_for_plan(db: AsyncSession, user: User, plan: list[RollbackItem]) -> None:
    """Ein Rollback, der LUA-RRsets (wieder)herstellt, unterliegt derselben Policy wie das Anlegen (F15)."""
    if any(item.type == "LUA" and item.target is not None for item in plan):
        from app.services.lua_records import assert_lua_write_allowed

        await assert_lua_write_allowed(db, user)


async def _visible_entry(db: AsyncSession, zone: str, audit_id: int, *, admin: bool,
                         cutoff: Optional[int]) -> AuditLog:
    """Eintrag dieser Zone oder 404 (fremde Zone, unbekannt, fuer Nicht-Admins: vor der Neuanlage der Zone)."""
    log = await db.get(AuditLog, audit_id)
    if log is None or normalize_zone_name(log.zone_name or "") != zone:
        raise HTTPException(status_code=404, detail=ENTRY_NOT_FOUND)
    if not admin and cutoff is not None and (log.id or 0) <= cutoff:
        raise HTTPException(status_code=404, detail=ENTRY_NOT_FOUND)
    return log


def _require_client(server_name: str) -> None:
    try:
        pdns_manager.get_client(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _change_keys(log: AuditLog) -> list[tuple[str, str]]:
    keys = []
    for c in (log.details or {}).get("changes") or []:
        if not isinstance(c, dict):
            continue
        key = rr_key(c.get("name", ""), c.get("type", ""))
        if exclusion_reason(*key) is None and key not in keys:
            keys.append(key)
    return keys


async def _read_current(client: Any, zone_id: str, keys: list, *, zone: str, server_name: str) -> Any:
    """Aktuellen Zustand der betroffenen RRsets am Primary lesen; Fehler als HTTPException (F7 3.4 Schritt 4)."""
    try:
        return await read_rrsets(client, zone_id, keys)
    except PowerDNSAPIError as exc:
        if fanout.zone_not_found_for(exc):
            raise HTTPException(status_code=404, detail=f"Zone '{zone}' existiert auf Server '{server_name}' nicht")
        raise HTTPException(status_code=exc.status_code or 502, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001 - httpx & Co.: Ursache nur ins Log
        logger.warning("History: Zustand von %s nicht lesbar (%s): %s", zone, server_name, type(exc).__name__)
        raise HTTPException(status_code=502, detail=PDNS_UNREACHABLE)


def _targets_view(targets: fanout.Targets, info: dict[str, str]) -> dict[str, str]:
    out = {name: "write" for name, _ in targets}
    for name, hint in info.items():
        out.setdefault(name, f"skipped ({hint})")
    return out


# =============================================================================
# Endpunkte (Reihenfolge laut F7 3.0)
# =============================================================================
@router.get("/{server_name}/{zone_id:path}/history", response_model=HistoryListResponse)
async def list_zone_history(
    server_name: str,
    zone_id: str,
    db: DbRead,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None, max_length=300),
    resource_type: Optional[str] = Query(None, max_length=50),
    record_type: Optional[str] = Query(None, alias="type", max_length=16),
    name: Optional[str] = Query(None, max_length=255),
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    user_id: Optional[int] = Query(None),
    status_filter: Optional[Literal["success", "error"]] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Zonenverlauf ueber alle Server (neueste zuerst). ``server_name`` dient nur der Berechnung von ``can_rollback``."""
    await assert_zone_access(db, current_user, zone_id)
    zone = normalize_zone_name(zone_id)
    admin = is_effective_admin(current_user)
    cutoff = await last_final_zone_delete_id(db, zone)

    conds = build_audit_filters(
        zone=zone, action=action, resource_type=resource_type, user_id=user_id, status=status_filter,
        date_from=date_from, date_to=date_to, q=q, name=name, record_type=record_type,
        public_only=not admin,  # [S12] q nicht ueber ausgeblendete Felder (kein Such-Orakel)
    )
    visible = [AuditLog.zone_name == zone, AuditLog.user_id.isnot(None)]
    if not admin and cutoff is not None:
        conds.append(AuditLog.id > cutoff)
        visible.append(AuditLog.id > cutoff)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conds))
    rows = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    actor_ids = [r[0] for r in (await db.execute(
        select(AuditLog.user_id).where(*visible).distinct().limit(MAX_ACTORS)
    )).all() if r[0] is not None]

    usernames = await load_usernames(db, {r.user_id for r in rows} | set(actor_ids))
    reverted = await load_reverted_by(db, [r.id for r in rows])
    user_can_write = await can_write_zone(db, current_user, zone_id)
    server_writable = await fanout.is_server_writable(db, server_name)
    entries = [
        serialize_history_entry(
            r, usernames, reverted, user_can_write=user_can_write, server_writable=server_writable,
            max_changes=20, admin=admin, recreated_cutoff=cutoff,
        )
        for r in rows
    ]
    actors = [{"user_id": uid, "username": usernames.get(uid)} for uid in sorted(set(actor_ids))]
    return {"zone": zone, "server": server_name, "total": int(total or 0), "offset": offset, "limit": limit,
            "entries": entries, "actors": actors}


@router.get("/{server_name}/{zone_id:path}/history/{audit_id:int}/rollback-preview",
            response_model=RollbackPreviewResponse)
async def rollback_preview(
    server_name: str,
    zone_id: str,
    audit_id: int,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Plan fuer das Zuruecksetzen gegen den aktuellen Zustand des Primary. Ein Konflikt ist hier kein Fehler."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone = normalize_zone_name(zone_id)
    admin = is_effective_admin(current_user)
    cutoff = await last_final_zone_delete_id(db, zone)
    log = await _visible_entry(db, zone, audit_id, admin=admin, cutoff=cutoff)

    base = {"audit_id": log.id, "zone": zone, "server": server_name, "source_server": log.server_name,
            "action": log.action, "already_reverted_by": (await load_reverted_by(db, [log.id])).get(log.id)}

    def blocked(code: str, targets: Optional[dict] = None) -> dict:
        return {**base, "rollbackable": False, "blocked_reason": code, "blocked_message": BLOCK_MESSAGES[code],
                "has_conflicts": False, "plan": [], "skipped": [], "targets": targets or {}}

    reason = rollback_block_reason(log, recreated_cutoff=cutoff)
    if reason:
        return blocked(reason)
    _require_client(server_name)
    targets, info = await fanout.writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        return blocked("server_read_only", _targets_view(targets, info))
    current = await _read_current(targets[0][1], zone_id, _change_keys(log), zone=zone, server_name=server_name)
    plan, skipped = plan_rollback(log, current, origin=zone)
    if not plan:
        return blocked("only_excluded_records", _targets_view(targets, info))
    return {**base, "rollbackable": True, "blocked_reason": None, "blocked_message": None,
            "has_conflicts": any(i.conflict for i in plan), "plan": [i.to_dict() for i in plan],
            "skipped": skipped, "targets": _targets_view(targets, info)}


@router.post("/{server_name}/{zone_id:path}/history/{audit_id:int}/rollback", response_model=MessageResponse)
async def rollback_change(
    server_name: str,
    zone_id: str,
    audit_id: int,
    db: DbWrite,
    body: Optional[RollbackRequest] = None,
    current_user: User = Depends(get_current_user),
):
    """Setzt die RRsets eines v2-Eintrags auf den Vorher-Zustand (Primary + alle schreibbaren Peers).

    409 bei Konflikt ohne ``force``; Noop (Zustand bereits wie vorher) -> 200 ohne Audit/Webhook. Erfolg:
    Audit ``RECORD_ROLLBACK`` (``revert_of_id``) und Ereignis ``record.rollback``.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    force = bool(body.force) if body is not None else False
    zone = normalize_zone_name(zone_id)
    admin = is_effective_admin(current_user)
    cutoff = await last_final_zone_delete_id(db, zone)
    log = await _visible_entry(db, zone, audit_id, admin=admin, cutoff=cutoff)

    reason = rollback_block_reason(log, recreated_cutoff=cutoff)
    if reason:
        raise HTTPException(status_code=422, detail={"message": BLOCK_MESSAGES[reason], "code": reason})
    _require_client(server_name)
    targets, info = await fanout.writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise fanout.read_only_error(server_name)
    primary_client = targets[0][1]
    current = await _read_current(primary_client, zone_id, _change_keys(log), zone=zone, server_name=server_name)
    plan, skipped = plan_rollback(log, current, origin=zone)
    if not plan:
        raise HTTPException(status_code=422, detail={"message": BLOCK_MESSAGES["only_excluded_records"],
                                                     "code": "only_excluded_records"})
    await assert_lua_allowed_for_plan(db, current_user, plan)
    conflicts = [i for i in plan if i.conflict]
    if conflicts and not force:
        raise HTTPException(status_code=409, detail={
            "message": ROLLBACK_CONFLICT_MESSAGE,
            "code": "rollback_conflict",
            "conflicts": [i.conflict_info() for i in conflicts],
            "already_reverted_by": (await load_reverted_by(db, [log.id])).get(log.id),
        })
    writes = [i for i in plan if not i.noop]
    if not writes:
        return MessageResponse(message=NOOP_MESSAGE, details={"noop": True, "skipped": skipped})

    forced = bool(force and conflicts)
    extra: dict[str, Any] = {"revert_of": audit_id, "forced": forced, "skipped": skipped}
    if forced:
        extra["conflicts"] = [i.conflict_info() for i in conflicts]
    before_map = {i.key: i.current for i in writes}
    fan = await fanout.apply_rrsets(
        db, server_name, zone_id, inverse_rrsets(writes), targets=targets, info=info, before_state=before_map,
    )
    capture = PrimaryCapture.from_before(server_name, before_map)
    change = await describe_change(fan, capture, primary_client, zone_id, zone, extra=extra)
    res_name = writes[0].name if len(writes) == 1 else zone

    if not fan.primary_success:
        if fan.primary_status == fanout.STATUS_ZONE_MISSING:
            exc = HTTPException(status_code=404, detail=f"Zone '{zone}' existiert auf Server '{server_name}' nicht")
        elif fan.primary_error is not None:
            exc = HTTPException(status_code=fan.primary_error.status_code or 502, detail=fan.primary_error.detail)
        else:
            exc = HTTPException(status_code=502, detail=NO_TARGET_DETAIL)
        await write_audit(
            db, "RECORD_ROLLBACK", "record", res_name, status="error", error_message=str(exc.detail),
            details=change.details, user_id=current_user.id, server_name=server_name, zone_name=zone,
            revert_of_id=audit_id,
        )
        raise exc

    # PTR-Pflege wie bei jedem anderen Record-Schreiber (Plan B.6a, Review-Fund L-8): Rollback hat kein eigenes
    # manage_ptr, es gilt der Admin-Default ptr_auto_default (Standard aus)
    from app.services import bulk as bulk_service

    ptr = await bulk_service.sync_ptr_for_changes(db, current_user, server_name, None, change.changes,
                                                  action="ROLLBACK", zone=zone)
    if ptr is not None:
        change.details["ptr"] = ptr.compact
    new_log = await write_audit(
        db, "RECORD_ROLLBACK", "record", res_name, details=change.details, user_id=current_user.id,
        server_name=server_name, zone_name=zone, revert_of_id=audit_id,
    )
    from app.services.webhook_outbox import enqueue_event
    await enqueue_event(
        db, "record.rollback", actor=current_user, zone=zone, server=server_name,
        data={"server": server_name, "zone": zone, "reverted_audit_log_id": audit_id, "forced": forced,
              **webhook_changes(change.changes), "fanout": fan.summary,
              **({"ptr": ptr.compact} if ptr is not None else {})},
        audit_log_id=new_log.id if new_log else None,
    )
    details = {"revert_audit_id": new_log.id if new_log else None,
               "rolled_back": [{"name": i.name, "type": i.type} for i in writes],
               "skipped": skipped, "forced": forced, "fanout": fan.summary,
               "primary_outcome": fan.primary_outcome}
    if ptr is not None:
        details["ptr"] = ptr.results
    return MessageResponse(message=f"Änderung #{audit_id} zurückgesetzt", details=details)


@router.get("/{server_name}/{zone_id:path}/history/{audit_id:int}", response_model=HistoryEntry)
async def get_zone_history_entry(
    server_name: str,
    zone_id: str,
    audit_id: int,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Einzeleintrag mit allen ``changes`` (404, wenn er nicht zu dieser Zone gehoert)."""
    await assert_zone_access(db, current_user, zone_id)
    zone = normalize_zone_name(zone_id)
    admin = is_effective_admin(current_user)
    cutoff = await last_final_zone_delete_id(db, zone)
    log = await _visible_entry(db, zone, audit_id, admin=admin, cutoff=cutoff)
    usernames = await load_usernames(db, [log.user_id])
    reverted = await load_reverted_by(db, [log.id])
    return serialize_history_entry(
        log, usernames, reverted,
        user_can_write=await can_write_zone(db, current_user, zone_id),
        server_writable=await fanout.is_server_writable(db, server_name),
        max_changes=None, admin=admin, recreated_cutoff=cutoff,
    )
