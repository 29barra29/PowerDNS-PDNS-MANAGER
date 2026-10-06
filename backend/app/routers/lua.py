"""LUA-Records: Policy und PowerDNS-Server-Status fuer die Oberflaeche (F15 3.2/3.3, Bauplan B.13: ROUTER_ORDER 75).

- ``GET /lua/policy``: Policy und ``can_write`` fuer den Aufrufer (auch Panel-Tokens; Admin-Recht eines Tokens nur
  mit ``allow_admin``, ``is_effective_admin``).
- ``GET /lua/server-status``: ``enable-lua-records``, geoip-Backend usw. je PowerDNS-Server (Whitelist aus
  ``GET /config``, 60-s-Cache). Nur fuer Admins oder Nutzer mit mindestens einem Zonenrecht [S5]; ``refresh``
  wirkt nur fuer effektive Admins (sonst Cache), damit Nutzer die PowerDNS-Server nicht mit Abrufen fluten.

Beide nur lesend (``DbRead``), kein Audit. Eigener Prefix ``/lua`` – kein Konflikt mit ``{zone_id:path}``-Routen.
``server-status`` gibt die DB-Verbindung vor den PowerDNS-Abfragen frei; gleichzeitige Aufrufe teilen sich eine
laufende Abfrage je Server (Review-Fund W3-L1).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import effective_zone_filter, get_current_user, is_effective_admin
from app.core.database import DbRead
from app.models.models import User
from app.schemas.lua import LuaPolicyResponse, LuaServerStatusResponse
from app.services import lua_records
from app.services.propagation import release_db

ROUTER_ORDER = 75

router = APIRouter(prefix="/lua", tags=["LUA"])

STATUS_NO_ZONE_DETAIL = "Der LUA-Status der Server ist nur mit Zugriff auf mindestens eine Zone abrufbar"


@router.get("/policy", response_model=LuaPolicyResponse)
async def read_lua_policy(db: DbRead, current_user: User = Depends(get_current_user)):
    policy = await lua_records.get_lua_policy(db)
    ok = lua_records.lua_policy_allows(policy, current_user)
    return LuaPolicyResponse(
        policy=policy,
        can_write=ok,
        reason=None if ok else lua_records.lua_denied_message(policy),
        target_types=list(lua_records.LUA_TARGET_TYPES),
        max_content_length=lua_records.LUA_MAX_CONTENT_LENGTH,
    )


@router.get("/server-status", response_model=LuaServerStatusResponse)
async def read_lua_server_status(db: DbRead, refresh: bool = False, current_user: User = Depends(get_current_user)):
    # Admins (auch per Token ohne Zonen-Scope) sehen alles; sonst mindestens ein Zonenrecht (Token-Scope beachtet)
    zones = await effective_zone_filter(db, current_user)
    if zones is not None and not zones:
        raise HTTPException(status_code=403, detail=STATUS_NO_ZONE_DETAIL)
    refresh_ok = bool(refresh) and is_effective_admin(current_user)
    await release_db(db)  # Rechte geprueft; die PowerDNS-Abfragen (bis zu 5 s) halten keine Pool-Verbindung (W3-L1)
    return await lua_records.get_server_lua_status(refresh_ok)
