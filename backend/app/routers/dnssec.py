"""DNSSEC-Endpunkte (F4 3, Neuschrieb in 3.0).

Gemeinsame Vorpruefungen (Spec 3.0), in dieser Reihenfolge:
P1 ``assert_zone_access`` als erste Zeile jedes Endpunkts (schreibend und Einzel-Key-GET mit ``write=True``),
P2 Server (404), P3 nur schreibend: Server mit "Speichern: Nein" -> 403, P4 Zonenkontext laden (PowerDNS-Fehler
-> Status von PowerDNS, Text ``PowerDNS (<server>): …``), P5 nur schreibend: PRESIGNED-Zone -> 409.

Schreibende Endpunkte: ``DbWrite`` (Commit vor der Antwort), Erfolgs-Audit per ``write_audit(zone_name=)``,
Fehler-Audit (detached) bei jedem gescheiterten PowerDNS-Schreibversuch, Webhook nur ueber
``await webhook_outbox.enqueue_event(...)`` mit Katalognamen (B.8). Keine Audits fuer 403/404/409/422.
Schutzregeln (letzter aktiver Schluessel / KSK/CSK / veroeffentlichter KSK/CSK) -> 409 mit
``detail={"message", "code", "force_possible": true}``; ``force`` uebersteuert, ``details.overridden`` nennt sie.
Nach jeder Aenderung: ``after_key_change`` (Serial + NOTIFY bei Master/Producer, ``bump_serial``) [D10].

DNSSEC-Operationen laufen nur gegen den Server aus der URL (kein Fan-out, kein Schluesselabgleich).
"""
from __future__ import annotations

import logging
from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import assert_zone_access, get_current_user
from app.core.database import DbRead, DbWrite
from app.core.names import normalize_zone_name
from app.models.models import User
from app.schemas.dns import MessageResponse
from app.schemas.dnssec import (
    CryptoKeyCreate,
    CryptoKeyUpdate,
    DNSSECDisable,
    DNSSECEnableRequest,
    DnssecStatusResponse,
    Nsec3Update,
)
from app.services import dnssec_logic as logic
from app.services import dnssec_service as svc
from app.services import webhook_outbox
from app.services.audit import write_audit
from app.services.dnssec_parse import compute_key_tag, parse_dnskey_rdata, parse_ds_line
from app.services.fanout import is_server_writable, read_only_error
from app.services.pdns_client import PowerDNSAPIError, PowerDNSClient, pdns_error_text, pdns_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dnssec", tags=["DNSSEC"])

DS_INFO = (
    "Trage beim Registrar den DS mit Digest-Typ 2 (SHA-256) der Schlüssel mit Status 'current' ein. "
    "Mehrere Zeilen pro Schlüssel sind verschiedene Digest-Typen."
)
ENABLE_INFO = "Trage den DS-Record (Digest-Typ 2) beim Registrar ein, um die Vertrauenskette zu schließen."
DISABLE_WARNING = "Entferne die DS-Records beim Registrar, falls noch nicht geschehen."


# =============================================================================================
# Helfer
# =============================================================================================
def _client_or_404(server_name: str) -> PowerDNSClient:
    try:
        return pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _ensure_writable(db, server_name: str) -> None:
    if not await is_server_writable(db, server_name):
        raise read_only_error(server_name)


def _raise_pdns(e: PowerDNSAPIError) -> NoReturn:
    raise HTTPException(status_code=e.status_code, detail=svc.public_pdns_error(e))


async def _load(client, zone_id: str, *, with_version: bool = True) -> svc.ZoneDnssecContext:
    try:
        return await svc.load_context(client, zone_id, with_version=with_version)
    except PowerDNSAPIError as e:
        _raise_pdns(e)


def _ensure_not_presigned(ctx: svc.ZoneDnssecContext, zone_norm: str) -> None:
    if ctx.presigned:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Die Zone '{zone_norm}' ist vorsigniert (PRESIGNED). DNSSEC-Schlüssel werden für solche Zonen "
                "nicht im Panel verwaltet."
            ),
        )


async def _write_context(db, server_name: str, zone_id: str) -> tuple[PowerDNSClient, svc.ZoneDnssecContext]:
    """P2–P5 fuer schreibende Endpunkte (P1 steht als erste Zeile im Endpunkt)."""
    client = _client_or_404(server_name)
    await _ensure_writable(db, server_name)
    ctx = await _load(client, zone_id)
    _ensure_not_presigned(ctx, normalize_zone_name(zone_id))
    return client, ctx


async def _audit(db, action: str, zone_norm: str, server_name: str, user_id: Optional[int], details: dict, *,
                 status: str = "success", error_message: Optional[str] = None):
    return await write_audit(
        db, action, "dnssec_key", zone_norm, user_id=user_id, details=details, status=status,
        error_message=error_message, server_name=server_name, zone_name=zone_norm,
    )


def _protection_error(code: str, key_id: int) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"message": logic.PROTECTION_MESSAGES[code].format(key_id=key_id), "code": code,
                "force_possible": True},
    )


def _key_not_found(key_id: int, zone_norm: str, server_name: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"Schlüssel {key_id} existiert in der Zone '{zone_norm}' auf '{server_name}' nicht.",
    )


def _key_action(key: logic.KeyView, new_active: bool, new_published: bool) -> str:
    active_changed = new_active != key.active
    published_changed = new_published != key.published_eff
    if active_changed and published_changed:
        return "KEY_UPDATE"
    if active_changed:
        return "KEY_ACTIVATE" if new_active else "KEY_DEACTIVATE"
    return "KEY_PUBLISH" if new_published else "KEY_UNPUBLISH"


def _log(action: str, zone_norm: str, server_name: str, key_id, user: User) -> None:
    logger.info("DNSSEC %s zone=%s server=%s key=%s user=%s", action, zone_norm, server_name,
                key_id if key_id is not None else "-", getattr(user, "id", None))


async def _follow_up(db, client, zone_id: str, ctx: svc.ZoneDnssecContext, user: User, bump_serial, trigger: str):
    """Serial-Erhoehung + NOTIFY nach einer erfolgreichen Aenderung [D10] (wirft nie)."""
    # kind aus dem bereits geladenen Kontext ("" = unbekannt -> keine Serial-Erhoehung, kein zweiter Abruf)
    return await svc.after_key_change(db, client, zone_id, bump_serial=bump_serial, kind=ctx.kind or "", user=user,
                                      trigger=trigger)


async def _emit_key_change_events(db, user: User, zone_norm: str, server_name: str, data: dict, *,
                                  activated: Optional[bool], published: Optional[bool], audit_log_id) -> None:
    """Je geaenderter Eigenschaft ein Ereignis (Katalognamen B.8, Literale fuer den Verdrahtungstest)."""
    if activated is True:
        await webhook_outbox.enqueue_event(db, "dnssec.key_activated", actor=user, zone=zone_norm, server=server_name,
                                           data=data, audit_log_id=audit_log_id)
    elif activated is False:
        await webhook_outbox.enqueue_event(db, "dnssec.key_deactivated", actor=user, zone=zone_norm,
                                           server=server_name, data=data, audit_log_id=audit_log_id)
    if published is True:
        await webhook_outbox.enqueue_event(db, "dnssec.key_published", actor=user, zone=zone_norm, server=server_name,
                                           data=data, audit_log_id=audit_log_id)
    elif published is False:
        await webhook_outbox.enqueue_event(db, "dnssec.key_unpublished", actor=user, zone=zone_norm,
                                           server=server_name, data=data, audit_log_id=audit_log_id)


async def _apply_key_change(db, user: User, server_name: str, zone_id: str, key_id: int, *,
                            active: Optional[bool], published: Optional[bool], force: bool, via: str,
                            bump_serial: Optional[bool]) -> MessageResponse:
    """Gemeinsamer Ablauf fuer ``PUT …/keys/{id}`` und die Kompat-POSTs activate/deactivate (P1 im Endpunkt)."""
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    key = ctx.key_by_id(key_id)
    if key is None:
        raise _key_not_found(key_id, zone_norm, server_name)
    if published is not None and published != key.published_eff and ctx.supports_published is False:
        raise HTTPException(status_code=422, detail=svc.published_unsupported_text(server_name, ctx.version))
    new_active = key.active if active is None else bool(active)
    new_published = key.published_eff if published is None else bool(published)
    if (new_active, new_published) == (key.active, key.published_eff):
        return MessageResponse(
            message=f"Schlüssel {key_id}: keine Änderung",
            details={"key_id": key_id, "active": key.active, "published": key.published_eff, "unchanged": True,
                     "force": bool(force), "overridden": [], **svc.no_follow_up()},
        )
    violations = logic.check_key_change(ctx.keys, key_id, active=new_active, published=new_published)
    if violations and not force:
        raise _protection_error(violations[0], key_id)
    action = _key_action(key, new_active, new_published)
    raw = ctx.raw_by_id(key_id)
    key_tag = compute_key_tag(raw.get("dnskey"))
    overridden = list(violations)
    details = {
        "zone": zone_norm, "key_id": key_id, "keytype": key.keytype, "key_tag": key_tag,
        "before": {"active": key.active, "published": key.published_eff},
        "after": {"active": new_active, "published": new_published},
        "force": bool(force), "overridden": overridden, "via": via,
    }
    try:
        await svc.update_key(client, zone_id, key, active=new_active,
                             published=(new_published if new_published != key.published_eff else None), ctx=ctx)
    except PowerDNSAPIError as e:
        await _audit(db, action, zone_norm, server_name, user.id, details, status="error",
                     error_message=pdns_error_text(e))
        _raise_pdns(e)
    audit = await _audit(db, action, zone_norm, server_name, user.id, details)
    follow = await _follow_up(db, client, zone_id, ctx, user, bump_serial, action)
    _log(action, zone_norm, server_name, key_id, user)
    data = {
        "zone": zone_norm, "server": server_name, "key_id": key_id, "keytype": key.keytype,
        "algorithm": raw.get("algorithm"), "active": new_active, "published": new_published, "key_tag": key_tag,
        "before": details["before"], "after": details["after"], "force": bool(force),
        "serial_bumped": follow["serial_bumped"],
    }
    await _emit_key_change_events(
        db, user, zone_norm, server_name, data,
        activated=(new_active if new_active != key.active else None),
        published=(new_published if new_published != key.published_eff else None),
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=f"Schlüssel {key_id} geändert",
        details={"key_id": key_id, "active": new_active, "published": new_published, "unchanged": False,
                 "force": bool(force), "overridden": overridden, **follow},
    )


# =============================================================================================
# Endpunkte (Reihenfolge Spec 3.1; parent-ds folgt mit F4-C)
# =============================================================================================
@router.get("/{server_name}/{zone_id:path}/status", response_model=DnssecStatusResponse)
async def get_dnssec_status(
    server_name: str,
    zone_id: str,
    db: DbRead,
    peers: bool = True,
    history: bool = True,
    current_user: User = Depends(get_current_user),
):
    """DNSSEC-Gesamtstatus (Schluessel, NSEC, Rollover, Peers, Hinweise, Rechte); Lesezugriff genuegt."""
    await assert_zone_access(db, current_user, zone_id)
    client = _client_or_404(server_name)
    ctx = await _load(client, zone_id)
    return await svc.build_status(db, current_user, server_name, zone_id, client, ctx=ctx, peers=peers,
                                  history=history)


@router.get("/{server_name}/{zone_id:path}/ds")
async def get_ds_records(
    server_name: str,
    zone_id: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """DS-Records und signierende Schluessel (Kompatibilitaet fuer Skripte; die UI nutzt ``/status``)."""
    await assert_zone_access(db, current_user, zone_id)
    zone_norm = normalize_zone_name(zone_id)
    client = _client_or_404(server_name)
    try:
        keys = svc._clean_keys(await client.get_cryptokeys(zone_id))
    except PowerDNSAPIError as e:
        _raise_pdns(e)
    views = [logic.key_view_from_pdns(k) for k in keys]
    ds_map = logic.ds_status_map(views)
    ds_records = []
    signing_keys = []
    for key, view in zip(keys, views):
        kid = key.get("id")
        dnskey_str = key.get("dnskey")
        key_tag = compute_key_tag(dnskey_str)
        signing_keys.append({
            "key_id": kid,
            "keytype": key.get("keytype"),
            "active": key.get("active"),
            "published": key.get("published"),
            "algorithm": key.get("algorithm"),
            "bits": key.get("bits"),
            "dnskey": dnskey_str,
            "dnskey_parsed": parse_dnskey_rdata(dnskey_str),
            "key_tag": key_tag,
            "role": "sep" if view.is_sep else "zsk",
        })
        for ds in key.get("ds") or []:
            parsed = parse_ds_line(ds) if isinstance(ds, str) else {"raw": str(ds), "error": "not_string"}
            ds_records.append({
                "key_id": kid,
                "keytype": key.get("keytype"),
                "active": key.get("active"),
                "published": key.get("published"),
                "key_tag": key_tag,
                "ds_status": ds_map.get(view.id),
                "ds": ds,
                "parsed": parsed,
            })
    return {
        "zone": zone_norm,
        "server": server_name,
        "ds_count": len(ds_records),
        "ds_records": ds_records,
        "signing_keys": signing_keys,
        "info": DS_INFO,
    }


@router.get("/{server_name}/{zone_id:path}/keys")
async def list_cryptokeys(
    server_name: str,
    zone_id: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Alle Schluessel der Zone (PowerDNS-Felder ohne ``privatekey`` plus key_tag/role/flags/…)."""
    await assert_zone_access(db, current_user, zone_id)
    zone_norm = normalize_zone_name(zone_id)
    client = _client_or_404(server_name)
    try:
        keys = svc._clean_keys(await client.get_cryptokeys(zone_id))
    except PowerDNSAPIError as e:
        _raise_pdns(e)
    return {
        "zone": zone_norm,
        "server": server_name,
        "key_count": len(keys),
        "keys": [svc.public_key(k) for k in keys],
    }


@router.post("/{server_name}/{zone_id:path}/keys", response_model=MessageResponse)
async def create_cryptokey(
    server_name: str,
    zone_id: str,
    body: CryptoKeyCreate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Schluessel anlegen (z. B. Pre-Publish fuer einen Rollover). Nie Schluesselmaterial annehmen/ausgeben."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    request_details = {
        "zone": zone_norm, "keytype": body.keytype, "algorithm": body.algorithm,
        "algorithm_number": logic.ALGORITHMS[body.algorithm]["number"], "bits": body.bits,
        "active": body.active, "published": body.published,
    }
    try:
        created, warnings = await svc.create_key(client, zone_id, body, ctx)
    except svc.DnssecConflict as e:
        raise HTTPException(status_code=409, detail=e.message)
    except svc.DnssecInputError as e:
        raise HTTPException(status_code=422, detail=e.message)
    except PowerDNSAPIError as e:
        await _audit(db, "KEY_CREATE", zone_norm, server_name, current_user.id, request_details, status="error",
                     error_message=pdns_error_text(e))
        _raise_pdns(e)
    key_id = int(created.get("id") or 0) or None
    key_tag = compute_key_tag(created.get("dnskey"))
    details = {**request_details, "key_id": key_id, "key_tag": key_tag, "warnings": warnings}
    audit = await _audit(db, "KEY_CREATE", zone_norm, server_name, current_user.id, details)
    follow = await _follow_up(db, client, zone_id, ctx, current_user, body.bump_serial, "KEY_CREATE")
    _log("KEY_CREATE", zone_norm, server_name, key_id, current_user)
    await webhook_outbox.enqueue_event(
        db, "dnssec.key_created", actor=current_user, zone=zone_norm, server=server_name,
        data={"zone": zone_norm, "server": server_name, "key_id": key_id, "keytype": body.keytype,
              "algorithm": body.algorithm, "bits": body.bits, "active": body.active, "published": body.published,
              "key_tag": key_tag, "ds": [x for x in (created.get("ds") or []) if isinstance(x, str)],
              "serial_bumped": follow["serial_bumped"]},
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=f"Schlüssel {key_id} für Zone '{zone_norm}' angelegt",
        details={"key": svc.public_key(created), "warnings": warnings, **follow},
    )


@router.post("/{server_name}/{zone_id:path}/enable", response_model=MessageResponse)
async def enable_dnssec(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    config: DNSSECEnableRequest = DNSSECEnableRequest(),
    current_user: User = Depends(get_current_user),
):
    """DNSSEC aktivieren (idempotent): signiert -> 200 ohne Aenderung; nur inaktive Schluessel -> 409."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    try:
        result = await svc.enable_dnssec(client, zone_id, config, ctx)
    except svc.DnssecConflict as e:
        raise HTTPException(status_code=409, detail=e.message)
    except svc.DnssecStepError as e:
        details = {**svc.enable_audit_details(zone_norm, config, [], source="dnssec_enable"),
                   "step": e.step, "rollback": e.rollback}
        await _audit(db, "DNSSEC_ENABLE", zone_norm, server_name, current_user.id, details, status="error",
                     error_message=pdns_error_text(e.exc))
        tail = ("Bereits angelegte Schlüssel wurden wieder entfernt." if e.rollback == "ok"
                else f"Aufräumen fehlgeschlagen ({e.rollback}) – bitte die Schlüssel der Zone prüfen.")
        raise HTTPException(
            status_code=e.exc.status_code,
            detail=f"DNSSEC konnte nicht aktiviert werden ({e.step}): {svc.public_pdns_error(e.exc)}. {tail}",
        )
    keys_out = [svc.public_key(k) for k in result["raw_keys"]]
    if result["already_enabled"]:
        return MessageResponse(
            message=f"DNSSEC ist für Zone '{zone_norm}' bereits aktiv – keine Änderung",
            details={"already_enabled": True, "key": None, "keys": keys_out, "ds_records": result["ds_records"],
                     "nsec3param": result["nsec3param"], "info": ENABLE_INFO, "warnings": [],
                     **svc.no_follow_up()},
        )
    created = result["created"]
    audit = await _audit(db, "DNSSEC_ENABLE", zone_norm, server_name, current_user.id,
                         svc.enable_audit_details(zone_norm, config, created, source="dnssec_enable"))
    follow = await _follow_up(db, client, zone_id, ctx, current_user, config.bump_serial, "DNSSEC_ENABLE")
    _log("DNSSEC_ENABLE", zone_norm, server_name, ",".join(str(k.get("id")) for k in created), current_user)
    await webhook_outbox.enqueue_event(
        db, "dnssec.enabled", actor=current_user, zone=zone_norm, server=server_name,
        data={**svc.enabled_event_data(zone_norm, server_name, config, created),
              "serial_bumped": follow["serial_bumped"]},
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=f"DNSSEC für Zone '{zone_norm}' auf '{server_name}' aktiviert",
        details={"already_enabled": False, "key": svc.public_key(created[0]) if created else None,
                 "keys": [svc.public_key(k) for k in created], "ds_records": result["ds_records"],
                 "nsec3param": result["nsec3param"], "info": ENABLE_INFO, "warnings": result["warnings"],
                 **follow},
    )


@router.post("/{server_name}/{zone_id:path}/disable", response_model=MessageResponse)
async def disable_dnssec(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    config: DNSSECDisable = DNSSECDisable(),
    current_user: User = Depends(get_current_user),
):
    """DNSSEC deaktivieren: NSEC3PARAM entfernen, dann alle Schluessel (inaktive zuerst), Rectify."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    nsec3_before = str(ctx.meta.get("nsec3param") or "")
    base = {"zone": zone_norm, "nsec3param_before": nsec3_before, "force": bool(config.force),
            "parent_ds": "not_checked"}

    def brief(k: logic.KeyView) -> dict:
        raw = ctx.raw_by_id(k.id)
        return {"key_id": k.id, "keytype": k.keytype, "key_tag": compute_key_tag(raw.get("dnskey")),
                "active": k.active}

    try:
        result = await svc.disable_dnssec(client, zone_id, ctx)
    except svc.DnssecPartialError as e:
        deleted = [brief(k) for k in e.deleted]
        await _audit(db, "DNSSEC_DISABLE", zone_norm, server_name, current_user.id,
                     {**base, "step": "delete_key", "deleted_keys": deleted, "failed_key_id": e.failed.id},
                     status="error", error_message=pdns_error_text(e.exc))
        ids = ", ".join(str(k["key_id"]) for k in deleted) or "keine"
        raise HTTPException(
            status_code=e.exc.status_code,
            detail=(f"DNSSEC nur teilweise deaktiviert: Schlüssel {ids} gelöscht, Schlüssel {e.failed.id} "
                    f"fehlgeschlagen: {svc.public_pdns_error(e.exc)}. Bitte erneut versuchen."),
        )
    except PowerDNSAPIError as e:
        await _audit(db, "DNSSEC_DISABLE", zone_norm, server_name, current_user.id,
                     {**base, "step": "nsec3", "deleted_keys": []}, status="error",
                     error_message=pdns_error_text(e))
        _raise_pdns(e)
    if result["already_disabled"]:
        return MessageResponse(
            message=f"DNSSEC für Zone '{zone_norm}' auf '{server_name}' war bereits deaktiviert",
            details={"already_disabled": True, "deleted_keys": [], "warning": DISABLE_WARNING, "rectify_error": None,
                     **svc.no_follow_up()},
        )
    deleted = [brief(k) for k in result["deleted"]]
    audit = await _audit(db, "DNSSEC_DISABLE", zone_norm, server_name, current_user.id,
                         {**base, "deleted_keys": deleted, "rectify_error": result["rectify_error"]})
    follow = await _follow_up(db, client, zone_id, ctx, current_user, config.bump_serial, "DNSSEC_DISABLE")
    _log("DNSSEC_DISABLE", zone_norm, server_name, ",".join(str(k["key_id"]) for k in deleted), current_user)
    await webhook_outbox.enqueue_event(
        db, "dnssec.disabled", actor=current_user, zone=zone_norm, server=server_name,
        data={"zone": zone_norm, "server": server_name, "deleted_key_ids": [k["key_id"] for k in deleted],
              "serial_bumped": follow["serial_bumped"]},
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=f"DNSSEC für Zone '{zone_norm}' auf '{server_name}' deaktiviert",
        details={"already_disabled": False, "deleted_keys": deleted, "warning": DISABLE_WARNING,
                 "rectify_error": result["rectify_error"], **follow},
    )


@router.put("/{server_name}/{zone_id:path}/nsec3", response_model=MessageResponse)
async def update_nsec3(
    server_name: str,
    zone_id: str,
    body: Nsec3Update,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """NSEC/NSEC3 nachtraeglich aendern (setzt ``api_rectify`` und rectifiziert)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    target = {"nsec3param": body.effective_nsec3param(), "nsec3narrow": body.effective_narrow(), "api_rectify": True}
    before = {"nsec3param": str(ctx.meta.get("nsec3param") or ""), "nsec3narrow": bool(ctx.meta.get("nsec3narrow")),
              "api_rectify": ctx.meta.get("api_rectify")}
    try:
        result = await svc.set_nsec(client, zone_id, body, ctx)
    except svc.DnssecConflict as e:
        raise HTTPException(status_code=409, detail=e.message)
    except svc.DnssecStepError as e:
        await _audit(db, "DNSSEC_NSEC3_UPDATE", zone_norm, server_name, current_user.id,
                     {"zone": zone_norm, "before": before, "after": target, "step": "rectify"},
                     status="error", error_message=pdns_error_text(e.exc))
        raise HTTPException(
            status_code=e.exc.status_code,
            detail=(f"NSEC/NSEC3-Einstellung gespeichert, aber Rectify fehlgeschlagen: "
                    f"{svc.public_pdns_error(e.exc)}. Bitte erneut speichern oder "
                    f"'pdnsutil rectify-zone {zone_norm}' ausführen."),
        )
    except PowerDNSAPIError as e:
        await _audit(db, "DNSSEC_NSEC3_UPDATE", zone_norm, server_name, current_user.id,
                     {"zone": zone_norm, "before": before, "after": target, "step": "nsec3"},
                     status="error", error_message=pdns_error_text(e))
        _raise_pdns(e)
    message = f"NSEC-Einstellungen für Zone '{zone_norm}' gespeichert"
    if result["unchanged"]:
        return MessageResponse(
            message=message,
            details={"unchanged": True, "nsec3param": before["nsec3param"], "nsec3narrow": before["nsec3narrow"],
                     "warnings": result["warnings"], **svc.no_follow_up()},
        )
    audit = await _audit(db, "DNSSEC_NSEC3_UPDATE", zone_norm, server_name, current_user.id,
                         {"zone": zone_norm, "before": result["before"], "after": result["after"]})
    follow = await _follow_up(db, client, zone_id, ctx, current_user, body.bump_serial, "DNSSEC_NSEC3_UPDATE")
    _log("DNSSEC_NSEC3_UPDATE", zone_norm, server_name, None, current_user)
    await webhook_outbox.enqueue_event(
        db, "dnssec.nsec3_changed", actor=current_user, zone=zone_norm, server=server_name,
        data={"zone": zone_norm, "server": server_name, "nsec3param": target["nsec3param"] or None,
              "nsec3narrow": target["nsec3narrow"],
              "before": {"nsec3param": result["before"]["nsec3param"] or None,
                         "nsec3narrow": result["before"]["nsec3narrow"]},
              "serial_bumped": follow["serial_bumped"]},
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=message,
        details={"unchanged": False, "nsec3param": target["nsec3param"], "nsec3narrow": target["nsec3narrow"],
                 "warnings": result["warnings"], **follow},
    )


@router.post("/{server_name}/{zone_id:path}/keys/{key_id}/activate", response_model=MessageResponse)
async def activate_key(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    force: bool = False,
    bump_serial: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
):
    """Schluessel aktivieren (Kompatibilitaet; gleiche Logik wie ``PUT …/keys/{id}``)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    return await _apply_key_change(db, current_user, server_name, zone_id, key_id, active=True, published=None,
                                   force=force, via="legacy_post", bump_serial=bump_serial)


@router.post("/{server_name}/{zone_id:path}/keys/{key_id}/deactivate", response_model=MessageResponse)
async def deactivate_key(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    force: bool = False,
    bump_serial: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
):
    """Schluessel deaktivieren (Kompatibilitaet; Schutzregeln, ``?force=true`` uebersteuert)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    return await _apply_key_change(db, current_user, server_name, zone_id, key_id, active=False, published=None,
                                   force=force, via="legacy_post", bump_serial=bump_serial)


@router.get("/{server_name}/{zone_id:path}/keys/{key_id}")
async def get_cryptokey(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Einzelner Schluessel (Schreibrecht noetig; ``privatekey`` wird nie ausgeliefert)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client = _client_or_404(server_name)
    try:
        key = await client.get_cryptokey(zone_id, key_id)
    except PowerDNSAPIError as e:
        _raise_pdns(e)
    return {"zone": zone_norm, "server": server_name, "key": svc.public_key(key if isinstance(key, dict) else {})}


@router.put("/{server_name}/{zone_id:path}/keys/{key_id}", response_model=MessageResponse)
async def update_cryptokey(
    server_name: str,
    zone_id: str,
    key_id: int,
    body: CryptoKeyUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """``active``/``published`` eines Schluessels aendern; Schutzregeln -> 409, ``force`` uebersteuert."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    return await _apply_key_change(db, current_user, server_name, zone_id, key_id, active=body.active,
                                   published=body.published, force=body.force, via="put",
                                   bump_serial=body.bump_serial)


@router.delete("/{server_name}/{zone_id:path}/keys/{key_id}", response_model=MessageResponse)
async def delete_cryptokey(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    force: bool = False,
    bump_serial: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
):
    """Schluessel loeschen; der letzte aktive (KSK/CSK) ist geschuetzt (``?force=true`` uebersteuert)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = normalize_zone_name(zone_id)
    client, ctx = await _write_context(db, server_name, zone_id)
    key = ctx.key_by_id(key_id)
    if key is None:
        raise _key_not_found(key_id, zone_norm, server_name)
    violations = logic.check_key_change(ctx.keys, key_id, delete=True)
    if violations and not force:
        raise _protection_error(violations[0], key_id)
    raw = ctx.raw_by_id(key_id)
    key_tag = compute_key_tag(raw.get("dnskey"))
    details = {
        "zone": zone_norm, "key_id": key_id, "keytype": key.keytype, "key_tag": key_tag,
        "algorithm": raw.get("algorithm"), "was_active": key.active, "was_published": key.published_eff,
        "force": bool(force), "overridden": list(violations),
    }
    try:
        await client.delete_cryptokey(zone_id, key_id)
    except PowerDNSAPIError as e:
        await _audit(db, "KEY_DELETE", zone_norm, server_name, current_user.id, details, status="error",
                     error_message=pdns_error_text(e))
        _raise_pdns(e)
    audit = await _audit(db, "KEY_DELETE", zone_norm, server_name, current_user.id, details)
    follow = await _follow_up(db, client, zone_id, ctx, current_user, bump_serial, "KEY_DELETE")
    _log("KEY_DELETE", zone_norm, server_name, key_id, current_user)
    await webhook_outbox.enqueue_event(
        db, "dnssec.key_deleted", actor=current_user, zone=zone_norm, server=server_name,
        data={"zone": zone_norm, "server": server_name, "key_id": key_id, "key_tag": key_tag,
              "keytype": key.keytype, "algorithm": raw.get("algorithm"), "force": bool(force),
              "serial_bumped": follow["serial_bumped"]},
        audit_log_id=audit.id if audit else None,
    )
    return MessageResponse(
        message=f"Schlüssel {key_id} aus Zone '{zone_norm}' gelöscht",
        details={"key_id": key_id, "force": bool(force), "overridden": list(violations), **follow},
    )
