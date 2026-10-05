"""``GET /metrics`` (Prometheus-Scrape, F13 3.8/5.4) und die Prometheus-Middleware in ``main.py``.

Pflichtfall des Bauplans (W0-INT-BE2b): ``/metrics`` liefert 404, wenn Metriken aus sind – identisch zu einer
unbekannten Seite. Die Konfiguration (Env-Token/Panel-Einstellung) wird ueber ``metrics_runtime`` gelesen und
hier gepatcht; ``metrics_runtime`` selbst testet ``test_metrics_core.py``.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core import metrics as prom
from app.services import metrics_runtime

TOKEN = "t" * 32


def _cfg(enabled=True, token=TOKEN):
    return metrics_runtime.MetricsConfig(enabled, token if enabled else None, "env" if enabled else None, enabled, True)


@pytest.fixture
def client(monkeypatch):
    refreshed = []

    async def refresh(cfg):
        refreshed.append(cfg)

    monkeypatch.setattr(metrics_runtime, "refresh_runtime_gauges", refresh)
    c = TestClient(main.app, raise_server_exceptions=False)
    c.refreshed = refreshed
    return c


def _set_cfg(monkeypatch, cfg=None, exc=None):
    async def get(force=False):
        if exc is not None:
            raise exc
        return cfg

    monkeypatch.setattr(metrics_runtime, "get_metrics_config", get)


def test_metrics_404_when_disabled(client, monkeypatch):
    _set_cfg(monkeypatch, _cfg(enabled=False))
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404
    assert r.json() == {"error": "Not found"}
    # identisch zu einer unbekannten API-Seite (verraet nicht, dass es den Endpunkt gibt)
    assert client.get("/api/gibtsnicht").json() == r.json()
    assert client.refreshed == []


def test_metrics_401_without_or_with_wrong_token(client, monkeypatch):
    _set_cfg(monkeypatch, _cfg())
    before = prom.REGISTRY.get_sample_value("pdnsmgr_metrics_auth_failures_total") or 0.0
    for headers in ({}, {"Authorization": "Bearer falsch"}, {"Authorization": f"Basic {TOKEN}"}):
        r = client.get("/metrics", headers=headers)
        assert r.status_code == 401
        assert r.json() == {"detail": main.METRICS_TOKEN_INVALID_DETAIL}
        assert r.headers["www-authenticate"] == 'Bearer realm="pdns-manager-metrics"'
    # Query-Parameter zaehlt nicht als Token
    assert client.get(f"/metrics?token={TOKEN}").status_code == 401
    after = prom.REGISTRY.get_sample_value("pdnsmgr_metrics_auth_failures_total") or 0.0
    assert after == before + 4
    assert client.refreshed == []


def test_metrics_200_with_token(client, monkeypatch):
    _set_cfg(monkeypatch, _cfg())
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "no-store"
    assert "pdnsmgr_info" in r.text and "<html" not in r.text.lower()
    assert len(client.refreshed) == 1


def test_metrics_503_when_config_unreadable(client, monkeypatch):
    _set_cfg(monkeypatch, exc=metrics_runtime.ConfigUnavailable())
    r = client.get("/metrics")
    assert r.status_code == 503 and r.json() == {"detail": main.METRICS_CONFIG_UNAVAILABLE_DETAIL}


def test_metrics_refresh_timeout_returns_last_values(client, monkeypatch):
    _set_cfg(monkeypatch, _cfg())

    async def slow(cfg):  # verhaelt sich wie ein abgelaufenes wait_for(…, 5.0)
        raise asyncio.TimeoutError

    monkeypatch.setattr(metrics_runtime, "refresh_runtime_gauges", slow)
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and "pdnsmgr_info" in r.text


def test_metrics_route_has_no_auth_dependency_and_no_schema():
    from fastapi.routing import iter_route_contexts

    ctx = next(c for c in iter_route_contexts(main.app.routes) if c.path == "/metrics")
    assert ctx.methods == {"GET"}
    assert ctx.dependant.dependencies == []
    assert "/metrics" not in main.app.openapi().get("paths", {}) if main.app.openapi_url else True


def test_prometheus_middleware_counts_by_route_template(client, monkeypatch):
    _set_cfg(monkeypatch, _cfg(enabled=False))
    labels = {"method": "GET", "route": "/metrics", "status": "404"}
    before = prom.REGISTRY.get_sample_value("pdnsmgr_http_requests_total", labels) or 0.0
    client.get("/metrics")
    assert prom.REGISTRY.get_sample_value("pdnsmgr_http_requests_total", labels) == before + 1
    # CSRF-403 wird ebenfalls gemessen (Middleware ist die aeusserste)
    csrf = {"method": "POST", "route": "__unmatched__", "status": "403"}
    before = prom.REGISTRY.get_sample_value("pdnsmgr_http_requests_total", csrf) or 0.0
    r = client.post("/api/v1/zones", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert prom.REGISTRY.get_sample_value("pdnsmgr_http_requests_total", csrf) == before + 1
