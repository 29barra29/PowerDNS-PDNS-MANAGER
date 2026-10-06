"""Bulk-Editor: Request-/Response-Schemas und Limits (F1 3.1).

``schemas/dns.py`` re-exportiert alle Namen (``from app.schemas.dns import BulkRecordUpdate`` bleibt gueltig).

Semantik von ``BulkRecordUpdate`` (je Server ein Plan, ein PATCH, siehe ``services/bulk.py``):

- ``create``        REPLACE des kompletten RRsets (wie 2.4.1), ``records`` darf nicht leer sein
- ``delete``        ohne ``content``: ganzes RRset loeschen; mit ``content``: nur diesen Wert entfernen
- ``merge``         Werte anhaengen (Einzel-Create-Semantik), ``ttl=None`` behaelt die bestehende TTL
- ``set_ttl``       TTL eines RRsets setzen (TTL ist in DNS eine RRset-Eigenschaft)
- ``set_disabled``  einen Wert (de)aktivieren
- ``expected``      optimistische Sperre gegen den Stand des Primary (409 bei Abweichung, ``force`` uebergeht sie).
                    Die Vorschau liefert einen Fingerprint fuer jedes beruehrte RRset (auch unveraenderte). Ist
                    ``expected`` gesetzt, gilt jedes geaenderte RRset ohne Fingerprint ebenfalls als Abweichung.

"Absolute" Ops (``create``, ``delete`` ohne ``content``) duerfen je (Name, Typ) nur einmal vorkommen und nicht mit
"relativen" Ops (``delete`` mit ``content``, ``merge``, ``set_ttl``, ``set_disabled``) desselben RRsets kombiniert
werden. Eine leere Anfrage lehnt der Endpunkt ab (nach der Zonen-Rechtepruefung, siehe ``services/bulk.py``).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.dns import (
    RECORD_CONTENT_MAX_LENGTH,
    RecordCreate,
    RecordDelete,
    RecordItem,
    _reject_generic_type,
    validate_allowed_type,
)
from app.services.lua_records import validate_lua_items

BULK_MAX_OPS = 5000             # Summe aller Listeneintraege eines Requests
BULK_MAX_CHANGED_RRSETS = 1000  # geaenderte RRsets je Request (Audit bleibt vollstaendig)
BULK_MAX_TEXT_CHARS = 1_000_000
BULK_MAX_TEXT_LINES = 20_000

TTL_MIN = 60
TTL_MAX = 604800

FINGERPRINT_PATTERN = r"^(absent|[0-9a-f]{32})$"


def _norm_fqdn(v: str) -> str:
    """strip, lower, Trailing-Dot (wie ``RecordCreate.validate_name``)."""
    v = (v or "").strip().lower()
    if not v.endswith("."):
        v += "."
    return v


def _norm_type(v: str) -> str:
    return (v or "").strip().upper()


class _NameType(BaseModel):
    name: str
    type: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _norm_fqdn(v)


class RRsetKey(_NameType):
    """Schluessel eines RRsets (Scope des Modus ``sync_scope``). Typ nur normalisiert (ungueltige Eintraege
    meldet die Vorschau als Warnung ``scope_invalid``)."""

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _norm_type(v)


class BulkTtlChange(_NameType):
    ttl: int = Field(..., ge=TTL_MIN, le=TTL_MAX)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _reject_generic_type(_norm_type(v))


class BulkDisabledChange(_NameType):
    content: str = Field(..., max_length=RECORD_CONTENT_MAX_LENGTH)  # F15-fix3, siehe schemas/dns.py
    disabled: bool

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _reject_generic_type(_norm_type(v))


class BulkMergeItem(_NameType):
    ttl: Optional[int] = Field(default=None, ge=TTL_MIN, le=TTL_MAX)  # None = TTL des bestehenden RRsets behalten
    records: list[RecordItem] = Field(..., min_length=1, max_length=BULK_MAX_OPS)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return validate_allowed_type(v)

    @model_validator(mode="after")
    def _validate_lua(self):
        validate_lua_items(self.type, self.records)
        return self


class BulkExpectation(_NameType):
    fingerprint: str = Field(..., pattern=FINGERPRINT_PATTERN)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _norm_type(v)


def _key(name: str, rtype: str) -> tuple[str, str]:
    return (_norm_fqdn(name), _norm_type(rtype))


def count_ops(ops: "BulkRecordUpdate") -> int:
    """Anzahl aller Listeneintraege (ohne ``expected``)."""
    return len(ops.create) + len(ops.delete) + len(ops.merge) + len(ops.set_ttl) + len(ops.set_disabled)


class BulkRecordUpdate(BaseModel):
    """Bulk-Aenderung einer Zone (F1 3.1). Alte Clients (nur ``create``/``delete``) funktionieren weiter."""

    create: list[RecordCreate] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    delete: list[RecordDelete] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    merge: list[BulkMergeItem] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    set_ttl: list[BulkTtlChange] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    set_disabled: list[BulkDisabledChange] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    default_ttl: int = Field(default=3600, ge=TTL_MIN, le=TTL_MAX)  # fuer merge-Items ohne ttl, deren RRset neu entsteht
    expected: list[BulkExpectation] = Field(default_factory=list, max_length=BULK_MAX_OPS)
    force: bool = False  # expected-Abweichungen ignorieren (nur API, im Audit als forced sichtbar)
    source: Literal["api", "selection", "text"] = "api"  # nur fuer Audit/Webhook
    mode: Optional[Literal["merge", "replace", "sync_scope"]] = None  # nur fuer Audit/Webhook (Textfluss)
    # F11: PTR-Pflege fuer A/AAAA; zaehlt nur auf dieser Ebene (Felder in create[] werden ignoriert).
    manage_ptr: Optional[bool] = Field(
        None, description="PTR in verwalteter Reverse-Zone mitpflegen (nur A/AAAA); None = Admin-Default",
    )

    @model_validator(mode="after")
    def _check(self):
        if count_ops(self) > BULK_MAX_OPS:
            raise ValueError(f"Zu viele Operationen in einer Anfrage (maximal {BULK_MAX_OPS})")
        absolute: set[tuple[str, str]] = set()
        for item in self.create:
            if not item.records:
                raise ValueError(f"{item.name} {item.type}: leere Werteliste – zum Löschen bitte 'delete' verwenden")
            k = _key(item.name, item.type)
            if k in absolute:
                raise ValueError(f"{item.name} {item.type}: doppelt in 'create'")
            absolute.add(k)
        for item in self.delete:
            if item.content is not None:
                continue
            k = _key(item.name, item.type)
            if k in absolute:
                raise ValueError(f"{item.name} {item.type}: widersprüchliche Operationen in einer Anfrage")
            absolute.add(k)
        relative = [_key(d.name, d.type) for d in self.delete if d.content is not None]
        relative += [_key(m.name, m.type) for m in self.merge]
        relative += [_key(t.name, t.type) for t in self.set_ttl]
        relative += [_key(s.name, s.type) for s in self.set_disabled]
        for k in relative:
            if k in absolute:
                raise ValueError(f"{k[0]} {k[1]}: widersprüchliche Operationen in einer Anfrage")
        return self


# ========================
# Vorschau
# ========================
class BulkTextInput(BaseModel):
    content: str = Field(..., max_length=BULK_MAX_TEXT_CHARS)
    mode: Literal["merge", "replace", "sync_scope"] = "merge"
    scope: list[RRsetKey] = Field(default_factory=list, max_length=BULK_MAX_OPS)  # nur fuer sync_scope relevant
    default_ttl: int = Field(default=3600, ge=TTL_MIN, le=TTL_MAX)


class BulkPreviewRequest(BaseModel):
    ops: Optional[BulkRecordUpdate] = None
    text: Optional[BulkTextInput] = None

    @model_validator(mode="after")
    def _check(self):
        if (self.ops is None) == (self.text is None):
            raise ValueError("Entweder 'ops' oder 'text' angeben")
        if self.text is not None and self.text.mode == "sync_scope" and not self.text.scope:
            raise ValueError("Modus 'sync_scope' braucht einen Bereich (scope)")
        return self


class RRsetSnapshot(BaseModel):
    ttl: int
    records: list[RecordItem]
    comments: list[dict] = Field(default_factory=list)  # PowerDNS-Kommentare unveraendert durchgereicht


class BulkIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str                       # deutscher Text (Fallback fuer das Frontend)
    line: Optional[int] = None         # 1-basiert, nur Textfluss
    name: Optional[str] = None
    type: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)  # Interpolationswerte fuer i18n


Semantics = Literal["replace", "merge", "delete_rrset", "delete_value", "ttl", "disabled"]


class BulkChange(BaseModel):
    name: str
    type: str
    op: Literal["create", "update", "delete"]
    semantics: list[Semantics]
    before: Optional[RRsetSnapshot]
    after: Optional[RRsetSnapshot]
    added: list[str]             # Inhalte neu
    removed: list[str]           # Inhalte entfernt
    kept: list[str]              # Inhalte unveraendert vorhanden
    disabled_changed: list[str]  # Inhalte, deren disabled-Flag kippt
    ttl_before: Optional[int]
    ttl_after: Optional[int]


class BulkSummary(BaseModel):
    rrsets_created: int
    rrsets_updated: int
    rrsets_deleted: int
    rrsets_unchanged: int
    values_added: int
    values_removed: int


class BulkPreviewResponse(BaseModel):
    zone: str                    # normalisiert, mit Punkt
    server: str
    source: Literal["api", "selection", "text"]
    mode: Optional[str]
    blocking: bool
    issues: list[BulkIssue]
    changes: list[BulkChange]    # sortiert nach (name, type)
    summary: BulkSummary
    ops: Optional[BulkRecordUpdate]   # None bei blocking oder ohne Aenderung; sonst exakt an POST /bulk zu senden
    peers: list[str]             # weitere schreibbare Fan-out-Ziele (ohne Primary)
    skipped_servers: dict[str, str]   # z. B. {"ns3": "read-only"}
