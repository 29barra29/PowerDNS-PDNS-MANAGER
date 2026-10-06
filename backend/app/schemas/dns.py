"""Pydantic schemas for request/response validation."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional
from datetime import datetime
import re

from app.services.lua_records import validate_lua_content
# DNSSEC-Schemas liegen ab 3.0 in schemas/dnssec.py (F4 5.6); Re-Export fuer bestehende Importe.
from app.schemas.dnssec import CryptoKeyResponse, DNSSECEnable  # noqa: F401

# Erlaubte Record-Typen (Panel). LUA seit 3.0 (F15); ob ein Benutzer LUA schreiben darf, entscheidet die
# LUA-Policy in den Record-Endpunkten (services/lua_records.assert_lua_write_allowed).
ALLOWED_RECORD_TYPES: tuple[str, ...] = (
    "A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "SRV",
    "CAA", "PTR", "ALIAS", "DNAME", "LOC", "NAPTR", "SSHFP",
    "TLSA", "DS", "DNSKEY", "NSEC", "NSEC3", "NSEC3PARAM",
    "RRSIG", "SPF", "OPENPGPKEY", "HTTPS", "SVCB", "LUA",
)

_GENERIC_TYPE_RE = re.compile(r"TYPE\d+")
GENERIC_TYPE_ERROR = (
    "Generische Typangaben (TYPE…) werden nicht unterstützt – bitte den Typnamen verwenden."
)
MANAGE_PTR_DESCRIPTION = "PTR in verwalteter Reverse-Zone mitpflegen (nur A/AAAA); None = Admin-Default"


def _reject_generic_type(v: str) -> str:
    """``TYPE65402`` & Co. ablehnen: PowerDNS kennt die Generic-Schreibweise als Synonym (z. B. fuer LUA) –
    ohne Sperre liesse sich die LUA-Policy darueber umgehen (F15 5.2)."""
    if _GENERIC_TYPE_RE.fullmatch(v):
        raise ValueError(GENERIC_TYPE_ERROR)
    return v


def validate_allowed_type(v: str) -> str:
    """Record-Typ normalisieren (strip, upper) und gegen ``ALLOWED_RECORD_TYPES`` pruefen.

    Gemeinsame Pruefung fuer ``RecordCreate`` und die Bulk-Schemas (``schemas/bulk.py``, F1 3.1).
    """
    v = _reject_generic_type((v or "").strip().upper())
    if v not in ALLOWED_RECORD_TYPES:
        raise ValueError(f"Unknown record type: {v}")  # Text bleibt (Skript-Kompatibilitaet)
    return v


# ========================
# Zone Schemas
# ========================

class ZoneCreate(BaseModel):
    """Schema for creating a new zone."""
    name: str = Field(..., description="Zone name (e.g., 'example.com')")
    kind: str = Field(default="Native", description="Zone kind: Native, Master, or Slave")
    nameservers: list[str] = Field(
        default_factory=list,
        description="List of nameservers (e.g., ['ns1.example.com.', 'ns2.example.com.'])"
    )
    soa_edit_api: str = Field(default="DEFAULT", description="SOA-EDIT-API setting")
    masters: list[str] = Field(
        default_factory=list,
        description="Master servers (only for Slave zones)"
    )
    enable_dnssec: bool = Field(default=False, description="Enable DNSSEC immediately")
    servers: list[str] = Field(
        default_factory=list,
        description="Server names to create zone on (empty = all servers)"
    )
    # Typisiert seit F4-A (schemas/dnssec.py); ausgewertet beim Zonenanlegen ab F4-B
    # (dnssec_service.enable_dnssec_on_new_zone). Ungueltige Optionen -> 422.
    dnssec_options: Optional[DNSSECEnable] = Field(
        default=None,
        description="DNSSEC-Optionen, nur mit enable_dnssec",
    )

    @field_validator("name")
    @classmethod
    def validate_zone_name(cls, v: str) -> str:
        """Ensure zone name is a valid domain and ends with a dot."""
        v = v.strip().lower().rstrip(".")
        
        # Must contain at least one dot (e.g. example.com, not just "test")
        if "." not in v:
            raise ValueError(
                f"'{v}' ist kein gültiger Domainname. "
                "Ein Domainname muss mindestens eine TLD haben (z.B. example.com, test.de)"
            )
        
        # Check for valid DNS characters
        domain_regex = re.compile(r'^([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')
        if not domain_regex.match(v):
            raise ValueError(
                f"'{v}' enthält ungültige Zeichen oder ist kein gültiger Domainname. "
                "Erlaubt sind: Buchstaben (a-z), Zahlen (0-9) und Bindestriche (-)"
            )
        
        return v + "."

    @field_validator("nameservers")
    @classmethod
    def validate_nameservers(cls, v: list[str]) -> list[str]:
        """Ensure nameservers end with a dot."""
        return [ns if ns.endswith(".") else ns + "." for ns in v]

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        allowed = ["Native", "Master", "Slave"]
        if v not in allowed:
            raise ValueError(f"Kind must be one of: {allowed}")
        return v


class ZoneUpdate(BaseModel):
    """Schema for updating a zone."""
    kind: Optional[str] = None
    masters: Optional[list[str]] = None
    soa_edit_api: Optional[str] = None
    account: Optional[str] = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        vv = v.strip().capitalize()
        # Consumer/Producer (Katalogzonen) bewusst nicht ueber das Panel setzbar.
        if vv not in ("Native", "Master", "Slave"):
            raise ValueError("kind muss Native, Master oder Slave sein")
        return vv


class ZoneResponse(BaseModel):
    """Schema for zone response."""
    id: str
    name: str
    kind: str
    serial: int
    edited_serial: Optional[int] = None  # PowerDNS: Serial inkl. noch nicht veroeffentlichter Aenderungen (F12)
    notified_serial: Optional[int] = None
    dnssec: bool = False
    account: Optional[str] = None
    last_check: Optional[int] = None
    masters: list[str] = []
    rrsets: Optional[list] = None

    model_config = ConfigDict(from_attributes=True)


class ZoneListResponse(BaseModel):
    """Schema for zone list response."""
    server: str
    zones: list[ZoneResponse]


# ========================
# Record Schemas
# ========================

class RecordItem(BaseModel):
    """A single record value."""
    content: str
    disabled: bool = False


class RecordCreate(BaseModel):
    """Schema for creating/replacing a record set."""
    name: str = Field(..., description="Fully qualified record name")
    type: str = Field(..., description="Record type (A, AAAA, CNAME, MX, TXT, etc.)")
    ttl: int = Field(default=3600, ge=60, le=604800, description="TTL in seconds")
    records: list[RecordItem] = Field(..., description="Record values")
    manage_ptr: Optional[bool] = Field(None, description=MANAGE_PTR_DESCRIPTION)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.endswith("."):
            v += "."
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        return validate_allowed_type(v)

    @model_validator(mode="after")
    def _validate_lua(self):
        if self.type == "LUA":
            for item in self.records:
                item.content = validate_lua_content(item.content)
        return self


class RecordDelete(BaseModel):
    """Schema for deleting a record set or a single value of it.

    Ohne ``content`` wird das komplette RRset geloescht (alle Werte). Mit
    ``content`` nur dieser eine Wert; die uebrigen Werte bleiben erhalten.
    """
    name: str
    type: str
    content: str | None = None
    manage_ptr: Optional[bool] = Field(None, description=MANAGE_PTR_DESCRIPTION)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.endswith("."):
            v += "."
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        return _reject_generic_type(v.strip().upper())


class RecordUpdate(BaseModel):
    """Schema for updating a specific record."""
    name: str = Field(..., description="Fully qualified record name")
    type: str = Field(..., description="Record type")
    ttl: int = Field(default=3600, ge=60, le=604800, description="TTL in seconds")
    old_content: str = Field(..., description="Previous content to identify the record")
    new_content: str = Field(..., description="New record content")
    disabled: bool = Field(default=False)
    manage_ptr: Optional[bool] = Field(None, description=MANAGE_PTR_DESCRIPTION)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.endswith("."):
            v += "."
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        return _reject_generic_type(v.strip().upper())

    @model_validator(mode="after")
    def _validate_lua(self):
        # old_content bleibt unveraendert: er muss exakt dem PowerDNS-Bestand entsprechen
        if self.type == "LUA":
            self.new_content = validate_lua_content(self.new_content)
        return self


# Bulk-Schemas (BulkRecordUpdate, Vorschau, Limits) liegen seit 3.0 in schemas/bulk.py (F1 3.1); sie werden
# unten ueber das Modul-__getattr__ re-exportiert (``from app.schemas.dns import BulkRecordUpdate`` bleibt gueltig).


# ========================
# Zone Import/Export Schemas
# ========================

class ZoneImport(BaseModel):
    """Schema for importing a zone from a zonefile."""
    name: str = Field(..., description="Zone name")
    content: str = Field(..., description="BIND-format zone file content")
    kind: str = Field(default="Native")
    nameservers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.endswith("."):
            v += "."
        return v


# ========================
# Server Schemas
# ========================

class ServerInfo(BaseModel):
    """Schema for server information."""
    name: str
    url: Optional[str] = None  # nur fuer effektive Admins (F14/[S5]); sonst weggelassen
    is_reachable: bool
    version: Optional[str] = None
    daemon_type: Optional[str] = None
    zone_count: Optional[int] = None
    # True wenn dieser Server Schreibvorgaenge entgegennimmt (Records / Zonen).
    # Default True fuer Server, die nur via env definiert sind und keinen
    # ServerConfig-Eintrag haben (Backwards-Compat).
    allow_writes: bool = True


class ServerListResponse(BaseModel):
    """Schema for listing all servers."""
    servers: list[ServerInfo]


# ========================
# Search Schema
# ========================

class SearchResult(BaseModel):
    """Schema for search results."""
    server: str
    results: list[dict]


# ========================
# General Response Schemas
# ========================

class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    server: Optional[str] = None
    details: Optional[str] = None


# ========================
# Re-Export der Bulk-Schemas (F1)
# ========================
# schemas/bulk.py importiert RecordCreate/RecordDelete/RecordItem aus diesem Modul. Ein Import von bulk.py hier
# oben bzw. am Dateiende waere zyklisch, je nachdem welches Modul zuerst geladen wird – daher lazy (PEP 562).
_BULK_EXPORTS = frozenset({
    "BULK_MAX_OPS", "BULK_MAX_CHANGED_RRSETS", "BULK_MAX_TEXT_CHARS", "BULK_MAX_TEXT_LINES",
    "RRsetKey", "BulkTtlChange", "BulkDisabledChange", "BulkMergeItem", "BulkExpectation", "BulkRecordUpdate",
    "BulkTextInput", "BulkPreviewRequest", "RRsetSnapshot", "BulkIssue", "BulkChange", "BulkSummary",
    "BulkPreviewResponse",
})


def __getattr__(name: str):
    if name in _BULK_EXPORTS:
        from app.schemas import bulk as _bulk

        return getattr(_bulk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
