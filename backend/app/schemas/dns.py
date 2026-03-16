"""Pydantic schemas for request/response validation."""
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
import re


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


class ZoneResponse(BaseModel):
    """Schema for zone response."""
    id: str
    name: str
    kind: str
    serial: int
    notified_serial: Optional[int] = None
    dnssec: bool = False
    account: Optional[str] = None
    last_check: Optional[int] = None
    masters: list[str] = []
    rrsets: Optional[list] = None

    class Config:
        from_attributes = True


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
        v = v.strip().upper()
        allowed_types = [
            "A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "SRV",
            "CAA", "PTR", "ALIAS", "DNAME", "LOC", "NAPTR", "SSHFP",
            "TLSA", "DS", "DNSKEY", "NSEC", "NSEC3", "NSEC3PARAM",
            "RRSIG", "SPF", "OPENPGPKEY", "HTTPS", "SVCB",
        ]
        if v not in allowed_types:
            raise ValueError(f"Unknown record type: {v}")
        return v


class RecordDelete(BaseModel):
    """Schema for deleting a record set."""
    name: str
    type: str

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
        return v.strip().upper()


class RecordUpdate(BaseModel):
    """Schema for updating a specific record."""
    name: str = Field(..., description="Fully qualified record name")
    type: str = Field(..., description="Record type")
    ttl: int = Field(default=3600, ge=60, le=604800, description="TTL in seconds")
    old_content: str = Field(..., description="Previous content to identify the record")
    new_content: str = Field(..., description="New record content")
    disabled: bool = Field(default=False)

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
        return v.strip().upper()


class BulkRecordUpdate(BaseModel):
    """Schema for bulk record operations."""
    create: list[RecordCreate] = Field(default_factory=list)
    delete: list[RecordDelete] = Field(default_factory=list)


# ========================
# DNSSEC Schemas
# ========================

class DNSSECEnable(BaseModel):
    """Schema for enabling DNSSEC."""
    algorithm: str = Field(default="ECDSAP256SHA256", description="DNSSEC algorithm")
    nsec3param: str = Field(default="1 0 1 ab", description="NSEC3 parameters")

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        allowed = [
            "ECDSAP256SHA256", "ECDSAP384SHA384",
            "ED25519", "ED448",
            "RSASHA256", "RSASHA512",
        ]
        if v not in allowed:
            raise ValueError(f"Algorithm must be one of: {allowed}")
        return v


class CryptoKeyResponse(BaseModel):
    """Schema for DNSSEC key response."""
    id: int
    type: Optional[str] = None
    keytype: Optional[str] = None
    active: bool = False
    published: Optional[bool] = None
    dnskey: Optional[str] = None
    ds: Optional[list[str]] = None
    cds: Optional[list[str]] = None
    algorithm: Optional[str] = None
    bits: Optional[int] = None


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
    url: str
    is_reachable: bool
    version: Optional[str] = None
    daemon_type: Optional[str] = None
    zone_count: Optional[int] = None


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
