"""API routes for zone management.

Zonen anlegen/loeschen/importieren sind Admin-Funktionen (``get_admin_user``, Token nur mit ``allow_admin``)
und pruefen zusaetzlich den Zonen-Scope eines Panel-Tokens (``assert_token_scope``, F14 5.6). Erfolgs-Audits
laufen in der Request-Session (``DbWrite``: Commit vor der Antwort), Webhook-Ereignisse ueber die Outbox
(``await enqueue_event``), danach wird der Zonen-Index fuer DynDNS/PTR verworfen (``zone_index.invalidate``).

NOTIFY und Export (F2) werden auditiert (``ZONE_NOTIFY``, ``ZONE_EXPORT``); PowerDNS-Fehler kommen als lesbare,
deutsche Texte zurueck. Nach dem Anlegen setzt ``_update_zone_soa`` nur mname/rname des SOA (F0, [D13]).

DNSSEC beim Anlegen (F4 5.7, Plan [F7]): ``dnssec_service.enable_dnssec_on_new_zone`` nur auf dem **ersten** Server,
auf dem die Zone tatsaechlich angelegt wurde (``created``). Weitere angelegte Server haben eine eigene Datenbank und
bekommen ``created; dnssec-skipped`` (keine abweichenden Schluessel je Server); bei ``synced`` (409, gemeinsame
Datenbank) passiert nichts. Scheitert DNSSEC, bleibt die Zone angelegt: ``created; dnssec-error: <text>``.

LUA beim Import (F15 3.6/5.8): Die Vorschau versteht LUA-/ALIAS-Zeilen (``zone_import_diff``) und meldet
``lua_count``/``lua_issues`` sowie ``lua_policy``/``lua_blocked``. Bei Policy ``disabled`` lehnt ``import_zone``
Dateien mit LUA-Records vor jedem PowerDNS-Zugriff mit 403 ab (Fehler-Audit ``IMPORT`` mit ``lua_count``).
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete as sql_delete

from app.core.auth import (
    _normalize_zone_name,
    assert_token_scope,
    assert_zone_access,
    effective_zone_filter,
    get_admin_user,
    get_current_user,
    is_effective_admin,
)
from app.core.database import DbRead, DbWrite
from app.services import dnssec_service, fanout, zone_index
from app.services.audit import write_audit
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.schemas.dns import (
    ZoneCreate, ZoneUpdate, ZoneResponse, ZoneListResponse,
    ZoneImport, MessageResponse,
)
from app.schemas.dnssec import DNSSECEnable
from app.models.models import AuditLog, User, UserZoneAccess, ServerConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zones", tags=["Zones"])


async def _log_action(
    db: AsyncSession, action: str, resource_name: str,
    server_name: str = None, details: dict = None,
    status: str = "success", error_message: str = None,
    user_id: int = None,
) -> Optional[AuditLog]:
    """Audit-Eintrag (resource_type ``zone``, ``zone_name`` = Zone) ueber ``write_audit``.

    Erfolg: Eintrag in der Request-Session (Rueckgabe mit ``id``); Fehler: eigene Session
    (``write_audit_detached``), Rueckgabe ``None``.
    """
    return await write_audit(
        db, action, "zone", resource_name, user_id=user_id, details=details, status=status,
        error_message=error_message, server_name=server_name, zone_name=resource_name,
    )


@router.get("/{server_name}", response_model=ZoneListResponse)
async def list_zones(
    server_name: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """List zones on a server. Sichtbar: Zonenrechte des Benutzers, bei Panel-Token geschnitten mit dem Scope."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    allowed_zones = await effective_zone_filter(db, current_user)

    try:
        zones_data = await client.list_zones()
        zones = []
        for z in zones_data:
            zone_name = z.get("name", "")

            if allowed_zones is not None and _normalize_zone_name(zone_name) not in allowed_zones:
                continue

            zones.append(ZoneResponse(
                id=z.get("id", zone_name),
                name=zone_name,
                kind=z.get("kind", ""),
                serial=z.get("serial", 0),
                edited_serial=z.get("edited_serial"),
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
    db: DbRead,
    current_user: User = Depends(get_current_user),
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


# Fallback-Timer, falls der gelesene SOA nicht parsebar ist (Werte wie 2.4.1 / PowerDNS-Default)
_SOA_DEFAULT_TIMERS = ("10800", "3600", "604800", "3600")


def _abs_name(name: str) -> str:
    n = (name or "").strip()
    return n if n.endswith(".") else n + "."


def build_created_zone_soa(current_content: Optional[str], zone_name: str, nameservers, *, today=None) -> Optional[str]:
    """SOA-Inhalt nach dem Anlegen einer Zone (F0, Bauplan [D13]).

    - mname = erster Nameserver, rname = ``hostmaster.<zone>.`` (2.4.1 setzte bei >= 2 Nameservern faelschlich
      den zweiten Nameserver als Hostmaster-Mailbox).
    - Serial des gelesenen SOA bleibt erhalten (PowerDNS hat ihn per SOA-EDIT-API ggf. schon gesetzt); nur ein
      Serial ``0`` (oder ein unlesbarer SOA) wird zu ``YYYYMMDD01``.
    - Refresh/Retry/Expire/Minimum bleiben wie gelesen.
    ``None``, wenn keine Nameserver angegeben sind (dann bleibt der SOA von PowerDNS unveraendert).
    """
    ns = [n for n in (nameservers or []) if str(n or "").strip()]
    if not ns:
        return None
    mname = _abs_name(str(ns[0]))
    rname = f"hostmaster.{zone_name.strip().rstrip('.').lower()}."
    parts = (current_content or "").split()
    serial = 0
    timers = _SOA_DEFAULT_TIMERS
    if len(parts) >= 7:
        try:
            serial = int(parts[2])
        except ValueError:
            serial = 0
        timers = tuple(parts[3:7])
    if serial <= 0:
        serial = int((today or datetime.now()).strftime("%Y%m%d") + "01")
    return f"{mname} {rname} {serial} {' '.join(timers)}"


async def _update_zone_soa(client, server_name: str, zone_name: str, zone_data: ZoneCreate):
    """SOA nach dem Anlegen setzen (mname/rname, F0 [D13]). Fehler werden nur geloggt (die Zone existiert ja).

    Der DNSSEC-Teil (2.4.1: ``client.enable_dnssec``) ist entfallen; DNSSEC richtet ``create_zone`` ueber
    ``dnssec_service.enable_dnssec_on_new_zone`` ein (F4 5.7).
    """
    try:
        zone_details = await client.get_zone(zone_name)
        soa_rrset = next((rr for rr in zone_details.get("rrsets", []) if rr.get("type") == "SOA"), None)
        if soa_rrset:
            records = soa_rrset.get("records") or []
            current = records[0].get("content") if records else None
            new_soa_content = build_created_zone_soa(current, zone_name, zone_data.nameservers)
            if new_soa_content and new_soa_content != current:
                await client.add_record(
                    zone_id=zone_name, name=soa_rrset.get("name") or zone_name, record_type="SOA",
                    content=[new_soa_content], ttl=int(soa_rrset.get("ttl") or 3600),
                )
    except Exception as e:
        logger.warning(f"Failed to update SOA for {zone_name} on {server_name}: {e}")


# Ergebnis-Werte je Server bei DNSSEC beim Anlegen (UI-Vertrag ZonesPage, F4 2.10)
DNSSEC_ERROR_PREFIX = "created; dnssec-error: "
DNSSEC_SKIPPED = "created; dnssec-skipped"


def _dnssec_create_details(opts: DNSSECEnable, dnssec_server: Optional[str], *, skipped: bool = False) -> dict:
    """``details.dnssec`` des Zonen-``CREATE``-Audits (F4 5.7): Optionen ohne das Legacy-Feld ``nsec3param``."""
    out = {"enabled": True, "server": dnssec_server, "options": opts.model_dump(exclude={"nsec3param"})}
    if skipped:
        out["skipped"] = True
    return out


def _allow_writes_column():
    """Spalte allow_writes kann bei alter DB fehlen."""
    return getattr(ServerConfig, "allow_writes", None)


@router.post("", response_model=MessageResponse)
async def create_zone(
    zone_data: ZoneCreate,
    db: DbWrite,
    admin: User = Depends(get_admin_user),
):
    """Create a new zone (Admin only). Only servers with allow_writes=True are used."""
    assert_token_scope(zone_data.name, write=True)
    results = {}
    if zone_data.servers:
        # F0: auch explizit gewaehlte Server muessen Schreiben erlauben ("Auf diesem Server speichern")
        aw = await fanout.allow_writes_map(db)
        requested = list(dict.fromkeys(zone_data.servers))
        target_servers = [name for name in requested if aw.get(name, True)]
        read_only = [name for name in requested if not aw.get(name, True)]
        if read_only and not target_servers:
            if len(read_only) == 1:
                raise fanout.read_only_error(read_only[0])
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Die gewählten Server ({', '.join(read_only)}) sind auf „Speichern: Nein“ gesetzt. "
                    "In Einstellungen → DNS-Server „Auf diesem Server speichern“ aktivieren."
                ),
            )
        for name in read_only:
            results[name] = "skipped (read-only)"
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
    zone_name = zone_data.name
    payload = {
        "name": zone_name,
        "kind": zone_data.kind,
        "nameservers": zone_data.nameservers,
        "soa_edit_api": zone_data.soa_edit_api,
    }
    if zone_data.masters:
        payload["masters"] = zone_data.masters

    dnssec_opts: Optional[DNSSECEnable] = None
    if zone_data.enable_dnssec:
        dnssec_opts = zone_data.dnssec_options or DNSSECEnable()
    # Erster Server, auf dem die Zone tatsaechlich angelegt wurde -> dort (und nur dort) DNSSEC (F4 5.7)
    dnssec_server: Optional[str] = None
    dnssec_client = None
    dnssec_skipped: list[str] = []

    first_audit: Optional[AuditLog] = None
    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            await client.create_zone(payload)
            await _update_zone_soa(client, server_name, zone_name, zone_data)
            results[server_name] = "created"
            dnssec_details = False
            if dnssec_opts is not None:
                if dnssec_server is None:
                    dnssec_server, dnssec_client = server_name, client
                    dnssec_details = _dnssec_create_details(dnssec_opts, dnssec_server)
                else:
                    # eigene Datenbank: keine zweiten, abweichenden Schluessel (Abgleich manuell, Doku Multi-Server)
                    dnssec_skipped.append(server_name)
                    dnssec_details = _dnssec_create_details(dnssec_opts, dnssec_server, skipped=True)
            audit = await _log_action(db, "CREATE", zone_name, server_name, {
                "kind": zone_data.kind,
                "nameservers": zone_data.nameservers,
                "dnssec": dnssec_details,
            }, user_id=admin.id)
            first_audit = first_audit or audit
        except PowerDNSAPIError as e:
            if e.status_code == 409 or "already exists" in (e.detail or "").lower() or "Conflict" in (e.detail or ""):
                results[server_name] = "synced"
                audit = await _log_action(db, "CREATE", zone_name, server_name,
                                          {"action": "synced (zone already present)"},
                                          user_id=admin.id)
                first_audit = first_audit or audit
            else:
                results[server_name] = f"error: {e.detail}"
                await _log_action(db, "CREATE", zone_name, server_name,
                                  status="error", error_message=e.detail,
                                  user_id=admin.id)
        except ValueError as e:
            results[server_name] = f"error: {str(e)}"

    if any(v in ("created", "synced") for v in results.values()):
        zone_index.invalidate()
        from app.services.webhook_outbox import enqueue_event
        # zone.created vor dnssec.enabled (Reihenfolge der Ereignisse fuer Empfaenger)
        await enqueue_event(
            db, "zone.created", actor=admin, zone=zone_name,
            data={
                "zone": zone_name,
                "kind": zone_data.kind,
                "nameservers": list(zone_data.nameservers),
                "dnssec": bool(zone_data.enable_dnssec),
                "results": dict(results),
            },
            audit_log_id=first_audit.id if first_audit else None,
        )

    if dnssec_server is not None:
        # Audit DNSSEC_ENABLE (source=zone_create) und Ereignis dnssec.enabled bzw. Fehler-Audit schreibt der Dienst
        dnssec_error = await dnssec_service.enable_dnssec_on_new_zone(
            db, dnssec_client, dnssec_server, zone_name, dnssec_opts, admin,
        )
        if dnssec_error is not None:
            results[dnssec_server] = f"{DNSSEC_ERROR_PREFIX}{dnssec_error}"
        for server_name in dnssec_skipped:
            results[server_name] = DNSSEC_SKIPPED

    return MessageResponse(
        message=f"Zone '{zone_name}' creation completed",
        details=results,
    )


@router.put("/{server_name}/{zone_id:path}", response_model=MessageResponse)
async def update_zone(
    server_name: str,
    zone_id: str,
    zone_data: ZoneUpdate,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Update zone metadata (Auth + Zone-ACL; kind/masters/account nur Admin)."""
    await assert_zone_access(db, current_user, zone_id, write=True)
    # Replikationsart, Master-Liste und Account veraendern, wer die Zone kontrolliert
    # (AXFR von fremden Mastern, Umgehung des Panel-Audits) – das bleibt Admins vorbehalten.
    # Panel-Token ohne allow_admin zaehlt hier nicht als Admin (F14 3.10).
    if not is_effective_admin(current_user) and any(
        getattr(zone_data, f) is not None for f in ("kind", "masters", "account")
    ):
        raise HTTPException(status_code=403, detail="kind, masters und account darf nur ein Admin aendern")
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        update_data = zone_data.model_dump(exclude_none=True)
        await client.update_zone(zone_id, update_data)

        audit = await _log_action(db, "UPDATE", zone_id, server_name, update_data, user_id=current_user.id)
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "zone.updated", actor=current_user, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name, "changed": dict(update_data)},
            audit_log_id=audit.id if audit else None,
        )

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
    db: DbWrite,
    admin: User = Depends(get_admin_user),
):
    """Delete a zone (Admin only)."""
    assert_token_scope(zone_id, write=True)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        await client.delete_zone(zone_id)

        # Zonenrechte mit entfernen: sonst haette ein frueherer Nutzer bei einer spaeter neu
        # angelegten Zone gleichen Namens sofort wieder Zugriff (Zombie-ACL).
        zname = _normalize_zone_name(zone_id)
        # Nur aufraeumen, wenn kein anderer aktiver Server die Zone noch fuehrt
        # (Mixed-Setups: nicht jeder Server hostet jede Zone).
        still_exists = False
        for other in pdns_manager.list_servers():
            if other == server_name:
                continue
            try:
                await pdns_manager.get_client(other).get_zone(zone_id)
                still_exists = True
                break
            except PowerDNSAPIError as exc:
                # Nur ein echtes "nicht vorhanden" zaehlt; 5xx/Timeouts (Peer nicht erreichbar)
                # -> Rechte lieber behalten.
                if exc.status_code == 404 or "could not find domain" in (exc.detail or "").lower():
                    continue
                still_exists = True
                break
            except Exception:  # noqa: BLE001 - nicht erreichbar: lieber Rechte behalten
                still_exists = True
                break
        # Ereignis VOR dem Entfernen der Zonenrechte einreihen: Empfaenger mit scope="zones" werden ueber
        # UserZoneAccess ermittelt (F6 3.9); der Audit-Eintrag entsteht erst danach (audit_log_id=None).
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "zone.deleted", actor=admin, zone=zone_id, server=server_name,
            data={"zone": zone_id, "server": server_name, "zone_still_on_other_server": still_exists},
        )
        removed = None
        pruned = 0
        if not still_exists:
            removed = await db.execute(sql_delete(UserZoneAccess).where(UserZoneAccess.zone_name == zname))
            # Zone aus den Scopes der Panel-Tokens entfernen (F14 3.10)
            from app.services import panel_token as ptk
            pruned = await ptk.remove_zone_from_scopes(db, zname)
        await _log_action(
            db, "DELETE", zone_id, server_name,
            {"removed_zone_access_rows": int(getattr(removed, "rowcount", 0) or 0) if removed is not None else 0,
             "zone_still_on_other_server": still_exists,
             "pruned_panel_token_scopes": int(pruned or 0)},
            user_id=admin.id,
        )
        zone_index.invalidate()

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


# Gleicher Text wie main.pdns_error_handler (keine PowerDNS-Interna bei 5xx)
_PDNS_UNAVAILABLE = "PowerDNS-Server ist derzeit nicht erreichbar. Bitte Server-Konfiguration und Erreichbarkeit prüfen."
_NOTIFY_HINT = (
    "NOTIFY funktioniert nur für Zonen vom Typ Master oder Producer (Slave nur mit secondary-do-renotify) "
    "und wenn auf dem PowerDNS-Server primary=yes gesetzt ist."
)


def _export_filename(zone_norm: str) -> str:
    """Dateiname fuer den Zonen-Export: ``<zone ohne Punkt>.txt``, nur ``[a-z0-9._-]`` (Rest -> ``_``)."""
    base = (zone_norm or "").rstrip(".") or "zone"
    return re.sub(r"[^a-z0-9._-]", "_", base.lower()) + ".txt"


def _export_content(raw) -> str:
    """PowerDNS liefert den Export als Text; aeltere/abweichende Versionen als ``{"zone": "..."}``."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("zone"), str):
        return raw["zone"]
    return ""


@router.post("/{server_name}/{zone_id:path}/notify", response_model=MessageResponse)
async def notify_zone(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """DNS NOTIFY fuer die Zone vom angegebenen Server aus (Auth + Zone-ACL mit Schreibrecht, F2 3.1.1).

    Kein Fan-out (F3 E3) und keine allow_writes-Pruefung (E4: NOTIFY aendert keine Daten). Auditiert als
    ``ZONE_NOTIFY`` (Fehler detached mit ``status_code``).
    """
    await assert_zone_access(db, current_user, zone_id, write=True)
    zone_norm = _normalize_zone_name(zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        await client.notify_zone(zone_id)
    except PowerDNSAPIError as e:
        msg = e.pdns_message
        await _log_action(
            db, "ZONE_NOTIFY", zone_norm, server_name,
            details={"server": server_name, "status_code": e.status_code},
            status="error", error_message=msg[:500], user_id=current_user.id,
        )
        if e.status_code in (400, 422):
            raise HTTPException(status_code=422, detail=f"NOTIFY fehlgeschlagen: {msg}. {_NOTIFY_HINT}")
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Zone '{zone_norm}' existiert auf '{server_name}' nicht")
        if e.status_code >= 500:
            raise HTTPException(status_code=e.status_code, detail=_PDNS_UNAVAILABLE)
        raise HTTPException(status_code=e.status_code, detail=msg)
    await _log_action(db, "ZONE_NOTIFY", zone_norm, server_name, details={"server": server_name},
                      user_id=current_user.id)
    return MessageResponse(
        message=f"NOTIFY für Zone '{zone_norm}' auf '{server_name}' ausgelöst",
        details={"server": server_name},
    )


@router.get("/{server_name}/{zone_id:path}/export")
async def export_zone(
    server_name: str,
    zone_id: str,
    db: DbWrite,
    current_user: User = Depends(get_current_user),
):
    """Zone als BIND-Zonendatei (Auth + Zone-ACL, Leserecht genuegt; F2 3.1.2).

    Antwort wie 2.4.1 plus ``filename``. Ein erfolgreicher Export ist ein vollstaendiger Datenabzug und wird als
    ``ZONE_EXPORT`` auditiert (nur Groesse/Zeilen). ``DbWrite``, damit der Audit-Eintrag vor der Antwort committet ist.
    """
    await assert_zone_access(db, current_user, zone_id)
    zone_norm = _normalize_zone_name(zone_id)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        content = _export_content(await client.get_zone_axfr(zone_id))
    except PowerDNSAPIError as e:
        if e.status_code >= 500:
            raise HTTPException(status_code=e.status_code, detail=_PDNS_UNAVAILABLE)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    await _log_action(
        db, "ZONE_EXPORT", zone_norm, server_name,
        details={"server": server_name, "bytes": len(content.encode("utf-8")), "lines": content.count("\n")},
        user_id=current_user.id,
    )
    return {
        "zone": zone_id,
        "server": server_name,
        "format": "bind",
        "content": content,
        "filename": _export_filename(zone_norm),
    }


LUA_IMPORT_BLOCKED_DETAIL = "Die Zonendatei enthält LUA-Records, LUA-Records sind in diesem Panel deaktiviert."


@router.post("/import/preview")
async def import_zone_preview(
    import_data: ZoneImport,
    db: DbWrite,
    admin: User = Depends(get_admin_user),
):
    """Vergleich Zonefile vs. bestehende PDNS-Zone (erster schreibender Server) – kein Schreiben."""
    assert_token_scope(import_data.name, write=False)
    from app.services.lua_records import get_lua_policy
    from app.services.zone_import_diff import build_import_diff, count_passthrough_records

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
    existing = None
    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            existing = await client.get_zone(import_data.name)
            break
        except PowerDNSAPIError as e:
            if e.status_code in (404, 422):
                continue
            raise HTTPException(status_code=e.status_code, detail=str(e.detail)[:2000])
        except ValueError:
            continue
    # Parser im Thread: grosse Zonendateien blockieren den Event-Loop nicht (F15 5.7)
    result = await asyncio.to_thread(build_import_diff, import_data.name, import_data.content, existing)
    policy = await get_lua_policy(db)
    result["lua_policy"] = policy
    # gleiche (grosszuegige) Zaehlung wie das Gate in import_zone
    result["lua_blocked"] = policy == "disabled" and (
        result.get("lua_count", 0) > 0 or count_passthrough_records(import_data.content).get("LUA", 0) > 0
    )
    return result


async def _assert_lua_import_allowed(db: AsyncSession, import_data: ZoneImport, admin: User) -> None:
    """Policy ``disabled`` + LUA in der Datei -> 403 vor jedem PowerDNS-Zugriff (F15 3.6), mit Fehler-Audit."""
    from app.services.lua_records import get_lua_policy
    from app.services.zone_import_diff import count_passthrough_records

    if await get_lua_policy(db) != "disabled":
        return
    n = count_passthrough_records(import_data.content).get("LUA", 0)
    if n <= 0:
        return
    await _log_action(db, "IMPORT", import_data.name, None, {"lua_count": n}, status="error",
                      error_message=LUA_IMPORT_BLOCKED_DETAIL, user_id=admin.id)
    raise HTTPException(status_code=403, detail=LUA_IMPORT_BLOCKED_DETAIL)


@router.post("/import", response_model=MessageResponse)
async def import_zone(
    import_data: ZoneImport,
    db: DbWrite,
    admin: User = Depends(get_admin_user),
):
    """Import a zone from BIND zonefile format (Admin only). Only servers with allow_writes=True are used."""
    assert_token_scope(import_data.name, write=True)
    await _assert_lua_import_allowed(db, import_data, admin)
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
    first_audit: Optional[AuditLog] = None
    payload = {
        "name": import_data.name,
        "kind": import_data.kind,
        "nameservers": import_data.nameservers,
        "zone": import_data.content,
        "soa_edit_api": "DEFAULT",
    }

    # Auf JEDEM schreibbaren Server anlegen (wie create_zone): Server mit getrennten
    # Datenbanken bekommen die Zone so wirklich; bei gemeinsamer Datenbank antwortet der
    # zweite Server mit 409 (existiert bereits) -> "synced".
    for server_name in target_servers:
        try:
            client = pdns_manager.get_client(server_name)
            try:
                await client.create_zone(payload)
                results[server_name] = "imported"
                audit = await _log_action(db, "IMPORT", import_data.name, server_name, {
                    "kind": import_data.kind,
                    "content_length": len(import_data.content),
                }, user_id=admin.id)
                first_audit = first_audit or audit
            except PowerDNSAPIError as e:
                if e.status_code == 409 or "already exists" in (e.detail or "").lower():
                    results[server_name] = "synced"
                    audit = await _log_action(db, "IMPORT", import_data.name, server_name, {
                        "action": "synced (Zone existiert bereits, z. B. gemeinsame Datenbank)",
                    }, user_id=admin.id)
                    first_audit = first_audit or audit
                else:
                    raise
            try:
                await client.rectify_zone(import_data.name)
            except Exception as rexc:  # noqa: BLE001
                logger.warning("rectify nach Import auf %s fehlgeschlagen: %s", server_name, rexc)
                results[server_name] += " (rectify fehlgeschlagen)"

        except PowerDNSAPIError as e:
            results[server_name] = f"error: {e.detail}"
            await _log_action(
                db, "IMPORT", import_data.name, server_name,
                status="error", error_message=e.detail,
                user_id=admin.id,
            )

    # Ereignis nur, wenn mindestens ein Server die Zone importiert oder bereits hatte (F6 5.9)
    if any(v.startswith(("imported", "synced")) for v in results.values()):
        zone_index.invalidate()
        from app.services.webhook_outbox import enqueue_event
        await enqueue_event(
            db, "zone.imported", actor=admin, zone=import_data.name,
            data={
                "zone": import_data.name,
                "kind": import_data.kind,
                "content_length": len(import_data.content),
                "results": dict(results),
            },
            audit_log_id=first_audit.id if first_audit else None,
        )
    return MessageResponse(
        message=f"Zone '{import_data.name}' import completed",
        details=results,
    )
