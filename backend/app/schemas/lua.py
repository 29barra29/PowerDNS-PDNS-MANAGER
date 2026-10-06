"""Schemas fuer LUA-Records (F15 3.2–3.5): Policy fuer die UI, Server-Status und Admin-Einstellungen."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

LuaPolicy = Literal["admin", "manage", "disabled"]


class LuaPolicyResponse(BaseModel):
    """``GET /lua/policy`` – darf DIESER Nutzer LUA-Records anlegen/aendern (Zonenrecht prueft die UI separat)?"""

    policy: LuaPolicy
    can_write: bool
    reason: Optional[str] = None  # Policy-Text, wenn can_write False
    target_types: list[str]
    max_content_length: int


class LuaServerStatus(BaseModel):
    """LUA-relevante PowerDNS-Konfiguration eines Servers (nur Whitelist-Werte aus ``GET /config``)."""

    name: str
    reachable: bool
    lua_records: Optional[Literal["yes", "shared", "no"]] = None  # None = unbekannt
    geoip_backend: Optional[bool] = None  # "geoip" in launch=
    edns_subnet_processing: Optional[bool] = None
    exec_limit: Optional[int] = None  # lua-records-exec-limit
    health_checks_interval: Optional[int] = None  # lua-health-checks-interval
    error: Optional[str] = None  # kurzer deutscher Text, nie PowerDNS-Body oder URL
    checked_at: str


class LuaServerStatusResponse(BaseModel):
    servers: list[LuaServerStatus]
    checked_at: str  # Zeitpunkt des aeltesten verwendeten Eintrags
    cached: bool  # mindestens ein Eintrag kam aus dem 60-s-Cache


class LuaSettingsResponse(BaseModel):
    """``GET /settings/lua`` (Admin, Browser-Session)."""

    policy: LuaPolicy
    default_policy: Literal["admin"] = "admin"
    target_types: list[str]
    max_content_length: int


class LuaSettingsUpdate(BaseModel):
    """``PUT /settings/lua`` – andere Werte als admin|manage|disabled -> 422."""

    policy: LuaPolicy = Field(..., description="admin | manage | disabled")
