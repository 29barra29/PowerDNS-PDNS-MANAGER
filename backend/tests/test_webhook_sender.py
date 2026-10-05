"""Webhook-Versand ohne DB (F6 9.1 Nr. 6–16).

HTTP immer per ``httpx.MockTransport`` (``webhook_sender._client_factory`` ersetzt), DNS per
``webhook_service._resolve_checked`` (gepinnte Ziele). Abgedeckt: Backoff, ``apply_result``, Header und
byte-identischer Body, Klassifikation (2xx/3xx/410/429/503/5xx, Verbindungs- und Lesefehler), naechste IP bei
Verbindungsfehler, begrenztes Lesen der Antwort, Gesamt-Timeout, SSRF/DNS/ungueltige URL (Aufloesung im Thread),
Fehlertexte ohne URL-Geheimnisse, Anzeige-Helfer und die URL-Pruefung der Schemas.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import urlparse

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.models.models import Webhook, WebhookDelivery
from app.schemas.webhooks import WebhookCreate, WebhookUpdate
from app.services import webhook_sender as sender
from app.services import webhook_service as wsvc
from app.services import webhook_worker as worker

URL = "https://hooks.example.com/services/T0/B0/XXXX?token=abc"
BODY = b'{"v":2,"event":"record.created","data":{"name":"j\\u00fcrgen"}}'
HEADERS = sender.build_request_headers(event="record.created", delivery_id="d-1", attempt=3,
                                       signature="sha256=" + "a" * 64)
T0 = datetime(2026, 10, 5, 12, 0, 0)


def _use_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(sender, "_client_factory",
                        lambda: httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False))


@pytest.fixture
def resolved(monkeypatch):
    """Pinning aktiv (private Ziele verboten), DNS liefert feste oeffentliche IPs; zaehlt Aufrufe und Threads."""
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    state = {"ips": ["93.184.216.34"], "threads": []}

    def fake_resolve(url):
        state["threads"].append(threading.get_ident())
        host = urlparse(url).hostname
        return host, list(state["ips"]), False

    monkeypatch.setattr(wsvc, "_resolve_checked", fake_resolve)
    return state


async def _send(url=URL, body=BODY, headers=HEADERS):
    return await sender.send_delivery(url=url, body=body, headers=headers)


# --------------------------------------------------------------------------- Nr. 6: Backoff
def test_backoff_schedule():
    expected = {1: (60, 66), 2: (120, 132), 3: (240, 264), 4: (480, 528), 5: (960, 1056)}
    for n, (lo, hi) in expected.items():
        for _ in range(50):
            assert lo <= worker.compute_next_delay(n) <= hi, n
    assert worker.compute_next_delay(1, retry_after=600) >= 600
    assert worker.compute_next_delay(1, retry_after=99999) <= 3600 * 1.1
    assert worker.compute_next_delay(20) <= 3600 * 1.1  # gedeckelt
    assert 30 <= worker.compute_next_delay(0) <= 33


# --------------------------------------------------------------------------- Nr. 7: apply_result
def _delivery(attempts=1, max_attempts=6, status="in_progress"):
    return WebhookDelivery(id=1, delivery_id="d-1", event_id="e-1", webhook_id=5, user_id=1, event="record.created",
                           body="{}", signature="sha256=x", status=status, attempts=attempts,
                           max_attempts=max_attempts, next_attempt_at=T0, created_at=T0)


def _webhook(**kw):
    data = dict(id=5, user_id=1, name="h", url=URL, secret="s", events=["*"], is_active=True, scope="own",
                consecutive_failures=2)
    data.update(kw)
    return Webhook(**data)


def _res(ok=False, code=500, error_code="http_status", error="HTTP 500", permanent=False, retry_after=None):
    return sender.SendResult(ok=ok, status_code=code, error_code=None if ok else error_code,
                             error=None if ok else error, excerpt="antwort", duration_ms=12,
                             permanent=permanent, retry_after=retry_after)


def test_apply_result_transitions():
    # Erfolg
    d, wh = _delivery(), _webhook()
    assert worker.apply_result(d, wh, _res(ok=True, code=204), T0) == "succeeded"
    assert d.delivered_at == T0 and d.last_status_code == 204 and d.last_error_code is None
    assert wh.last_success_at == T0 and wh.consecutive_failures == 0
    assert d.last_response_excerpt == "antwort" and d.last_duration_ms == 12
    # 500 beim ersten Versuch -> failed mit Backoff
    d, wh = _delivery(attempts=1), _webhook()
    assert worker.apply_result(d, wh, _res(), T0) == "failed"
    assert T0 + timedelta(seconds=60) <= d.next_attempt_at <= T0 + timedelta(seconds=66)
    assert d.last_error_code == "http_status" and d.last_error == "HTTP 500"
    assert wh.last_failure_at == T0 and wh.consecutive_failures == 3
    # Retry-After wird respektiert
    d = _delivery(attempts=1)
    worker.apply_result(d, None, _res(code=429, retry_after=600), T0)
    assert d.next_attempt_at >= T0 + timedelta(seconds=600)
    # Budget erschoepft -> dead
    d, wh = _delivery(attempts=6), _webhook(consecutive_failures=None)
    assert worker.apply_result(d, wh, _res(), T0) == "dead"
    assert wh.consecutive_failures == 1
    # endgueltiger Fehler -> dead, auch beim ersten Versuch
    d = _delivery(attempts=1)
    assert worker.apply_result(d, None, _res(code=410, error_code="gone", permanent=True), T0) == "dead"
    # Test-Zustellung (max_attempts=1) -> dead statt failed
    d = _delivery(attempts=1, max_attempts=1)
    assert worker.apply_result(d, None, _res(), T0) == "dead"


# --------------------------------------------------------------------------- Nr. 8: Header und Body
async def test_send_success_and_headers(monkeypatch, resolved):
    seen = {}

    def handler(request: httpx.Request):
        seen["request"] = request
        seen["body"] = request.content
        return httpx.Response(200, text="ok")

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert r.ok and r.status_code == 200 and r.error_code is None and r.excerpt == "ok"
    req = seen["request"]
    assert seen["body"] == BODY  # byte-identisch
    assert req.method == "POST"
    assert req.url.host == "93.184.216.34"  # gepinnt
    assert req.url.path == "/services/T0/B0/XXXX" and req.url.query == b"token=abc"
    assert req.headers["host"] == "hooks.example.com"
    assert req.extensions.get("sni_hostname") == "hooks.example.com"
    assert req.headers["x-dns-manager-signature"] == "sha256=" + "a" * 64
    assert req.headers["x-dns-manager-event"] == "record.created"
    assert req.headers["x-dns-manager-delivery"] == "d-1"
    assert req.headers["x-dns-manager-attempt"] == "3"
    assert req.headers["user-agent"] == f"PDNS-Manager-Webhook/{settings.APP_VERSION}"
    assert req.headers["accept-encoding"] == "identity"
    assert req.headers["content-type"] == "application/json; charset=utf-8"
    # DNS-Aufloesung lief in einem Worker-Thread, nicht im Event-Loop
    assert resolved["threads"] and threading.get_ident() not in resolved["threads"]


def test_build_request_headers():
    h = sender.build_request_headers(event="webhook.test", delivery_id="abc", attempt=1, signature="sha256=1")
    assert h["X-DNS-Manager-Attempt"] == "1" and h["X-DNS-Manager-Event"] == "webhook.test"
    assert "Host" not in h


# --------------------------------------------------------------------------- Nr. 9: Klassifikation
@pytest.mark.parametrize("code,headers,error_code,permanent,retry_after", [
    (302, {"location": "https://elsewhere.example/"}, "redirect", False, None),
    (410, {}, "gone", True, None),
    (429, {"retry-after": "120"}, "http_status", False, 120),
    (503, {}, "http_status", False, None),
    (503, {"retry-after": "30"}, "http_status", False, 30),
    (500, {"retry-after": "120"}, "http_status", False, None),  # Retry-After nur bei 429/503
    (404, {}, "http_status", False, None),
])
async def test_send_classification(monkeypatch, resolved, code, headers, error_code, permanent, retry_after):
    _use_transport(monkeypatch, lambda req: httpx.Response(code, headers=headers, text="nein"))
    r = await _send()
    assert not r.ok and r.status_code == code and r.error_code == error_code
    assert r.permanent is permanent and r.retry_after == retry_after
    assert r.excerpt == "nein"
    if code == 302:
        assert "nicht verfolgt" in r.error
    if code == 500:
        assert r.error == "HTTP 500"


async def test_send_2xx_variants_are_ok(monkeypatch, resolved):
    for code in (200, 201, 202, 204, 299):
        _use_transport(monkeypatch, lambda req, c=code: httpx.Response(c))
        r = await _send()
        assert r.ok and r.status_code == code and r.excerpt is None


async def test_send_connect_error_on_all_ips(monkeypatch, resolved):
    resolved["ips"] = ["93.184.216.34", "93.184.216.35"]
    tried = []

    def handler(request):
        tried.append(request.url.host)
        raise httpx.ConnectError("Connection refused", request=request)

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert tried == ["93.184.216.34", "93.184.216.35"]
    assert r.error_code == "connect_error" and not r.permanent and r.status_code is None
    assert r.error.startswith("Verbindungsaufbau fehlgeschlagen (ConnectError)")


async def test_send_read_timeout(monkeypatch, resolved):
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert r.error_code == "timeout" and not r.permanent


async def test_send_other_transport_error(monkeypatch, resolved):
    def handler(request):
        raise httpx.RemoteProtocolError("Server disconnected", request=request)

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert r.error_code == "connect_error" and r.error.startswith("Übertragungsfehler (RemoteProtocolError)")


async def test_send_unexpected_exception_is_internal_error(monkeypatch, resolved, caplog):
    def handler(request):
        raise RuntimeError(f"kaputt bei {URL}")

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert r.error_code == "internal_error" and "RuntimeError" in r.error
    assert "T0/B0" not in r.error and "T0/B0" not in caplog.text and "token=abc" not in caplog.text


# --------------------------------------------------------------------------- Nr. 10: naechste IP
async def test_send_tries_next_ip_on_connect_error(monkeypatch, resolved):
    resolved["ips"] = ["2001:db8::1", "93.184.216.34"]

    def handler(request):
        if request.url.host == "2001:db8::1":
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200)

    _use_transport(monkeypatch, handler)
    r = await _send()
    assert r.ok and r.status_code == 200


# --------------------------------------------------------------------------- Nr. 11: begrenztes Lesen
async def test_send_bounded_read(monkeypatch, resolved):
    produced = {"n": 0}

    async def stream():
        for i in range(80):  # 80 x 64 KiB = 5 MB
            produced["n"] += 1
            prefix = b"\x00\x1bHallo\x07 " if i == 0 else b""
            yield prefix + b"y" * (64 * 1024)

    _use_transport(monkeypatch, lambda req: httpx.Response(500, content=stream()))
    r = await _send()
    assert r.status_code == 500 and r.error_code == "http_status"
    assert r.excerpt is not None and len(r.excerpt) <= 1024
    assert r.excerpt.startswith("Hallo ")  # Steuerzeichen entfernt
    assert produced["n"] <= 2


def test_clean_excerpt_handles_invalid_utf8():
    assert sender._clean_excerpt(b"\xff\xfeok") == "��ok"
    assert sender._clean_excerpt(b"") is None
    assert sender._clean_excerpt(b"\x01\x02") is None


# --------------------------------------------------------------------------- Nr. 12: Gesamt-Timeout
async def test_send_total_timeout(monkeypatch, resolved):
    monkeypatch.setattr(sender, "TOTAL_TIMEOUT", 0.2)

    async def slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200)

    _use_transport(monkeypatch, slow)
    r = await _send()
    assert r.error_code == "total_timeout" and not r.ok and not r.permanent
    assert r.duration_ms < 1500


# --------------------------------------------------------------------------- Nr. 13: SSRF, DNS, URL
async def test_send_ssrf_and_dns(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    sent = []
    _use_transport(monkeypatch, lambda req: sent.append(req) or httpx.Response(200))

    def blocked(url):
        raise wsvc.WebhookTargetBlocked(wsvc.PRIVATE_WEBHOOK_ERROR)

    monkeypatch.setattr(wsvc, "_resolve_checked", blocked)
    r = await _send()
    assert r.error_code == "ssrf_blocked" and r.permanent and "SSRF" in r.error

    def unresolvable(url):
        raise wsvc.WebhookResolveError("Webhook-Host konnte nicht aufgelöst werden: hooks.example.com")

    monkeypatch.setattr(wsvc, "_resolve_checked", unresolvable)
    r = await _send()
    assert r.error_code == "dns_error" and not r.permanent and r.error == "Host nicht auflösbar: hooks.example.com"

    def invalid(url):
        raise ValueError("Ungültiger Webhook-Host: x")

    monkeypatch.setattr(wsvc, "_resolve_checked", invalid)
    r = await _send()
    assert r.error_code == "invalid_url" and r.permanent

    for bad in ("ftp://hooks.example.com/x", "https://", "kein-url", ""):
        r = await _send(url=bad)
        assert r.error_code == "invalid_url" and r.permanent, bad
    assert sent == []  # nichts wurde gesendet


async def test_send_ssrf_check_with_real_resolver(monkeypatch):
    """Ohne Mock: ein IP-Literal im privaten Netz wird ohne DNS blockiert (Pruefung bei jedem Versuch)."""
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    _use_transport(monkeypatch, lambda req: httpx.Response(200))
    r = await _send(url="http://127.0.0.1:8080/hook")
    assert r.error_code == "ssrf_blocked" and r.permanent
    r = await _send(url="http://[fd00::1]/hook")
    assert r.error_code == "ssrf_blocked"


async def test_private_targets_allowed_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)
    seen = []
    _use_transport(monkeypatch, lambda req: seen.append(str(req.url)) or httpx.Response(200))
    r = await _send(url="http://receiver:8080/hook/x")
    assert r.ok and seen == ["http://receiver:8080/hook/x"]  # kein Pinning, Hostname bleibt


# --------------------------------------------------------------------------- Nr. 14: Fehlertexte
async def test_error_text_has_no_url_secrets(monkeypatch, resolved):
    secret_url = "https://user:pw@hooks.example.com/T0/B0/XXXX"

    def handler(request):
        raise httpx.ConnectError(f"Fehler beim Verbinden mit {secret_url} ({request.url})", request=request)

    _use_transport(monkeypatch, handler)
    r = await _send(url=secret_url)
    assert r.error_code == "connect_error"
    for leak in ("pw", "/T0/B0/XXXX", "user:", "93.184.216.34/T0"):
        assert leak not in r.error, (leak, r.error)
    assert "hooks.example.com" in r.error and len(r.error) <= 200


def test_safe_error_strips_urls_and_userinfo():
    exc = RuntimeError("POST http://admin:geheim@10.0.0.1:8080/pfad?x=1 abgelehnt; ftp://a:b@c/d")
    text = sender.safe_error("Fehler", exc, "http://admin:geheim@10.0.0.1:8080/pfad?x=1")
    assert text.startswith("Fehler (RuntimeError): POST 10.0.0.1:8080 abgelehnt")
    for leak in ("geheim", "/pfad", "x=1", "a:b@", "/d"):
        assert leak not in text, leak
    assert sender.safe_error("Nur Text") == "Nur Text"
    assert len(sender.safe_error("x", RuntimeError("y" * 1000))) == 200


def test_parse_retry_after():
    assert sender._parse_retry_after("120") == 120
    assert sender._parse_retry_after(" 0 ") == 0
    assert sender._parse_retry_after(None) is None and sender._parse_retry_after("bald") is None
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    secs = sender._parse_retry_after(format_datetime(future, usegmt=True))
    assert 290 <= secs <= 300
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert sender._parse_retry_after(format_datetime(past, usegmt=True)) == 0
    assert sender._parse_retry_after("99999999") == sender.MAX_RETRY_AFTER_SECONDS


# --------------------------------------------------------------------------- Nr. 15/16: Anzeige, Pruefung
def test_url_display_and_host():
    url = "https://u:p@hooks.slack.com/services/T/B/X?x=1"
    assert wsvc.url_display(url) == "https://hooks.slack.com/…"
    assert wsvc.url_host(url) == "hooks.slack.com"
    assert wsvc.url_display("https://bücher.example:8443/x") == "https://xn--bcher-kva.example:8443/…"
    assert wsvc.url_display("https://example.com/?q=1") == "https://example.com/…"
    assert wsvc.url_display("nicht-gueltig") == ""


def test_validate_webhook_url_exceptions(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    assert issubclass(wsvc.WebhookTargetBlocked, ValueError) and issubclass(wsvc.WebhookResolveError, ValueError)
    with pytest.raises(wsvc.WebhookTargetBlocked):
        wsvc.validate_webhook_url("http://10.1.2.3/x")
    with pytest.raises(ValueError, match="http:// oder https://"):
        wsvc.validate_webhook_url("ftp://example.com/x")


def test_schema_rejects_bad_url_and_name():
    ok = WebhookCreate(name="  Slack  ", url="  https://hooks.example.com/x  ")
    assert ok.name == "Slack" and ok.url == "https://hooks.example.com/x"
    assert ok.events == ["*"] and ok.scope == "own" and ok.is_active is True
    for bad in ("https://hooks.example.com/a b", "https://hooks.example.com/\x00", "https://exa\tmple.com/x"):
        with pytest.raises(ValidationError, match="ungültige Zeichen"):
            WebhookCreate(name="h", url=bad)
    with pytest.raises(ValidationError):
        WebhookCreate(name="   ", url="https://hooks.example.com/x")
    with pytest.raises(ValidationError):
        WebhookCreate(name="h", url="https://hooks.example.com/x", scope="all")
    with pytest.raises(ValidationError):
        WebhookCreate(name="h", url="https://hooks.example.com/x", events=["record"] * 31)
    upd = WebhookUpdate(name=None, url=None)
    assert upd.rotate_secret is False and upd.name is None
    with pytest.raises(ValidationError):
        WebhookUpdate(url="https://x.exa\nmple/")
