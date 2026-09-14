"""Tests fuer die Client-IP-Ermittlung (Reverse-Proxy-Header nur wenn vertraut)."""
from types import SimpleNamespace

from starlette.datastructures import Headers

from app.core import client_ip as m
from app.core.config import settings


def _req(headers=None, client_host="10.1.2.3"):
    return SimpleNamespace(
        headers=Headers(headers or {}),
        client=SimpleNamespace(host=client_host) if client_host else None,
    )


def test_peer_ip_when_proxy_untrusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    req = _req({"x-forwarded-for": "1.2.3.4, 9.9.9.9"}, client_host="10.0.0.5")
    assert m.get_client_ip(req) == "10.0.0.5"


def test_xff_last_entry_is_trusted(monkeypatch):
    """Der Proxy haengt die echte IP AN – der erste Eintrag kommt vom Client."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1, raising=False)
    req = _req({"x-forwarded-for": "6.6.6.6, 9.9.9.9"}, client_host="172.18.0.1")
    assert m.get_client_ip(req) == "9.9.9.9"


def test_xff_two_trusted_hops(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2, raising=False)
    req = _req({"x-forwarded-for": "6.6.6.6, 1.2.3.4, 9.9.9.9"}, client_host="172.18.0.1")
    assert m.get_client_ip(req) == "1.2.3.4"


def test_xff_garbage_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1, raising=False)
    req = _req({"x-forwarded-for": "unknown", "x-real-ip": "5.6.7.8:443"}, client_host="10.0.0.5")
    assert m.get_client_ip(req) == "5.6.7.8"
    req = _req({"x-forwarded-for": "not-an-ip"}, client_host="10.0.0.5")
    assert m.get_client_ip(req) == "10.0.0.5"


def test_ipv6_with_port(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1, raising=False)
    req = _req({"x-forwarded-for": "[2001:db8::1]:5000"}, client_host="10.0.0.5")
    assert m.get_client_ip(req) == "2001:db8::1"


def test_none_when_no_client(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    req = _req({}, client_host=None)
    assert m.get_client_ip(req) is None
