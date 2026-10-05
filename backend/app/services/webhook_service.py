"""Webhook-Verwaltung und Ziel-Pruefung (SSRF-Schutz, DNS-Pinning, Signatur).

Seit 3.0 wird nicht mehr direkt aus dem Request gesendet: Ereignisse landen ueber
``services.webhook_outbox.enqueue_event`` in der Tabelle ``webhook_deliveries`` und werden vom
Hintergrund-Worker (``services.webhook_worker``) zugestellt (F6, Bauplan B.8). Dieses Modul liefert
die Bausteine dafuer: URL-Pruefung, Pinning der geprueften IPs, HMAC-Signatur, Anzeige-Helfer und die
Verwaltungs-Helfer der Webhook-Routen.

``validate_webhook_url`` und ``pin_webhook_targets`` loesen DNS synchron auf – Aufrufer im Event-Loop
nutzen ``asyncio.to_thread``.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
from typing import List, Optional
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Webhook

logger = logging.getLogger(__name__)

SIG_HEADER = "X-DNS-Manager-Signature"
MAX_WEBHOOKS_PER_USER = 20
PRIVATE_WEBHOOK_ERROR = (
    "Webhook-Ziel darf nicht auf localhost, private IPs oder interne Netze zeigen "
    "(WEBHOOK_ALLOW_PRIVATE_URLS=true nur setzen, wenn du das bewusst brauchst)."
)


class WebhookTargetBlocked(ValueError):
    """Ziel loest auf eine private/interne Adresse auf (SSRF-Schutz) – dauerhaft, kein Retry."""


class WebhookResolveError(ValueError):
    """Ziel-Host ist (derzeit) nicht aufloesbar – beim Zustellen wiederholbar."""


def sign(secret: str, raw: bytes) -> str:
    """HMAC-SHA256 ueber die exakt gesendeten Bytes: ``"sha256=" + hex`` (Header X-DNS-Manager-Signature)."""
    return "sha256=" + hmac.new((secret or "").encode("utf-8"), raw, hashlib.sha256).hexdigest()


def url_host(url: Optional[str]) -> str:
    """Host[:Port] der URL ohne Userinfo, IDN als A-Label, IPv6 in Klammern; ungueltig -> ``""``."""
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").strip("[]")
        port = parsed.port
    except ValueError:
        return ""
    if not host:
        return ""
    try:
        ip = ipaddress.ip_address(host)
        host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            host = host.lower()
    return f"{host}:{port}" if port else host


def url_display(url: Optional[str]) -> str:
    """Anzeigeform ohne Geheimnisse: ``https://host[:port]/…`` (Pfad/Query weggelassen; Slack-/Teams-URLs
    tragen das Geheimnis im Pfad). Leere oder ungueltige URL -> ``""``."""
    raw = (url or "").strip()
    host = url_host(raw)
    if not host:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    rest = "/…" if (parsed.path not in ("", "/") or parsed.query or parsed.fragment) else ""
    return f"{scheme}://{host}{rest}"


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    # is_global deckt zusaetzlich CGNAT (100.64/10), NAT64 (64:ff9b::/96) und aehnliche
    # Bereiche ab, die von is_private/is_reserved nicht erfasst werden.
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    )


def _resolve_checked(url: str) -> tuple[str, list[str], bool]:
    """Liefert (host als A-Label, [geprüfte IPs], host_is_literal).

    Wirft ``WebhookTargetBlocked`` (private/interne Adresse), ``WebhookResolveError`` (nicht aufloesbar)
    bzw. ``ValueError`` (ungueltiger Host) – alle sind ``ValueError``.
    """
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip("[]")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    candidates: list[str] = []
    is_literal = False
    try:
        candidates.append(str(ipaddress.ip_address(host)))
        is_literal = True
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")  # Umlaut-Domains -> A-Label (Host-Header/SNI)
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"Ungültiger Webhook-Host: {host}") from exc
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            for info in infos:
                if info[4][0] not in candidates:
                    candidates.append(info[4][0])
        except socket.gaierror as exc:
            raise WebhookResolveError(f"Webhook-Host konnte nicht aufgelöst werden: {host}") from exc
    for raw_ip in candidates:
        if _is_blocked_ip(ipaddress.ip_address(raw_ip)):
            raise WebhookTargetBlocked(PRIVATE_WEBHOOK_ERROR)
    return host, candidates, is_literal


def validate_webhook_url(url: str) -> str:
    """Validiert Webhook-URL und blockt standardmäßig private/internal Ziele."""
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL muss mit http:// oder https:// beginnen")
    if settings.WEBHOOK_ALLOW_PRIVATE_URLS:
        return value
    _resolve_checked(value)
    return value


def pin_webhook_targets(url: str) -> list[tuple[str, dict, dict]]:
    """Baut Ziel-URLs mit den GEPRUEFTEN IPs statt des Hostnamens (eine pro Adresse).

    Verhindert DNS-Rebinding: Ohne Pinning koennte der Angreifer-DNS bei der Pruefung
    eine oeffentliche IP und beim eigentlichen Connect 127.0.0.1 liefern. Host-Header
    und SNI bleiben auf dem Original-Hostnamen, damit TLS und virtuelle Hosts passen;
    Basic-Auth-Userinfo der URL bleibt erhalten. IP-Literale brauchen kein Pinning.
    Liefert [(url, extra_headers, httpx_extensions), ...] in Aufloesungs-Reihenfolge.
    """
    if settings.WEBHOOK_ALLOW_PRIVATE_URLS:
        return [(url, {}, {})]
    parsed = urlparse(url)
    host, ips, is_literal = _resolve_checked(url)
    if is_literal:
        return [(url, {}, {})]
    userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    host_header = host if not parsed.port else f"{host}:{parsed.port}"
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    out: list[tuple[str, dict, dict]] = []
    for ip in ips:
        is_v6 = isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address)
        netloc_ip = f"[{ip}]" if is_v6 else ip
        if parsed.port:
            netloc_ip += f":{parsed.port}"
        pinned = parsed._replace(netloc=userinfo + netloc_ip).geturl()
        out.append((pinned, {"Host": host_header}, extensions))
    return out


# Verwaltungs-Helfer der Webhook-Routen (routers/webhooks.py). Sie pruefen KEINE URL: der Router validiert sie
# vorher mit ``await asyncio.to_thread(validate_webhook_url, url)`` (DNS-Aufloesung nie im Event-Loop, F6 5.6).
async def list_webhooks(db: AsyncSession, user_id: int) -> List[Webhook]:
    r = await db.execute(
        select(Webhook)
        .where(Webhook.user_id == user_id)
        .order_by(Webhook.id.desc())
    )
    return list(r.scalars().all())


async def get_webhook(db: AsyncSession, user_id: int, wh_id: int) -> Optional[Webhook]:
    """Webhook des Benutzers oder ``None`` (fremde IDs verhalten sich wie nicht vorhandene)."""
    r = await db.execute(select(Webhook).where(Webhook.id == wh_id, Webhook.user_id == user_id))
    return r.scalar_one_or_none()


async def count_webhooks(db: AsyncSession, user_id: int) -> int:
    r = await db.execute(select(func.count()).select_from(Webhook).where(Webhook.user_id == user_id))
    return int(r.scalar() or 0)


async def create_webhook(
    db: AsyncSession,
    user_id: int,
    name: str,
    url: str,
    events: List[str],
    *,
    scope: str = "own",
    is_active: bool = True,
) -> Webhook:
    """Legt den Webhook mit neuem Secret an. ``url`` muss bereits geprueft sein (``validate_webhook_url``)."""
    wh = Webhook(
        user_id=user_id,
        name=name[:100],
        url=url[:1024],
        secret=generate_webhook_secret(),
        events=list(events or ["*"]),
        is_active=bool(is_active),
        scope=scope or "own",
        consecutive_failures=0,
    )
    db.add(wh)
    await db.flush()
    return wh


async def delete_webhook(db: AsyncSession, user_id: int, wh_id: int) -> tuple[bool, int]:
    """Loescht den Webhook samt seinen Zustellungen. Rueckgabe ``(gefunden, geloeschte_zustellungen)``."""
    from app.services.webhook_outbox import delete_for_webhook

    wh = await get_webhook(db, user_id, wh_id)
    if not wh:
        return False, 0
    n = await delete_for_webhook(db, wh.id)
    await db.delete(wh)
    await db.flush()
    return True, n
