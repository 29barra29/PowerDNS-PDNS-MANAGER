"""Webhook-Verwaltung des eigenen Kontos – eigener Router seit 3.0 (Bauplan B.13, ROUTER_ORDER 30).

Pfade unveraendert gegenueber 2.4.1 (``/api/v1/auth/me/webhooks…``). Welle 0b zieht nur die vier
Bestandsrouten aus ``routers/auth.py`` hierher und setzt die Rechte nach F14 3.10/F6 3.1–3.4:
Anlegen, Aendern und Loeschen nur mit Browser-Session (``get_session_user``); die Liste ist auch per
Panel-Token lesbar, dann ohne Ziel-URL (``url = null`` – Slack/Teams/Discord-URLs sind selbst Geheimnisse).
Zustellprotokoll, Test und Retry baut WS-F6-BE in Welle 1 hier weiter aus.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete

from app.core.auth import get_current_user, get_session_user
from app.core.database import DbRead, DbWrite
from app.core.timeutil import iso_utc
from app.models.models import User, WebhookDelivery

ROUTER_ORDER = 30

router = APIRouter(prefix="/auth/me/webhooks", tags=["Webhooks"])

CREATED_WARNING = (
    "Das Shared Secret für HMAC (Header X-DNS-Manager-Signature) – nur in dieser Antwort; "
    "bei Verlust: rotate_secret im PUT nutzen"
)


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=5, max_length=1024)
    events: list[str] = Field(default_factory=lambda: ["*"])


class WebhookUpdateBody(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    events: Optional[list[str]] = None
    is_active: Optional[bool] = None
    rotate_secret: bool = False


def _is_session(request: Request) -> bool:
    return getattr(request.state, "auth_via", None) == "session"


@router.get("")
async def list_my_webhooks(
    request: Request,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Eigene Webhooks; per Panel-Token ohne Ziel-URL."""
    from app.services import webhook_service as wh

    show_url = _is_session(request)
    rows = await wh.list_webhooks(db, current_user.id)
    return {
        "webhooks": [
            {
                "id": w.id,
                "name": w.name,
                "url": w.url if show_url else None,
                "events": w.events or ["*"],
                "is_active": w.is_active,
                "has_secret": bool(w.secret),
                "created_at": iso_utc(w.created_at),
            }
            for w in rows
        ]
    }


@router.post("", status_code=201)
async def create_my_webhook(
    data: WebhookCreate,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    from app.services import webhook_service as wh

    try:
        w = await wh.create_webhook(db, current_user.id, data.name, data.url, data.events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "webhook": {
            "id": w.id,
            "name": w.name,
            "url": w.url,
            "events": w.events,
            "is_active": w.is_active,
        },
        "secret": w.secret,
        "warning": CREATED_WARNING,
    }


@router.put("/{webhook_id}")
async def update_my_webhook(
    webhook_id: int,
    data: WebhookUpdateBody,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    from app.services import webhook_service as wh

    try:
        w = await wh.update_webhook(
            db,
            current_user.id,
            webhook_id,
            name=data.name,
            url=data.url,
            events=data.events,
            is_active=data.is_active,
            new_secret=data.rotate_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not w:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    out: dict = {
        "id": w.id,
        "name": w.name,
        "url": w.url,
        "events": w.events,
        "is_active": w.is_active,
    }
    if data.rotate_secret:
        out["new_secret"] = w.secret
    return out


@router.delete("/{webhook_id}")
async def delete_my_webhook(
    webhook_id: int,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Loescht den Webhook samt seinen Zustellungen (Outbox-Zeilen, F6 3.4)."""
    from app.services import webhook_service as wh

    if not await wh.delete_webhook(db, current_user.id, webhook_id):
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    res = await db.execute(
        sql_delete(WebhookDelivery).where(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.user_id == current_user.id,
        )
    )
    return {"message": "Webhook gelöscht", "deleted_deliveries": int(res.rowcount or 0)}
