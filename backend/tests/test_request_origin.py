"""Tests fuer core/request_origin.py (F9 5.3)."""
import pytest
from starlette.requests import Request

from app.core import request_origin as ro


def _req(headers: dict) -> Request:
    raw = []
    for k, v in headers.items():
        if isinstance(v, list):
            raw.extend((k.lower().encode(), x.encode()) for x in v)
        else:
            raw.append((k.lower().encode(), v.encode()))
    return Request({"type": "http", "method": "GET", "path": "/nic/update", "headers": raw, "query_string": b""})


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"host": "dns.example.com"}, False),  # Router/curl
        ({"host": "dns.example.com", "sec-fetch-site": "cross-site"}, True),
        ({"host": "dns.example.com", "sec-fetch-site": "same-site"}, True),
        ({"host": "dns.example.com", "sec-fetch-site": "same-origin"}, False),
        ({"host": "dns.example.com", "sec-fetch-site": "none"}, False),
        ({"host": "dns.example.com", "origin": "https://evil.example.net"}, True),
        ({"host": "dns.example.com", "origin": "https://dns.example.com"}, False),
        ({"host": "dns.example.com", "origin": "https://DNS.example.com/"}, False),
        ({"host": "dns.example.com", "origin": "null"}, True),
        ({"host": "dns.example.com:8443", "origin": "https://dns.example.com:8443"}, False),
    ],
)
def test_cross_site_detection(headers, expected, monkeypatch):
    monkeypatch.setattr(ro.settings, "TRUST_PROXY_HEADERS", False)
    assert ro.is_cross_site_browser_request(_req(headers)) is expected


def test_forwarded_host_only_with_trust_proxy(monkeypatch):
    h = {"host": "backend:8000", "origin": "https://dns.example.com", "x-forwarded-host": "dns.example.com"}
    monkeypatch.setattr(ro.settings, "TRUST_PROXY_HEADERS", False)
    assert ro.is_cross_site_browser_request(_req(h)) is True
    monkeypatch.setattr(ro.settings, "TRUST_PROXY_HEADERS", True)
    assert ro.is_cross_site_browser_request(_req(h)) is False


def test_forwarded_host_uses_last_entry(monkeypatch):
    monkeypatch.setattr(ro.settings, "TRUST_PROXY_HEADERS", True)
    h = {"host": "backend:8000", "origin": "https://evil.example.net",
         "x-forwarded-host": ["evil.example.net", "dns.example.com"]}
    assert ro.is_cross_site_browser_request(_req(h)) is True
    h["origin"] = "https://dns.example.com"
    assert ro.is_cross_site_browser_request(_req(h)) is False
