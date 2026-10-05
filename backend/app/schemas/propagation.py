"""Schemas für Propagations-Check und Monitoring (F12/F13, Bauplan WS-F12F13-BE).

- ``Propagation*``: Antwort von ``GET /api/v1/zones/{server}/{zone}/propagation`` (F12 3.1) und die
  Admin-Einstellungen ``GET/PUT /api/v1/settings/propagation`` (F12 3.2/3.3).
- ``Metrics*``: ``GET/PUT /api/v1/settings/metrics`` und ``POST/DELETE /api/v1/settings/metrics/token`` (F13 3.4-3.7).
- ``MonitoringStatus``: ``GET /api/v1/settings/monitoring/status`` (Bauplan [S14]: Worker-, Migrations-, Server- und
  Geheimnis-Kurzstatus fuer Admins; ``/health`` liefert diese Details nur lokal).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceKind = Literal["panel-api", "authoritative", "resolver"]
SourceStatus = Literal["ok", "mismatch", "error", "timeout", "skipped"]
SerialRelation = Literal["equal", "behind", "ahead"]


class PropagationRecordInfo(BaseModel):
    name: str  # FQDN, lower, Punkt am Ende
    type: str
    expected_values: list[str]  # normalisiert (rrsets.content_key), sortiert; deaktivierte Werte ausgeschlossen
    comparable: bool  # False bei LUA/ALIAS am Namen (DNS-Antworten sind dynamisch berechnet)


class PropagationExternal(BaseModel):
    enabled: bool  # propagation_enabled
    authoritative: bool  # enabled UND propagation_check_authoritative
    resolvers: list[str]  # [] wenn enabled False
    ipv6: bool


class PropagationSource(BaseModel):
    source: str  # Servername | NS-Hostname (FQDN) | Resolver-IP
    kind: SourceKind
    target: Optional[str] = None  # IP (DNS); bei panel-api None
    label: Optional[str] = None  # z. B. "Cloudflare" fuer bekannte Resolver
    is_reference: bool = False
    status: SourceStatus
    serial: Optional[int] = None  # ausgelieferte Serial (panel-api: edited_serial, sonst serial)
    serial_raw: Optional[int] = None  # nur panel-api: gespeicherte Serial
    notified_serial: Optional[int] = None  # nur panel-api
    zone_kind: Optional[str] = None  # nur panel-api (Native/Master/Slave/...)
    match: Optional[bool] = None  # Serial passt (siehe Service-Docstring: getrennte Backends)
    serial_relation: Optional[SerialRelation] = None  # RFC 1982 relativ zur erwarteten Serial
    record_values: Optional[list[str]] = None
    record_match: Optional[bool] = None
    content_match: Optional[bool] = None  # panel-api: Inhalt gleich (content=true oder Fingerprint-Vergleich)
    content_diff_count: Optional[int] = None
    content_diff_sample: list[str] = Field(default_factory=list)  # max. 5, Format "www.example.com. A"
    ttl: Optional[int] = None  # DNS: TTL der SOA- bzw. Record-Antwort
    authoritative: Optional[bool] = None  # DNS: AA-Flag
    rcode: Optional[str] = None  # DNS: "NOERROR", "REFUSED", ...
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None  # services.propagation.ERROR_TEXTS
    error: Optional[str] = None  # deutscher Kurztext zu error_code
    notes: list[str] = Field(default_factory=list)  # Notiz-Codes (Frontend: propagation.note_<code>)


class PropagationSummary(BaseModel):
    total: int
    ok: int
    mismatch: int
    failed: int
    skipped: int
    in_sync: bool  # mismatch == 0 and failed == 0


class PropagationResponse(BaseModel):
    zone: str
    server: str  # Referenz-Server
    checked_at: str  # iso_utc
    cached: bool
    duration_ms: int
    timed_out: bool
    expected_serial: int  # edited_serial der Referenz (Fallback serial)
    reference_serial_raw: int
    separate_backends: bool = False  # Zone liegt auf mehreren schreibbaren Panel-Servern [D9]
    nameservers: list[str]  # Apex-NS laut Referenzzone (sortiert)
    record: Optional[PropagationRecordInfo] = None
    content_compared: bool
    external: PropagationExternal
    comparable_types: list[str]
    sources: list[PropagationSource]
    summary: PropagationSummary


class PropagationSettingsOut(BaseModel):
    enabled: bool
    check_authoritative: bool
    ipv6: bool
    resolvers: list[str]
    default_resolvers: list[str]


class PropagationSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    check_authoritative: Optional[bool] = None
    ipv6: Optional[bool] = None
    # Rohliste (eine Adresse je Eintrag); der Server validiert und kuerzt nicht still
    resolvers: Optional[list[str]] = Field(None, max_length=50)


class MetricsSettingsOut(BaseModel):
    enabled: bool  # gespeicherter Schalter metrics_enabled
    effective_enabled: bool  # tatsaechlich aktiv (Env ODER enabled + Token)
    env_override: bool  # METRICS_TOKEN gueltig gesetzt
    token_set: bool  # DB-Token vorhanden und entschluesselbar
    token_unreadable: bool = False  # DB-Token vorhanden, aber nicht entschluesselbar (neu erzeugen)
    token_hint: Optional[str] = None
    token_created_at: Optional[str] = None
    pdns_probe: bool
    endpoint_path: str = "/metrics"
    scrape_url: Optional[str] = None  # oeffentliche Basis-URL + "/metrics", None ohne Basis-URL


class MetricsSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    pdns_probe: Optional[bool] = None


class MetricsTokenCreated(BaseModel):
    token: str  # Klartext, nur in dieser Antwort
    token_hint: str
    token_created_at: str
    warning: str


class BackgroundTaskStatus(BaseModel):
    running: bool
    last_run_at: Optional[str] = None
    last_error_at: Optional[str] = None


class BackgroundStatus(BaseModel):
    enabled: bool
    tasks: dict[str, BackgroundTaskStatus]


class SecretsShortStatus(BaseModel):
    mode: str  # "encrypted" | "plaintext_fallback" | "uninitialized"
    fallback_reason: Optional[str] = None
    key_source: Optional[str] = None  # "env" | "file" | "generated"
    unreadable_values: int = 0  # beim Start nicht entschluesselbare Werte
    runtime_unreadable: int = 0  # unlesbare Lesezugriffe seit dem Start (Summe)


class MonitoringStatus(BaseModel):
    checked_at: str
    ok: bool  # keine Migrationsfehler, alle Server geladen, Geheimnisse verschluesselt und lesbar, Worker laufen
    version: str
    background: BackgroundStatus
    migration_errors: int  # Anzahl fehlgeschlagener Schema-/Datenmigrationen beim letzten Start
    servers_not_loaded: dict[str, str]  # Servername -> Grund (z. B. "api key unreadable")
    secrets: SecretsShortStatus
