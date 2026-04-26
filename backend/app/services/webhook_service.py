"""Outbound-Webhooks: POST JSON, HMAC-SHA256 in X-DNS-Manager-Signature."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session
from app.models.models import Webhook

logger = logging.getLogger(__name__)

SIG_HEADER = "X-DNS-Manager-Signature"
EVENT_VERSION = 1
PRIVATE_WEBHOOK_ERROR = (
    "Webhook-Ziel darf nicht auf localhost, private IPs oder interne Netze zeigen "
    "(WEBHOOK_ALLOW_PRIVATE_URLS=true nur setzen, wenn du das bewusst brauchst)."
)


def _matches_subscription(subscribed: str, event: str) -> bool:
    s = (subscribed or "").strip()
    if not s or s == "*":
        return True
    if s.endswith("*") and s != "*":
        p = s[:-1]
        return event.startswith(p) or event == p
    return event == s or event.startswith(f"{s}.")


def _webhook_wants(wh: Webhook, event: str) -> bool:
    evs = wh.events
    if not evs or evs == ["*"]:
        return True
    for s in evs:
        if _matches_subscription(str(s), event):
            return True
    return False


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def validate_webhook_url(url: str) -> str:
    """Validiert Webhook-URL und blockt standardmäßig private/internal Ziele."""
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL muss mit http:// oder https:// beginnen")
    if settings.WEBHOOK_ALLOW_PRIVATE_URLS:
        return value

    host = parsed.hostname.strip("[]")
    candidates: set[str] = set()
    try:
        candidates.add(str(ipaddress.ip_address(host)))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            candidates.update(info[4][0] for info in infos)
        except socket.gaierror as exc:
            raise ValueError(f"Webhook-Host konnte nicht aufgelöst werden: {host}") from exc

    for raw_ip in candidates:
        ip = ipaddress.ip_address(raw_ip)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError(PRIVATE_WEBHOOK_ERROR)
    return value


def _build_payload(event: str, data: Dict[str, Any], actor_user_id: int) -> bytes:
    body = {
        "v": EVENT_VERSION,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "app": settings.APP_NAME,
        "actor_user_id": actor_user_id,
        "data": data,
    }
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def _post_one(wh: Webhook, event: str, data: Dict[str, Any], actor_user_id: int) -> None:
    try:
        target_url = validate_webhook_url(wh.url)
    except ValueError as e:
        logger.warning("Webhook %s blocked: %s", wh.id, e)
        return
    raw = _build_payload(event, data, actor_user_id)
    sig = hmac.new(wh.secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                target_url,
                content=raw,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    f"{SIG_HEADER}": f"sha256={sig}",
                },
            )
            if r.status_code >= 400:
                logger.warning("Webhook %s -> %s %s", wh.id, r.status_code, (r.text or "")[:200])
    except Exception as e:
        logger.warning("Webhook %s failed: %s", wh.id, e)


async def deliver_webhooks_background(user_id: int, event: str, data: Dict[str, Any]) -> None:
    """Lädt aktive Webhooks des Users und feuert asynchron (Fire-and-forget)."""

    async def _run() -> None:
        try:
            async with async_session() as db:
                r = await db.execute(
                    select(Webhook).where(Webhook.user_id == user_id, Webhook.is_active.is_(True))
                )
                rows: List[Webhook] = list(r.scalars().all())
                to_send = [w for w in rows if _webhook_wants(w, event)]
                await asyncio.gather(
                    *(_post_one(w, event, data, user_id) for w in to_send),
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error("webhook background task: %s", e)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        asyncio.run(_run())


# Kurz-API für Router (nur Verwaltung)
async def list_webhooks(db: AsyncSession, user_id: int) -> List[Webhook]:
    r = await db.execute(
        select(Webhook)
        .where(Webhook.user_id == user_id)
        .order_by(Webhook.id.desc())
    )
    return list(r.scalars().all())


async def create_webhook(
    db: AsyncSession, user_id: int, name: str, url: str, events: List[str]
) -> Webhook:
    safe_url = validate_webhook_url(url)
    wh = Webhook(
        user_id=user_id,
        name=name[:100],
        url=safe_url[:1024],
        secret=generate_webhook_secret(),
        events=events or ["*"],
    )
    db.add(wh)
    await db.flush()
    return wh


async def update_webhook(
    db: AsyncSession,
    user_id: int,
    wh_id: int,
    *,
    name: Optional[str] = None,
    url: Optional[str] = None,
    events: Optional[List[str]] = None,
    is_active: Optional[bool] = None,
    new_secret: bool = False,
) -> Optional[Webhook]:
    r = await db.execute(select(Webhook).where(Webhook.id == wh_id, Webhook.user_id == user_id))
    wh = r.scalar_one_or_none()
    if not wh:
        return None
    if name is not None:
        wh.name = name[:100]
    if url is not None:
        wh.url = validate_webhook_url(url)[:1024]
    if events is not None:
        wh.events = events
    if is_active is not None:
        wh.is_active = is_active
    if new_secret:
        wh.secret = generate_webhook_secret()
    await db.flush()
    return wh


async def delete_webhook(db: AsyncSession, user_id: int, wh_id: int) -> bool:
    r = await db.execute(select(Webhook).where(Webhook.id == wh_id, Webhook.user_id == user_id))
    wh = r.scalar_one_or_none()
    if not wh:
        return False
    await db.delete(wh)
    await db.flush()
    return True
