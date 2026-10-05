"""Schemas der Webhook-Verwaltung (F6 3.0, Router ``routers/webhooks.py``).

Zeitfelder sind ISO-8601 mit ``+00:00`` (``iso_utc``) oder ``null``. ``url`` ist nur bei Browser-Session gesetzt
(per API-Token ``null``); ``url_display`` zeigt nur ``scheme://host[:port]`` plus ``/…`` und ist ``null``, wenn die
gespeicherte URL nicht entschluesselt werden kann (``has_url = false``, [S10]).
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

DeliveryStatus = Literal["queued", "in_progress", "succeeded", "failed", "dead", "cancelled"]
WebhookScope = Literal["own", "zones"]

_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


def _clean_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        raise ValueError("Name darf nicht leer sein")
    return v


def _clean_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip()
    if _BAD_URL_CHARS.search(v):
        raise ValueError("URL enthält ungültige Zeichen")
    if len(v) < 8:
        raise ValueError("URL ist zu kurz")
    return v


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=8, max_length=1024)
    events: list[str] = Field(default_factory=lambda: ["*"], max_length=30)
    scope: WebhookScope = "own"
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _clean_url(v)


class WebhookUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    url: Optional[str] = Field(None, min_length=8, max_length=1024)
    events: Optional[list[str]] = Field(None, max_length=30)
    scope: Optional[WebhookScope] = None
    is_active: Optional[bool] = None
    rotate_secret: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        return _clean_name(v)

    @field_validator("url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        return _clean_url(v)


class WebhookStats(BaseModel):
    queued: int = 0
    in_progress: int = 0
    failed: int = 0
    succeeded: int = 0
    dead: int = 0
    cancelled: int = 0


class WebhookOut(BaseModel):
    id: int
    name: str
    url: Optional[str]  # volle URL nur bei Browser-Session, sonst None
    url_display: Optional[str]  # ohne Pfad/Query/Userinfo; None = URL nicht lesbar
    has_url: bool  # False, wenn die verschluesselte URL nicht lesbar ist [S10]
    events: list[str]
    scope: WebhookScope
    is_active: bool
    has_secret: bool  # False, wenn das Secret nicht lesbar ist (F5)
    created_at: Optional[str]
    updated_at: Optional[str]
    last_success_at: Optional[str]
    last_failure_at: Optional[str]
    consecutive_failures: int
    stats: WebhookStats


class WebhookUpdateOut(WebhookOut):
    new_secret: Optional[str] = None  # nur nach Rotation in der Antwort


class WebhookListOut(BaseModel):
    webhooks: list[WebhookOut]
    available_events: list[str]
    event_categories: list[str]
    worker_enabled: bool
    worker_running: bool
    max_attempts: int
    retention_days: int
    max_webhooks: int


class WebhookCreatedOut(BaseModel):
    webhook: WebhookOut
    secret: str
    warning: str


class DeliveryOut(BaseModel):
    id: int  # DB-PK (fuer Pfade)
    delivery_id: str  # == Header X-DNS-Manager-Delivery
    event_id: str  # gleich fuer alle Webhooks desselben Ereignisses
    event: str
    zone: Optional[str]
    status: DeliveryStatus
    attempts: int
    max_attempts: int
    created_at: Optional[str]
    last_attempt_at: Optional[str]
    next_attempt_at: Optional[str]  # None bei succeeded/dead/cancelled/in_progress
    delivered_at: Optional[str]
    last_status_code: Optional[int]
    last_error_code: Optional[str]
    last_error: Optional[str]
    last_response_excerpt: Optional[str]
    last_duration_ms: Optional[int]
    can_retry: bool


class DeliveryDetailOut(DeliveryOut):
    body: Any  # geparstes JSON; bei Parse-Fehler der Rohstring
    request_headers: dict[str, str]


class DeliveryListOut(BaseModel):
    total: int
    limit: int
    offset: int
    deliveries: list[DeliveryOut]


class WebhookTestOut(BaseModel):
    success: bool
    message: str
    delivery: DeliveryOut


class DeliveryRetryOut(BaseModel):
    message: str
    delivery: DeliveryOut


class WebhookDeletedOut(BaseModel):
    message: str
    deleted_deliveries: int
