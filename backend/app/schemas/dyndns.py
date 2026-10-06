"""Schemas fuer DynDNS (F9 3.3-3.7) und PTR-Pflege (F11 3.7/3.8).

DynDNS-Tokens duerfen nur A/AAAA einzelner, vorab freigegebener Hostnamen setzen. Die Klartext-Tokens erscheinen
genau einmal in der Antwort von Create/Rotate (``plaintext_token``), nie in Listen oder im Audit.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

TypeLit = Literal["A", "AAAA"]
TYPE_ORDER = ("A", "AAAA")

MIN_TTL = 60
MAX_TTL = 86400
DEFAULT_TTL = 60
MAX_HOSTNAMES = 20


def _ordered_types(value: Optional[list[str]]) -> Optional[list[str]]:
    """Dedupliziert, Reihenfolge A, AAAA."""
    if value is None:
        return None
    present = {str(v).upper() for v in value}
    return [t for t in TYPE_ORDER if t in present]


# ---------------------------------------------------------------------------------------------------------------------
# Update-Endpunkte
# ---------------------------------------------------------------------------------------------------------------------
class DynDnsUpdateBody(BaseModel):
    """Optionaler JSON-Body von ``POST /dyndns/update`` (Felder ueberschreiben die Query-Werte)."""

    hostname: Optional[str] = Field(None, max_length=5200)  # 20 x 253 + Kommas
    myip: Optional[str] = Field(None, max_length=200)
    myipv4: Optional[str] = Field(None, max_length=64)
    myipv6: Optional[str] = Field(None, max_length=64)


class DynDnsChange(BaseModel):
    type: TypeLit
    status: Literal["updated", "unchanged", "skipped"]
    old: list[str] = Field(default_factory=list)  # Werte vorher (Stand des ersten erreichbaren Servers)
    new: Optional[str] = None
    reason: Optional[str] = None  # bei skipped: "type_not_allowed"


class DynDnsHostResult(BaseModel):
    hostname: str
    zone: Optional[str] = None  # nur, wenn der Token-Besitzer die Zone lesen darf [S5]
    result: str  # good|nochg|nohost|notfqdn|badip|dnserr|911
    ips: list[str] = Field(default_factory=list)
    changes: list[DynDnsChange] = Field(default_factory=list)
    fanout: Optional[dict[str, str]] = None
    ptr: Optional[list[dict]] = None
    detail: Optional[str] = None
    repair: Optional[bool] = None  # True: nur veraltete Peer-Server nachgezogen [D5]


class DynDnsUpdateResponse(BaseModel):
    result: str  # schlechtester Code aller Hosts
    client_ip: Optional[str] = None
    ip_source: Optional[Literal["param", "client_ip"]] = None
    hosts: list[DynDnsHostResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------------------------------------
# Token-Verwaltung
# ---------------------------------------------------------------------------------------------------------------------
class DynDnsTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    hostnames: list[str] = Field(..., min_length=1, max_length=MAX_HOSTNAMES)
    allowed_types: list[TypeLit] = Field(default_factory=lambda: ["A", "AAAA"], min_length=1)
    ttl: int = Field(DEFAULT_TTL, ge=MIN_TTL, le=MAX_TTL)
    update_ptr: bool = False

    @field_validator("allowed_types")
    @classmethod
    def _types(cls, v):
        return _ordered_types(v)

    @field_validator("hostnames")
    @classmethod
    def _hostnames_len(cls, v):
        for h in v:
            if len(str(h)) > 255:
                raise ValueError("Hostname zu lang")
        return v


class DynDnsTokenUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    hostnames: Optional[list[str]] = Field(None, min_length=1, max_length=MAX_HOSTNAMES)
    allowed_types: Optional[list[TypeLit]] = Field(None, min_length=1)
    ttl: Optional[int] = Field(None, ge=MIN_TTL, le=MAX_TTL)
    update_ptr: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("allowed_types")
    @classmethod
    def _types(cls, v):
        return _ordered_types(v)

    @field_validator("hostnames")
    @classmethod
    def _hostnames_len(cls, v):
        for h in v or []:
            if len(str(h)) > 255:
                raise ValueError("Hostname zu lang")
        return v


# ---------------------------------------------------------------------------------------------------------------------
# Admin-Einstellungen
# ---------------------------------------------------------------------------------------------------------------------
class DynDnsSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    allow_private_ips: Optional[bool] = None


class DynDnsSettingsOut(BaseModel):
    enabled: bool
    allow_private_ips: bool
    token_count: int
    active_token_count: int


class PtrSettingsBody(BaseModel):
    auto_default: bool


class PtrSettingsOut(BaseModel):
    auto_default: bool


# ---------------------------------------------------------------------------------------------------------------------
# PTR
# ---------------------------------------------------------------------------------------------------------------------
class PtrConfigResponse(BaseModel):
    auto_default: bool
    reverse_zones_available: int


class PtrLookupResponse(BaseModel):
    ip: str  # kanonisch
    ptr: str  # Reverse-Name
    zone: Optional[str] = None  # nur mit Leserecht auf die Reverse-Zone [S5]
    status: Literal["ok", "no_reverse_zone", "classless", "forbidden", "error"]
    current: Optional[list[str]] = None  # nur mit Leserecht auf die Reverse-Zone
    would: Optional[Literal["set", "unchanged", "conflict"]] = None  # nur mit name + Schreibrecht
    classless_zone: Optional[str] = None  # nur mit Leserecht auf diese Zone [S5]
    detail: Optional[str] = None
