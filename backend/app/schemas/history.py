"""Schemas fuer Zonenverlauf, Rollback und Admin-Audit-Log (F7 3.1).

Ergaenzungen gegenueber der Spezifikation (Bauplan B.7/B.16): ``before_recreate`` (Eintrag liegt vor der letzten
endgueltigen Zonenloeschung, nur Admins sehen solche Eintraege [D12]), ``actor_username`` (Name zum Zeitpunkt der
Aktion, E-F7-1) und ``client_ip`` (nur fuer Admins gefuellt [S12]).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.audit import RETENTION_MAX_DAYS, RETENTION_MIN_DAYS


class RrsetRecordSnap(BaseModel):
    content: str
    disabled: bool = False


class RrsetCommentSnap(BaseModel):
    content: str = ""
    account: str = ""
    modified_at: Optional[int] = None


class RrsetSnapshot(BaseModel):
    ttl: Optional[int] = None                # robust gegen unvollstaendige Fremd-Eintraege
    records: list[RrsetRecordSnap]          # nach content sortiert, nie leer
    comments: list[RrsetCommentSnap] = []


class RrsetChange(BaseModel):
    name: str                                # FQDN lower + Punkt
    type: str                                # UPPER
    before: Optional[RrsetSnapshot] = None   # None = existierte nicht
    after: Optional[RrsetSnapshot] = None    # None = existiert danach nicht


class HistoryActor(BaseModel):
    user_id: int
    username: Optional[str] = None           # None = Benutzer geloescht


class HistoryEntry(BaseModel):
    id: int
    timestamp: Optional[str] = None
    action: str
    resource_type: str
    resource_name: Optional[str] = None
    server_name: Optional[str] = None
    zone_name: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    actor_username: Optional[str] = None
    client_ip: Optional[str] = None          # nur fuer Admins
    status: str
    error_message: Optional[str] = None      # Nicht-Admins: generischer Text
    version: int                             # 1 (Altbestand) | 2
    changes: Optional[list[RrsetChange]] = None   # None bei version 1; in Listen max. 20
    change_count: int = 0
    changes_truncated: bool = False
    details: Optional[Any] = None            # Details OHNE "changes" (Nicht-Admins: Allowlist)
    revert_of_id: Optional[int] = None
    reverted_by_id: Optional[int] = None
    can_rollback: bool = False
    rollback_blocked_reason: Optional[str] = None
    before_recreate: bool = False


class HistoryListResponse(BaseModel):
    zone: str
    server: str
    total: int
    offset: int
    limit: int
    entries: list[HistoryEntry]
    actors: list[HistoryActor]


class RollbackRequest(BaseModel):
    force: bool = False


class RollbackPlanItem(BaseModel):
    name: str
    type: str
    changetype: Literal["REPLACE", "DELETE"]
    current: Optional[RrsetSnapshot] = None  # Zustand jetzt auf dem Primary
    expected: Optional[RrsetSnapshot] = None  # Stand direkt nach der Aenderung
    target: Optional[RrsetSnapshot] = None   # Ziel des Rollbacks (Vorher-Zustand)
    conflict: bool
    noop: bool


class RollbackSkip(BaseModel):
    name: str
    type: str
    reason: Literal["soa", "dnssec", "acme_challenge"]


class RollbackPreviewResponse(BaseModel):
    audit_id: int
    zone: str
    server: str
    source_server: Optional[str] = None
    action: str
    rollbackable: bool
    blocked_reason: Optional[str] = None
    blocked_message: Optional[str] = None
    already_reverted_by: Optional[int] = None
    has_conflicts: bool = False
    plan: list[RollbackPlanItem] = []
    skipped: list[RollbackSkip] = []
    targets: dict[str, str] = {}


class AuditLogEntry(BaseModel):
    id: int
    timestamp: Optional[str] = None
    action: str
    resource_type: str
    resource_name: Optional[str] = None
    server_name: Optional[str] = None
    zone_name: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    actor_username: Optional[str] = None
    client_ip: Optional[str] = None
    details: Optional[Any] = None            # bei v2 ohne full: changes auf 20 gekuerzt
    details_truncated: bool = False
    status: Optional[str] = None
    error_message: Optional[str] = None
    revert_of_id: Optional[int] = None
    reverted_by_id: Optional[int] = None


class AuditLogListResponse(BaseModel):
    count: int        # Laenge dieser Seite (wie 2.4.1)
    total: int        # Gesamtzahl passend zu den Filtern
    offset: int
    limit: int
    entries: list[AuditLogEntry]


class AuditSettingsResponse(BaseModel):
    retention_days: int
    last_purge_at: Optional[str] = None
    last_purge_deleted: Optional[int] = None
    worker_enabled: bool
    total_entries: int
    oldest_entry_at: Optional[str] = None


class AuditSettingsUpdate(BaseModel):
    retention_days: int = Field(..., ge=0, le=RETENTION_MAX_DAYS)

    @field_validator("retention_days")
    @classmethod
    def _check_range(cls, v: int) -> int:
        if 0 < v < RETENTION_MIN_DAYS:
            raise ValueError(
                f"retention_days muss 0 (unbegrenzt) oder zwischen {RETENTION_MIN_DAYS} und "
                f"{RETENTION_MAX_DAYS} liegen"
            )
        return v


class AuditSettingsSaved(BaseModel):
    message: str
    settings: AuditSettingsResponse
