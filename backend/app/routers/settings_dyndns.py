"""Admin-Einstellungen DynDNS und PTR (F9 3.7, Bauplan B.13: ROUTER_ORDER 103, Prefix ``/settings``).

- ``GET/PUT /settings/dyndns``: DynDNS-Endpunkt global an/aus (``dyndns_enabled``, Default an) und private
  IP-Adressen erlauben (``dyndns_allow_private_ips``, Default aus).
- ``GET/PUT /settings/ptr``: Admin-Default der PTR-Pflege (``ptr_auto_default``, Default aus) – gilt fuer
  API-Aufrufe ohne ``manage_ptr`` und als Voreinstellung der Checkbox.

Nur Admin-Browser-Session (``get_admin_session_user``), schreibende Endpunkte mit ``DbWrite``; Audit
``DYNDNS_SETTINGS_UPDATE`` bzw. ``PTR_SETTINGS_UPDATE`` nur bei einer echten Aenderung.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.auth import get_admin_session_user
from app.core.database import DbRead, DbWrite
from app.models.models import DynDnsToken, User
from app.schemas.dyndns import DynDnsSettingsBody, DynDnsSettingsOut, PtrSettingsBody, PtrSettingsOut
from app.services import dyndns as dyndns_service
from app.services import ptr as ptr_service
from app.services.audit import write_audit
from app.services.system_settings import get_bool_setting, set_settings

ROUTER_ORDER = 103

router = APIRouter(prefix="/settings", tags=["Settings"])


async def _dyndns_settings(db) -> DynDnsSettingsOut:
    total = (await db.execute(select(func.count(DynDnsToken.id)))).scalar() or 0
    active = (await db.execute(select(func.count(DynDnsToken.id)).where(DynDnsToken.is_active.is_(True)))).scalar() or 0
    return DynDnsSettingsOut(
        enabled=await dyndns_service.is_enabled(db),
        allow_private_ips=await dyndns_service.allow_private(db),
        token_count=int(total),
        active_token_count=int(active),
    )


@router.get("/dyndns", response_model=DynDnsSettingsOut)
async def get_dyndns_settings(db: DbRead, admin: User = Depends(get_admin_session_user)):
    return await _dyndns_settings(db)


@router.put("/dyndns")
async def update_dyndns_settings(body: DynDnsSettingsBody, db: DbWrite, admin: User = Depends(get_admin_session_user)):
    current = {"enabled": await dyndns_service.is_enabled(db),
               "allow_private_ips": await dyndns_service.allow_private(db)}
    keys = {"enabled": dyndns_service.KEY_ENABLED, "allow_private_ips": dyndns_service.KEY_ALLOW_PRIVATE}
    changed: dict[str, dict] = {}
    updates: dict[str, bool] = {}
    for field, key in keys.items():
        value = getattr(body, field)
        if value is None:
            continue
        if bool(value) != current[field]:
            changed[field] = {"from": current[field], "to": bool(value)}
        updates[key] = bool(value)
        current[field] = bool(value)
    if updates:
        await set_settings(db, updates)
    if changed:
        await write_audit(db, "DYNDNS_SETTINGS_UPDATE", "settings", "dyndns", user_id=admin.id,
                          details={"changed": changed})
    return {"message": "DynDNS-Einstellungen gespeichert", "settings": current}


@router.get("/ptr", response_model=PtrSettingsOut)
async def get_ptr_settings(db: DbRead, admin: User = Depends(get_admin_session_user)):
    return PtrSettingsOut(auto_default=await get_bool_setting(db, ptr_service.KEY_AUTO_DEFAULT, False))


@router.put("/ptr")
async def update_ptr_settings(body: PtrSettingsBody, db: DbWrite, admin: User = Depends(get_admin_session_user)):
    old = await get_bool_setting(db, ptr_service.KEY_AUTO_DEFAULT, False)
    new = bool(body.auto_default)
    await set_settings(db, {ptr_service.KEY_AUTO_DEFAULT: new})
    if old != new:
        await write_audit(db, "PTR_SETTINGS_UPDATE", "settings", "ptr", user_id=admin.id,
                          details={"changed": {"auto_default": {"from": old, "to": new}}})
    return {"message": "PTR-Einstellungen gespeichert", "settings": {"auto_default": new}}
