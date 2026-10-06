"""Admin-Einstellungen LUA-Records (F15 3.4/3.5, Bauplan B.13: ROUTER_ORDER 105, Prefix ``/settings``).

``GET/PUT /settings/lua``: globale Policy ``lua_records_policy`` (``admin`` = nur Admins, Standard; ``manage`` = alle
mit Schreibrecht auf die Zone; ``disabled`` = niemand, Import mit LUA gesperrt). Nur Admin-Browser-Session
(``get_admin_session_user``, kein Panel-Token), Schreiben mit ``DbWrite``; Audit ``LUA_SETTINGS_UPDATE`` nur bei
einer echten Aenderung (``details.changed.policy.from/to``). Keine Webhooks (es gibt keine Settings-Ereignisse).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_admin_session_user
from app.core.database import DbRead, DbWrite
from app.models.models import User
from app.schemas.lua import LuaSettingsResponse, LuaSettingsUpdate
from app.services import lua_records
from app.services.audit import write_audit
from app.services.system_settings import set_setting

ROUTER_ORDER = 105

router = APIRouter(prefix="/settings", tags=["Settings"])


def _settings(policy: str) -> LuaSettingsResponse:
    return LuaSettingsResponse(
        policy=policy,
        default_policy=lua_records.DEFAULT_LUA_POLICY,
        target_types=list(lua_records.LUA_TARGET_TYPES),
        max_content_length=lua_records.LUA_MAX_CONTENT_LENGTH,
    )


@router.get("/lua", response_model=LuaSettingsResponse)
async def get_lua_settings(db: DbRead, admin: User = Depends(get_admin_session_user)):
    return _settings(await lua_records.get_lua_policy(db))


@router.put("/lua")
async def update_lua_settings(body: LuaSettingsUpdate, db: DbWrite, admin: User = Depends(get_admin_session_user)):
    old = await lua_records.get_lua_policy(db)
    new = body.policy
    if old != new:
        await set_setting(db, lua_records.LUA_POLICY_KEY, new)
        await write_audit(db, "LUA_SETTINGS_UPDATE", "settings", "lua", user_id=admin.id,
                          details={"changed": {"policy": {"from": old, "to": new}}})
    return {"message": "LUA-Einstellungen gespeichert", "settings": {"policy": new}}
