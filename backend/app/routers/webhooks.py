"""Webhook-Verwaltung des eigenen Kontos (F6 3.1–3.8, Bauplan B.13: ``ROUTER_ORDER = 30``).

Pfade wie 2.4.1 (``/api/v1/auth/me/webhooks…``). Rechte (F14 3.10, F6 7):

- ``GET ""`` (Liste) auch per Panel-Token – dann ohne Ziel-URL (``url = null``; Slack/Teams/Discord-URLs sind
  selbst Geheimnisse).
- Alles andere nur mit Browser-Session (``get_session_user``): Anlegen, Aendern (inkl. Aktiv-Schalter und
  Secret-Rotation), Loeschen, Test senden, Zustellprotokoll lesen, erneut senden. Ein geleakter API-Token darf
  keinen Datenabfluss verankern und keine Protokolle (Zonennamen, Antworten) lesen.

Jede Abfrage filtert auf den Besitzer; fremde IDs verhalten sich wie nicht vorhandene (404). Die Ziel-URL wird
vor dem Speichern im Thread geprueft (``asyncio.to_thread`` – DNS-Aufloesung nie im Event-Loop) und liegt
verschluesselt in der Datenbank [S10]; ist sie nicht lesbar, zeigt die Liste ``url_display = null`` und
``has_url = false``. Verwaltungsaktionen werden auditiert (``WEBHOOK_*``, nur Host, nie Pfad/Secret).
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from app.core.auth import get_current_user, get_session_user
from app.core.database import DbRead, DbWrite
from app.core.timeutil import iso_utc, utcnow
from app.models.models import User, Webhook, WebhookDelivery
from app.schemas.webhooks import (
    DeliveryDetailOut,
    DeliveryListOut,
    DeliveryRetryOut,
    DeliveryStatus,
    WebhookCreate,
    WebhookCreatedOut,
    WebhookDeletedOut,
    WebhookListOut,
    WebhookTestOut,
    WebhookUpdate,
    WebhookUpdateOut,
)

ROUTER_ORDER = 30

router = APIRouter(prefix="/auth/me/webhooks", tags=["Webhooks"])

CREATED_WARNING = (
    "Das Shared Secret wird nur jetzt angezeigt – bitte beim Empfänger hinterlegen. "
    "Bei Verlust „Secret erneuern“ nutzen."
)
NOT_FOUND = "Webhook nicht gefunden"
DELIVERY_NOT_FOUND = "Zustellung nicht gefunden"
TEST_COOLDOWN_SECONDS = 10.0
STATS_KEYS = ("queued", "in_progress", "failed", "succeeded", "dead", "cancelled")
SCOPES = ("own", "zones")

# Cooldown der Test-Zustellung je Webhook (monotonic); ein Prozess, daher im Speicher ausreichend.
_last_test: dict[int, float] = {}


def _is_session(request: Request) -> bool:
    return getattr(request.state, "auth_via", None) == "session"


def _events_list(value) -> list[str]:
    if isinstance(value, list) and value:
        return [str(v) for v in value]
    return ["*"]


def webhook_out(wh: Webhook, stats: Optional[dict] = None, *, show_url: bool) -> dict:
    """Serialisierung ``WebhookOut`` (F6 3.0); ``show_url`` nur bei Browser-Session."""
    from app.services.webhook_outbox import secret_readable, url_readable
    from app.services.webhook_service import url_display

    readable = url_readable(wh)
    stats = stats or {}
    return {
        "id": wh.id,
        "name": wh.name,
        "url": str(wh.url) if (show_url and readable) else None,
        "url_display": (url_display(wh.url) or None) if readable else None,
        "has_url": readable,
        "events": _events_list(wh.events),
        "scope": wh.scope if wh.scope in SCOPES else "own",
        "is_active": bool(wh.is_active),
        "has_secret": secret_readable(wh),
        "created_at": iso_utc(wh.created_at),
        "updated_at": iso_utc(wh.updated_at),
        "last_success_at": iso_utc(wh.last_success_at),
        "last_failure_at": iso_utc(wh.last_failure_at),
        "consecutive_failures": int(wh.consecutive_failures or 0),
        "stats": {k: int(stats.get(k, 0) or 0) for k in STATS_KEYS},
    }


def delivery_out(d: WebhookDelivery, *, webhook_active: bool) -> dict:
    """Serialisierung ``DeliveryOut`` (ohne Body)."""
    from app.services.webhook_outbox import RETRYABLE_STATUSES

    return {
        "id": d.id,
        "delivery_id": d.delivery_id,
        "event_id": d.event_id,
        "event": d.event,
        "zone": d.zone_name,
        "status": d.status,
        "attempts": int(d.attempts or 0),
        "max_attempts": int(d.max_attempts or 0),
        "created_at": iso_utc(d.created_at),
        "last_attempt_at": iso_utc(d.last_attempt_at),
        "next_attempt_at": iso_utc(d.next_attempt_at) if d.status in ("queued", "failed") else None,
        "delivered_at": iso_utc(d.delivered_at),
        "last_status_code": d.last_status_code,
        "last_error_code": d.last_error_code,
        "last_error": d.last_error,
        "last_response_excerpt": d.last_response_excerpt,
        "last_duration_ms": d.last_duration_ms,
        "can_retry": bool(webhook_active) and d.status in RETRYABLE_STATUSES,
    }


async def _load_webhook(db, user: User, webhook_id: int) -> Webhook:
    from app.services import webhook_service

    wh = await webhook_service.get_webhook(db, user.id, webhook_id)
    if wh is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return wh


async def _load_delivery(db, user: User, wh: Webhook, delivery_pk: int) -> WebhookDelivery:
    d = (await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_pk,
            WebhookDelivery.webhook_id == wh.id,
            WebhookDelivery.user_id == user.id,
        )
    )).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail=DELIVERY_NOT_FOUND)
    return d


async def _validated_url(raw: str) -> str:
    from app.services.webhook_service import validate_webhook_url

    try:
        return await asyncio.to_thread(validate_webhook_url, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalized_events(raw) -> list[str]:
    from app.services.webhook_events import normalize_event_filters

    try:
        return normalize_event_filters(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- 3.1 Liste
@router.get("", response_model=WebhookListOut)
async def list_my_webhooks(
    request: Request,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Eigene Webhooks mit Zustell-Statistik; per Panel-Token ohne Ziel-URL."""
    from app.services import webhook_service, webhook_worker
    from app.services.webhook_events import EVENT_CATEGORIES, SUBSCRIBABLE_EVENTS
    from app.services.webhook_outbox import MAX_ATTEMPTS, stats_for_user

    show_url = _is_session(request)
    rows = await webhook_service.list_webhooks(db, current_user.id)
    stats = await stats_for_user(db, current_user.id) if rows else {}
    state = webhook_worker.worker_state()
    return {
        "webhooks": [webhook_out(w, stats.get(w.id), show_url=show_url) for w in rows],
        "available_events": list(SUBSCRIBABLE_EVENTS),
        "event_categories": list(EVENT_CATEGORIES),
        "worker_enabled": bool(state.get("enabled")),
        "worker_running": bool(state.get("running")),
        "max_attempts": MAX_ATTEMPTS,
        "retention_days": webhook_worker.RETENTION_DAYS,
        "max_webhooks": webhook_service.MAX_WEBHOOKS_PER_USER,
    }


# --------------------------------------------------------------------------- 3.2 Anlegen
@router.post("", status_code=201, response_model=WebhookCreatedOut)
async def create_my_webhook(
    data: WebhookCreate,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    from app.services import webhook_service
    from app.services.audit import write_audit

    if await webhook_service.count_webhooks(db, current_user.id) >= webhook_service.MAX_WEBHOOKS_PER_USER:
        raise HTTPException(status_code=400,
                            detail=f"Maximal {webhook_service.MAX_WEBHOOKS_PER_USER} Webhooks pro Benutzer")
    events = _normalized_events(data.events)
    url = await _validated_url(data.url)
    wh = await webhook_service.create_webhook(db, current_user.id, data.name, url, events,
                                              scope=data.scope, is_active=data.is_active)
    await write_audit(db, "WEBHOOK_CREATE", "webhook", wh.name, user_id=current_user.id, details={
        "webhook_id": wh.id, "url_host": webhook_service.url_host(url), "events": events,
        "scope": data.scope, "is_active": bool(data.is_active),
    })
    return {"webhook": webhook_out(wh, {}, show_url=True), "secret": wh.secret, "warning": CREATED_WARNING}


# --------------------------------------------------------------------------- 3.3 Aendern / Aktiv / Rotation
@router.put("/{webhook_id}", response_model=WebhookUpdateOut, response_model_exclude_unset=True)
async def update_my_webhook(
    webhook_id: int,
    data: WebhookUpdate,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Nur gesetzte Felder werden geaendert. Deaktivieren verwirft offene Zustellungen (``webhook_inactive``),
    die Rotation signiert offene Zustellungen neu; ``new_secret`` steht nur nach einer Rotation in der Antwort."""
    from app.core.secrets import is_unreadable
    from app.services import webhook_outbox, webhook_service
    from app.services.audit import write_audit

    wh = await _load_webhook(db, current_user, webhook_id)
    # Zuerst alles pruefen, dann aendern
    events = _normalized_events(data.events) if data.events is not None else None
    new_url = await _validated_url(data.url) if data.url is not None else None

    changed: dict = {}
    if data.name is not None and data.name != wh.name:
        changed["name"] = {"from": wh.name, "to": data.name}
        wh.name = data.name
    if new_url is not None:
        old_url = wh.url
        old_readable = bool(old_url) and not is_unreadable(old_url)
        if not old_readable or new_url != str(old_url).strip():
            old_host = webhook_service.url_host(old_url) if old_readable else None
            new_host = webhook_service.url_host(new_url)
            if old_host != new_host:
                changed["url_host"] = {"from": old_host or None, "to": new_host}
            else:
                changed["url"] = "changed"  # nur Pfad/Query/Userinfo – kein Inhalt ins Audit
            wh.url = new_url
    if events is not None and events != _events_list(wh.events):
        changed["events"] = {"from": _events_list(wh.events), "to": events}
        wh.events = events
    if data.scope is not None and data.scope != (wh.scope or "own"):
        changed["scope"] = {"from": wh.scope or "own", "to": data.scope}
        wh.scope = data.scope
    cancelled: Optional[int] = None
    if data.is_active is not None and bool(data.is_active) != bool(wh.is_active):
        changed["is_active"] = {"from": bool(wh.is_active), "to": bool(data.is_active)}
        wh.is_active = bool(data.is_active)
        if not data.is_active:
            cancelled = await webhook_outbox.cancel_pending(db, wh.id)
    new_secret: Optional[str] = None
    resigned = 0
    if data.rotate_secret:
        wh.secret = webhook_service.generate_webhook_secret()
        new_secret = wh.secret
    if changed or new_secret:
        wh.updated_at = utcnow()
    await db.flush()
    if new_secret:
        resigned = await webhook_outbox.resign_pending(db, wh)

    if changed:
        details: dict = {"webhook_id": wh.id, "changed": changed}
        if cancelled is not None:
            details["cancelled_pending"] = cancelled
        await write_audit(db, "WEBHOOK_UPDATE", "webhook", wh.name, user_id=current_user.id, details=details)
    if new_secret:
        await write_audit(db, "WEBHOOK_SECRET_ROTATE", "webhook", wh.name, user_id=current_user.id,
                          details={"webhook_id": wh.id, "resigned_pending": resigned})

    stats = (await webhook_outbox.stats_for_user(db, current_user.id)).get(wh.id)
    out = webhook_out(wh, stats, show_url=True)
    if new_secret:
        out["new_secret"] = new_secret
    return out


# --------------------------------------------------------------------------- 3.4 Loeschen
@router.delete("/{webhook_id}", response_model=WebhookDeletedOut)
async def delete_my_webhook(
    webhook_id: int,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Loescht den Webhook samt seinem Zustellprotokoll."""
    from app.core.secrets import is_unreadable
    from app.services import webhook_service
    from app.services.audit import write_audit

    wh = await _load_webhook(db, current_user, webhook_id)
    name = wh.name
    host = webhook_service.url_host(wh.url) if wh.url and not is_unreadable(wh.url) else None
    found, deleted = await webhook_service.delete_webhook(db, current_user.id, wh.id)
    if not found:  # pragma: no cover - gerade geladen
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    await write_audit(db, "WEBHOOK_DELETE", "webhook", name, user_id=current_user.id, details={
        "webhook_id": webhook_id, "url_host": host, "deleted_deliveries": deleted,
    })
    return {"message": "Webhook gelöscht", "deleted_deliveries": deleted}


# --------------------------------------------------------------------------- 3.5 Test senden
def _cooldown_left(webhook_id: int, now_mono: float) -> int:
    for wid, ts in list(_last_test.items()):
        if now_mono - ts >= TEST_COOLDOWN_SECONDS:
            _last_test.pop(wid, None)
    last = _last_test.get(webhook_id)
    if last is None:
        return 0
    return max(1, math.ceil(TEST_COOLDOWN_SECONDS - (now_mono - last)))


@router.post("/{webhook_id}/test", response_model=WebhookTestOut)
async def test_my_webhook(
    webhook_id: int,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Synchrone Test-Zustellung (Ereignis ``webhook.test``) – auch fuer inaktive Webhooks, ohne Ereignisfilter.

    Ergebnis steht im Zustellprotokoll (eine Zeile, ``max_attempts = 1``). Antwort immer 200 mit ``success``.
    """
    from app.services import webhook_sender, webhook_worker
    from app.services.webhook_outbox import create_test_delivery

    wh = await _load_webhook(db, current_user, webhook_id)
    now_mono = time.monotonic()
    wait = _cooldown_left(wh.id, now_mono)
    if wait:
        raise HTTPException(status_code=429, detail=f"Bitte {wait} Sekunden warten, bevor du erneut einen Test sendest")
    _last_test[wh.id] = now_mono

    d = await create_test_delivery(db, wh, actor=current_user)
    if d.status == "in_progress":
        result = await webhook_sender.send_delivery(
            url=str(wh.url).strip(),
            body=(d.body or "").encode("ascii"),
            headers=webhook_sender.build_request_headers(event=d.event, delivery_id=d.delivery_id, attempt=1,
                                                         signature=d.signature),
        )
        status = webhook_worker.apply_result(d, wh, result, utcnow())
        webhook_worker.record_attempt(status, result.duration_ms / 1000, result.status_code)
    await db.flush()
    success = d.status == "succeeded"
    if success:
        message = f"Test erfolgreich (HTTP {d.last_status_code}, {d.last_duration_ms} ms)"
    else:
        message = f"Test fehlgeschlagen: {d.last_error or d.last_error_code or 'unbekannter Fehler'}"
    return {"success": success, "message": message, "delivery": delivery_out(d, webhook_active=bool(wh.is_active))}


# --------------------------------------------------------------------------- 3.6 Zustellprotokoll
@router.get("/{webhook_id}/deliveries", response_model=DeliveryListOut)
async def list_my_webhook_deliveries(
    webhook_id: int,
    db: DbRead,
    current_user: User = Depends(get_session_user),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    status: Optional[DeliveryStatus] = Query(None),
    event: Optional[str] = Query(None, max_length=64, pattern=r"^[a-z_.]+$"),
):
    """Zustellungen eines Webhooks, neueste zuerst (ohne Body)."""
    wh = await _load_webhook(db, current_user, webhook_id)
    conds = [WebhookDelivery.webhook_id == wh.id, WebhookDelivery.user_id == current_user.id]
    if status:
        conds.append(WebhookDelivery.status == status)
    if event:
        conds.append(WebhookDelivery.event == event)
    total = int((await db.execute(select(func.count()).select_from(WebhookDelivery).where(*conds))).scalar() or 0)
    rows = (await db.execute(
        select(WebhookDelivery).where(*conds)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .limit(limit).offset(offset)
    )).scalars().all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "deliveries": [delivery_out(d, webhook_active=bool(wh.is_active)) for d in rows],
    }


# --------------------------------------------------------------------------- 3.7 Einzelne Zustellung
@router.get("/{webhook_id}/deliveries/{delivery_pk}", response_model=DeliveryDetailOut)
async def get_my_webhook_delivery(
    webhook_id: int,
    delivery_pk: int,
    db: DbRead,
    current_user: User = Depends(get_session_user),
):
    """Zustellung mit gesendetem Body und den (rekonstruierten) Request-Headern."""
    from app.services.webhook_sender import build_request_headers

    wh = await _load_webhook(db, current_user, webhook_id)
    d = await _load_delivery(db, current_user, wh, delivery_pk)
    try:
        body = json.loads(d.body or "")
    except ValueError:
        body = d.body
    out = delivery_out(d, webhook_active=bool(wh.is_active))
    out["body"] = body
    out["request_headers"] = build_request_headers(event=d.event, delivery_id=d.delivery_id,
                                                   attempt=int(d.attempts or 1) or 1, signature=d.signature or "")
    return out


# --------------------------------------------------------------------------- 3.8 Erneut senden
@router.post("/{webhook_id}/deliveries/{delivery_pk}/retry", response_model=DeliveryRetryOut)
async def retry_my_webhook_delivery(
    webhook_id: int,
    delivery_pk: int,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """``failed`` sofort erneut versuchen; ``dead``/``succeeded``/``cancelled`` mit einem weiteren Versuch."""
    from app.services.audit import write_audit
    from app.services.webhook_outbox import RetryNotAllowed, retry_delivery

    wh = await _load_webhook(db, current_user, webhook_id)
    d = await _load_delivery(db, current_user, wh, delivery_pk)
    previous = d.status
    try:
        await retry_delivery(db, wh, d)
    except RetryNotAllowed as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await write_audit(db, "WEBHOOK_DELIVERY_RETRY", "webhook", wh.name, user_id=current_user.id, details={
        "webhook_id": wh.id, "delivery_id": d.delivery_id, "event": d.event, "previous_status": previous,
    })
    return {"message": "Zustellung neu eingeplant", "delivery": delivery_out(d, webhook_active=bool(wh.is_active))}
