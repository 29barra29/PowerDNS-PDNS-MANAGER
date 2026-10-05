"""HTTP-Versand einer Webhook-Zustellung (F6 5.5).

Ein Aufruf von ``send_delivery`` ist genau ein Zustellversuch: der gespeicherte Body wird byte-identisch per
POST an die (entschluesselte) Ziel-URL geschickt, das Ergebnis kommt als ``SendResult`` zurueck. Status und
Backoff setzt der Worker (``webhook_worker.apply_result``) – dieses Modul kennt keine Datenbank.

Schutzmassnahmen:
- **SSRF bei jedem Versuch:** Aufloesung und Pruefung der Ziel-IPs im Thread (``asyncio.to_thread``, nie
  ``getaddrinfo`` im Event-Loop); gesendet wird an die gepruefte IP (Pinning gegen DNS-Rebinding), Host-Header
  und SNI bleiben beim Hostnamen. Ein blockiertes Ziel ist endgueltig (``ssrf_blocked``, sofort ``dead``).
- **Keine Weiterleitungen, kein Proxy** (``follow_redirects=False``, ``trust_env=False``).
- **Begrenztes Lesen:** hoechstens ``EXCERPT_BYTES`` Rohbytes der Antwort (``aiter_raw``, keine Dekompression,
  ``Accept-Encoding: identity``), danach wird die Verbindung geschlossen.
- **Gesamtzeit** je Versuch hoechstens ``TOTAL_TIMEOUT`` Sekunden (schuetzt gegen langsames Tropfen).
- **Fehlertexte ohne Geheimnisse:** nie ``str(exc)`` ungefiltert; URLs werden durch ihren Host ersetzt,
  Userinfo entfernt (``safe_error``). Logs nennen nur den Host.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services import webhook_service

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
TOTAL_TIMEOUT = 30.0
EXCERPT_BYTES = 1024
MAX_ERROR_TEXT = 200
MAX_RETRY_AFTER_SECONDS = 24 * 3600

# Vollstaendige Liste der Fehlercodes (auch fuer die Uebersetzungen der UI, ``webhooks.errorCode.*``).
# Die letzten sieben setzen Outbox/Worker, nicht der Sender.
ERROR_CODES: tuple[str, ...] = (
    "http_status", "redirect", "gone", "connect_error", "timeout", "total_timeout", "dns_error",
    "ssrf_blocked", "invalid_url", "internal_error",
    "webhook_inactive", "webhook_deleted", "owner_inactive", "interrupted", "secret_unreadable",
    "url_unreadable",
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>]+")
_USERINFO_IN_TEXT = re.compile(r"[^\s/@'\"<>]+:[^\s/@'\"<>]*@")


@dataclass
class SendResult:
    """Ergebnis eines Zustellversuchs."""

    ok: bool
    status_code: Optional[int]
    error_code: Optional[str]  # None bei ok
    error: Optional[str]  # deutscher Text ohne URL/Userinfo
    excerpt: Optional[str]  # <= 1024 Zeichen, Steuerzeichen entfernt
    duration_ms: int
    permanent: bool = False  # sofort dead (kein weiterer Versuch)
    retry_after: Optional[int] = None  # Sekunden (nur bei 429/503 mit Retry-After)


def build_request_headers(*, event: str, delivery_id: str, attempt: int, signature: str) -> dict[str, str]:
    """Header eines Zustellversuchs (Namen sind Vertrag gegenueber den Empfaengern)."""
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": f"PDNS-Manager-Webhook/{settings.APP_VERSION}",
        "Accept-Encoding": "identity",
        "X-DNS-Manager-Signature": signature,
        "X-DNS-Manager-Event": event,
        "X-DNS-Manager-Delivery": delivery_id,
        "X-DNS-Manager-Attempt": str(attempt),
    }


def _default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
        follow_redirects=False,
        trust_env=False,
    )


# In Tests per monkeypatch durch einen Client mit ``httpx.MockTransport`` ersetzbar.
_client_factory = _default_client_factory


def _ms(t0: float) -> int:
    return max(0, int(round((time.monotonic() - t0) * 1000)))


def _url_host_safe(url: str) -> str:
    return webhook_service.url_host(url) or "?"


def safe_error(prefix: str, exc: Optional[BaseException] = None, *urls: str) -> str:
    """``"<prefix> (<Klassenname>): <bereinigter Text>"`` – ohne URL-Pfade, Query und Userinfo, max. 200 Zeichen.

    Jede URL im Text wird durch ihren Host ersetzt; bekannte URLs (Ziel, gepinnte Ziele) zusaetzlich direkt.
    """
    text = prefix if exc is None else f"{prefix} ({type(exc).__name__})"
    detail = ""
    if exc is not None:
        try:
            detail = str(exc) or ""
        except Exception:  # noqa: BLE001 - kaputtes __str__ darf nichts verhindern
            detail = ""
    if detail:
        for u in sorted((u for u in urls if u), key=len, reverse=True):
            detail = detail.replace(u, _url_host_safe(u))
        detail = _URL_IN_TEXT.sub(lambda m: _url_host_safe(m.group(0)), detail)
        detail = _USERINFO_IN_TEXT.sub("", detail)
        detail = _CONTROL_CHARS.sub("", detail).strip()
        if detail:
            text = f"{text}: {detail}"
    return text[:MAX_ERROR_TEXT]


def _parse_retry_after(value: Optional[str]) -> Optional[int]:
    """``Retry-After`` als Sekunden (Ganzzahl oder HTTP-Datum), sonst ``None``."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v.isdigit():
        return min(int(v), MAX_RETRY_AFTER_SECONDS)
    try:
        when = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    secs = int((when - datetime.now(timezone.utc)).total_seconds())
    return min(max(secs, 0), MAX_RETRY_AFTER_SECONDS)


def _clean_excerpt(raw: bytes) -> Optional[str]:
    text = raw[:EXCERPT_BYTES].decode("utf-8", "replace")
    text = _CONTROL_CHARS.sub("", text)[:EXCERPT_BYTES]
    return text or None


async def _read_excerpt(response: httpx.Response) -> Optional[str]:
    """Hoechstens ``EXCERPT_BYTES`` Rohbytes lesen, danach abbrechen (das Kontextende schliesst die Verbindung)."""
    try:
        preloaded = response.content  # nur bei bereits gelesenen Antworten (z. B. Test-Transport)
    except httpx.ResponseNotRead:
        preloaded = None
    if preloaded is not None:
        return _clean_excerpt(preloaded[:EXCERPT_BYTES])
    buf = bytearray()
    async for chunk in response.aiter_raw():
        buf.extend(chunk)
        if len(buf) >= EXCERPT_BYTES:
            break
    return _clean_excerpt(bytes(buf))


def _result(ok: bool, code: Optional[int], error_code: Optional[str], error: Optional[str],
            excerpt: Optional[str], t0: float, *, permanent: bool = False,
            retry_after: Optional[int] = None) -> SendResult:
    return SendResult(ok=ok, status_code=code, error_code=error_code, error=error, excerpt=excerpt,
                      duration_ms=_ms(t0), permanent=permanent, retry_after=retry_after)


def classify(code: int, excerpt: Optional[str], t0: float, retry_after: Optional[int]) -> SendResult:
    """HTTP-Status -> Ergebnis: 2xx ok; 410 endgueltig; 3xx und alles andere wiederholbar."""
    if 200 <= code <= 299:
        return _result(True, code, None, None, excerpt, t0)
    if code == 410:
        return _result(False, code, "gone", "Empfänger meldet „Gone“ (HTTP 410) – Zustellung endgültig aufgegeben",
                       excerpt, t0, permanent=True)
    if 300 <= code <= 399:
        return _result(False, code, "redirect", f"Weiterleitung (HTTP {code}) wird nicht verfolgt", excerpt, t0)
    return _result(False, code, "http_status", f"HTTP {code}", excerpt, t0,
                   retry_after=retry_after if code in (429, 503) else None)


async def _send(url: str, body: bytes, headers: dict[str, str], t0: float) -> SendResult:
    try:
        parsed = urlparse(url or "")
        scheme_ok = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        scheme_ok = False
    if not scheme_ok:
        return _result(False, None, "invalid_url", "Ungültige Ziel-URL (nur http:// oder https:// erlaubt)",
                       None, t0, permanent=True)
    host = _url_host_safe(url)
    try:
        targets = await asyncio.to_thread(webhook_service.pin_webhook_targets, url)
    except webhook_service.WebhookTargetBlocked:
        return _result(False, None, "ssrf_blocked", "Ziel blockiert: private/interne Adresse (SSRF-Schutz)",
                       None, t0, permanent=True)
    except webhook_service.WebhookResolveError:
        return _result(False, None, "dns_error", f"Host nicht auflösbar: {host}", None, t0)
    except ValueError:
        return _result(False, None, "invalid_url", f"Ungültiger Ziel-Host: {host}", None, t0, permanent=True)
    if not targets:
        return _result(False, None, "dns_error", f"Host nicht auflösbar: {host}", None, t0)

    pinned = [t[0] for t in targets]
    code: Optional[int] = None
    excerpt: Optional[str] = None
    retry_after: Optional[int] = None
    async with _client_factory() as client:
        for idx, (pinned_url, extra_headers, extensions) in enumerate(targets):
            try:
                async with client.stream("POST", pinned_url, content=body,
                                         headers={**headers, **(extra_headers or {})},
                                         extensions=extensions or {}) as r:
                    code = r.status_code
                    retry_after = _parse_retry_after(r.headers.get("retry-after"))
                    try:
                        excerpt = await _read_excerpt(r)
                    except httpx.HTTPError:
                        excerpt = None  # Status liegt vor; ein abgebrochener Body aendert das Ergebnis nicht
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if idx + 1 < len(targets):
                    continue  # naechste gepruefte Adresse (z. B. IPv6 -> IPv4)
                prefix = "Verbindungsaufbau fehlgeschlagen" if isinstance(exc, httpx.ConnectError) \
                    else "Zeitüberschreitung beim Verbindungsaufbau"
                return _result(False, None, "connect_error", safe_error(prefix, exc, url, *pinned), None, t0)
            except httpx.TimeoutException as exc:
                return _result(False, None, "timeout", safe_error("Zeitüberschreitung", exc, url, *pinned), None, t0)
            except httpx.HTTPError as exc:
                return _result(False, None, "connect_error", safe_error("Übertragungsfehler", exc, url, *pinned),
                               None, t0)
    if code is None:  # pragma: no cover - Schleife endet immer mit break oder return
        return _result(False, None, "internal_error", "Kein Ergebnis", None, t0)
    return classify(code, excerpt, t0, retry_after)


async def send_delivery(*, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
    """Einen Zustellversuch ausfuehren. Wirft nie (ausser ``CancelledError`` beim Herunterfahren)."""
    t0 = time.monotonic()
    try:
        return await asyncio.wait_for(_send(url, body, headers, t0), TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        return _result(False, None, "total_timeout", f"Gesamtzeit von {TOTAL_TIMEOUT:g} s überschritten", None, t0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - ein Versuch darf den Worker nie abbrechen
        # Kein logger.exception: der Text koennte die URL enthalten. Typ + Host reichen fuer die Diagnose.
        logger.error("Webhook-Versand an %s: unerwarteter Fehler (%s)", _url_host_safe(url), type(exc).__name__)
        logger.debug("Webhook-Versand: Details", exc_info=True)
        return _result(False, None, "internal_error", f"Interner Fehler beim Versand ({type(exc).__name__})",
                       None, t0)
