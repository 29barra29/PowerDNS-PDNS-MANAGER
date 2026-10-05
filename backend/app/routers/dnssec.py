"""API routes for DNSSEC management.

Erfolgs-Audits ueber ``write_audit`` (``zone_name`` = Zone, Commit vor der Antwort per ``DbWrite``),
Webhook-Ereignisse ``dnssec.*`` ueber die Outbox (``await enqueue_event``). F4 baut die Endpunkte in
Welle 1/2 aus (Schluesselverwaltung, NSEC3, Rollover).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, assert_zone_access
from app.core.database import DbRead, DbWrite
from app.models.models import AuditLog, User
from app.schemas.dns import DNSSECEnable, MessageResponse  # CryptoKeyResponse currently unused
from app.services.audit import write_audit
from app.services.dnssec_parse import parse_ds_line, parse_dnskey_rdata
from app.services.pdns_client import pdns_manager, PowerDNSAPIError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dnssec", tags=["DNSSEC"])


def _strip_private(key):
    """Entfernt den privaten Schluessel aus PowerDNS-Cryptokey-Antworten.

    Der Einzel-GET /cryptokeys/{id} liefert 'privatekey' im Klartext (ISC-Format).
    Der Signaturschluessel der Zone darf das Panel nie verlassen.
    """
    if isinstance(key, dict):
        return {k: v for k, v in key.items() if k != "privatekey"}
    if isinstance(key, list):
        return [_strip_private(k) for k in key]
    return key


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None,
    user_id: int = None,
) -> Optional[AuditLog]:
    """Audit-Eintrag (resource_type ``dnssec_key``, ``zone_name`` = Zone) ueber ``write_audit``.

    Erfolg: Eintrag in der Request-Session (Rueckgabe mit ``id``); Fehler: eigene Session, Rueckgabe ``None``.
    """
    return await write_audit(
        db, action, "dnssec_key", resource_name, user_id=user_id, details=details, status=status,
        error_message=error_message, server_name=server_name, zone_name=resource_name,
    )


@router.get("/{server_name}/{zone_id:path}/keys")
async def list_cryptokeys(
    server_name: str,
    zone_id: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """List all DNSSEC keys for a zone (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        keys = _strip_private(await client.get_cryptokeys(zone_id))
        return {
            "zone": zone_id,
            "server": server_name,
            "key_count": len(keys),
            "keys": keys,
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/{zone_id:path}/keys/{key_id}")
async def get_cryptokey(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Get a specific DNSSEC key (Auth + Zone-Schreibrecht). privatekey wird nie ausgeliefert."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        key = _strip_private(await client.get_cryptokey(zone_id, key_id))
        return {
            "zone": zone_id,
            "server": server_name,
            "key": key,
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/enable", response_model=MessageResponse)
async def enable_dnssec(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    config: DNSSECEnable = DNSSECEnable(),
    current_user: User = Depends(get_current_user),
):
    """Enable DNSSEC for a zone (Auth + Zone-ACL).

    Creates a CSK (Combined Signing Key), sets NSEC3 parameters and rectifies the zone.
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        result = await client.enable_dnssec(
            zone_id,
            algorithm=config.algorithm,
            nsec3param=config.nsec3param,
        )

        keys = _strip_private(await client.get_cryptokeys(zone_id))
        ds_records = []
        for key in keys:
            if key.get("ds"):
                ds_records.extend(key["ds"])

        audit = await _log_action(db, "DNSSEC_ENABLE", zone_id, server_name, {
            "algorithm": config.algorithm,
            "nsec3param": config.nsec3param,
            "key_id": result.get("id") if isinstance(result, dict) else None,
        }, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "dnssec.enabled", actor=current_user, zone=zone_id, server=server_name,
            data={
                "zone": zone_id,
                "server": server_name,
                "algorithm": config.algorithm,
                "nsec3param": config.nsec3param or None,
                # ohne privatekey (keys ist bereits durch _strip_private gelaufen)
                "keys": [
                    {
                        "id": k.get("id"),
                        "keytype": k.get("keytype"),
                        "algorithm": k.get("algorithm"),
                        "active": k.get("active"),
                        "published": k.get("published"),
                        "ds": list(k.get("ds") or []),
                    }
                    for k in keys if isinstance(k, dict)
                ],
            },
            audit_log_id=audit.id if audit else None,
        )

        return MessageResponse(
            message=f"DNSSEC enabled for zone '{zone_id}' on '{server_name}'",
            details={
                "key": _strip_private(result),
                "ds_records": ds_records,
                "info": "Add the DS records to your domain registrar to complete DNSSEC setup.",
            },
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "DNSSEC_ENABLE", zone_id, server_name,
            status="error", error_message=e.detail,
            user_id=current_user.id,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/disable", response_model=MessageResponse)
async def disable_dnssec(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Disable DNSSEC for a zone (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        await client.disable_dnssec(zone_id)

        audit = await _log_action(db, "DNSSEC_DISABLE", zone_id, server_name, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "dnssec.disabled", actor=current_user, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name},
            audit_log_id=audit.id if audit else None,
        )

        return MessageResponse(
            message=f"DNSSEC disabled for zone '{zone_id}' on '{server_name}'",
            details={
                "warning": "Remember to remove the DS records from your domain registrar!",
            },
        )
    except PowerDNSAPIError as e:
        await _log_action(
            db, "DNSSEC_DISABLE", zone_id, server_name,
            status="error", error_message=e.detail,
            user_id=current_user.id,
        )
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/keys/{key_id}/activate", response_model=MessageResponse)
async def activate_key(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Activate a DNSSEC key (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
        await client.activate_cryptokey(zone_id, key_id)

        audit = await _log_action(db, "KEY_ACTIVATE", zone_id, server_name, {"key_id": key_id}, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "dnssec.key_activated", actor=current_user, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name, "key_id": key_id},
            audit_log_id=audit.id if audit else None,
        )

        return MessageResponse(
            message=f"Key {key_id} activated for zone '{zone_id}'"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{server_name}/{zone_id:path}/keys/{key_id}/deactivate", response_model=MessageResponse)
async def deactivate_key(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Deactivate a DNSSEC key (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
        await client.deactivate_cryptokey(zone_id, key_id)

        audit = await _log_action(db, "KEY_DEACTIVATE", zone_id, server_name, {"key_id": key_id}, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "dnssec.key_deactivated", actor=current_user, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name, "key_id": key_id},
            audit_log_id=audit.id if audit else None,
        )

        return MessageResponse(
            message=f"Key {key_id} deactivated for zone '{zone_id}'"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete("/{server_name}/{zone_id:path}/keys/{key_id}", response_model=MessageResponse)
async def delete_key(
    server_name: str,
    zone_id: str,
    key_id: int,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Delete a DNSSEC key (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
        await client.delete_cryptokey(zone_id, key_id)

        audit = await _log_action(db, "KEY_DELETE", zone_id, server_name, {"key_id": key_id}, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "dnssec.key_deleted", actor=current_user, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name, "key_id": key_id},
            audit_log_id=audit.id if audit else None,
        )

        return MessageResponse(
            message=f"Key {key_id} deleted from zone '{zone_id}'"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/{zone_id:path}/ds")
async def get_ds_records(
    server_name: str,
    zone_id: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Get DS records for a zone (Auth + Zone-ACL)."""
    await assert_zone_access(db, current_user, zone_id)
    try:
        client = pdns_manager.get_client(server_name)
        keys = _strip_private(await client.get_cryptokeys(zone_id))

        ds_records = []
        signing_keys = []
        for key in keys:
            kid = key.get("id")
            dnskey_str = key.get("dnskey")
            signing_keys.append({
                "key_id": kid,
                "keytype": key.get("keytype"),
                "active": key.get("active"),
                "algorithm": key.get("algorithm"),  # z. B. "ECDSAP256SHA256" laut PowerDNS
                "bits": key.get("bits"),
                "dnskey": dnskey_str,
                "dnskey_parsed": parse_dnskey_rdata(dnskey_str),
            })
            if key.get("ds"):
                for ds in key["ds"]:
                    p = parse_ds_line(ds) if isinstance(ds, str) else {"raw": str(ds), "error": "not_string"}
                    ds_records.append({
                        "key_id": kid,
                        "keytype": key.get("keytype"),
                        "active": key.get("active"),
                        "ds": ds,
                        "parsed": p,
                    })

        return {
            "zone": zone_id,
            "server": server_name,
            "ds_count": len(ds_records),
            "ds_records": ds_records,
            "signing_keys": signing_keys,
            "info": "Add these DS records to your domain registrar. Multiple DS lines per key are different digest algorithms (1=SHA-1, 2=SHA-256, 4=SHA-384) — use digest type 2 at the registrar if only one is allowed.",
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
