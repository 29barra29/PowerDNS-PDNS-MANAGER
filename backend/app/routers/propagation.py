"""Propagations-Check einer Zone (F12 3.1, Bauplan B.13: eigener Router, ROUTER_ORDER 55, Prefix ``/zones``).

``GET /api/v1/zones/{server_name}/{zone_id:path}/propagation`` – Lesezugriff auf die Zone genuegt (auch
Read-only-Benutzer und Panel-Tokens mit Zonen-Scope). Kein Audit, kein Webhook (reiner Lesezugriff); Metrik
``pdnsmgr_propagation_checks_total{result}``. Der Router liegt vor ``routers/zones.py`` (Ordnung 60), damit keine
generische Zonenroute den Suffix-Pfad verdeckt.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core import metrics as prom
from app.core.auth import assert_zone_access, get_current_user, is_effective_admin
from app.core.database import DbRead
from app.core.names import normalize_zone_name
from app.models.models import User
from app.schemas.propagation import PropagationResponse
from app.services.pdns_client import pdns_manager

ROUTER_ORDER = 55

router = APIRouter(prefix="/zones", tags=["Propagation"])

MSG_NAME_AND_TYPE = "Bei einem Record-Vergleich sind Name und Typ erforderlich."


def _reference_error(exc, server_name: str, zone_norm: str, is_admin: bool) -> HTTPException:
    kind = exc.kind
    if kind == "zone_missing":
        return HTTPException(404, f"Zone {zone_norm} wurde auf Server {server_name} nicht gefunden.")
    if kind == "unreachable":
        return HTTPException(503, f"Referenz-Server {server_name} ist nicht erreichbar.")
    if kind == "timeout":
        return HTTPException(504, f"Referenz-Server {server_name} hat nicht rechtzeitig geantwortet.")
    msg = exc.pdns_message if is_admin else ""
    if msg:
        return HTTPException(502, f"Referenz-Server {server_name} meldet einen Fehler: {msg[:300]}")
    return HTTPException(502, f"Referenz-Server {server_name} meldet einen Fehler.")


@router.get("/{server_name}/{zone_id:path}/propagation", response_model=PropagationResponse)
async def zone_propagation(
    server_name: str,
    zone_id: str,
    db: DbRead,
    name: Optional[str] = Query(None, max_length=255),
    rtype: Optional[str] = Query(None, alias="type", max_length=16),
    content: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """Serial-/Record-/Inhaltsvergleich der Zone ueber Panel-Server, autoritative Nameserver und Resolver."""
    await assert_zone_access(db, current_user, zone_id)
    from app.services import fanout
    from app.services import propagation as prop

    zone_norm = normalize_zone_name(zone_id)
    name = (name or "").strip() or None
    rtype = (rtype or "").strip().upper() or None
    if bool(name) != bool(rtype):
        raise HTTPException(422, MSG_NAME_AND_TYPE)
    fqdn = None
    if name:
        try:
            fqdn = prop.resolve_record_name(name, zone_norm)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if rtype not in prop.COMPARABLE_TYPES:
            raise HTTPException(422, f"Der Typ {rtype[:16]} kann nicht verglichen werden.")

    if server_name not in pdns_manager.clients:
        if server_name in (getattr(pdns_manager, "unloaded", {}) or {}):
            raise HTTPException(
                503, f"Referenz-Server {server_name} ist nicht geladen (API-Key fehlt oder ist nicht lesbar)."
            )
        raise HTTPException(404, f"Server '{server_name}' ist nicht konfiguriert.")

    is_admin = is_effective_admin(current_user)
    cfg = await prop.load_settings(db)
    writable = await fanout.writable_server_names(db)
    try:
        return await prop.check_zone(
            user_id=current_user.id, is_admin=is_admin, server_name=server_name, zone_norm=zone_norm,
            record_fqdn=fqdn, rtype=rtype if fqdn else None, compare_content_flag=content, settings=cfg,
            writable_servers=writable,
        )
    except prop.PropagationRateLimited as exc:
        prom.record_propagation("rate_limited")
        raise HTTPException(
            429, f"Zu viele Propagations-Prüfungen – bitte in {exc.retry_after} Sekunden erneut versuchen.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except prop.PropagationReferenceError as exc:
        raise _reference_error(exc, server_name, zone_norm, is_admin) from None
    except ValueError:
        # Server wurde zwischen Pruefung und Check entfernt
        raise HTTPException(404, f"Server '{server_name}' ist nicht konfiguriert.") from None
