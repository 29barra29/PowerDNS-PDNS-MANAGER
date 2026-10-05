"""Tests fuer core/metrics.py und die pdns_client-Instrumentierung (F13 M3, M6, M7, M8)."""
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from app.core import metrics as prom
from app.services import pdns_client
from app.services.pdns_client import PowerDNSAPIError, PowerDNSClient


def _val(name, labels=None):
    return prom.REGISTRY.get_sample_value(name, labels or {}) or 0.0


# ---------------------------------------------------------------- route_label (M3)
def test_route_label_router_template_gets_prefix():
    scope = {"route": SimpleNamespace(path="/zones/{server_name}/{zone_id:path}/propagation")}
    assert prom.route_label(scope, "/api/v1/zones/a/b./propagation") == "/api/v1/zones/{server_name}/{zone_id:path}/propagation"


def test_route_label_app_route_and_spa_and_static():
    assert prom.route_label({"route": SimpleNamespace(path="/metrics")}, "/metrics") == "/metrics"
    assert prom.route_label({"route": SimpleNamespace(path="/api/v1/metrics")}, "/api/v1/metrics") == "/api/v1/metrics"
    assert prom.route_label({"route": SimpleNamespace(path="/{path:path}")}, "/zones") == "__spa__"
    assert prom.route_label({}, "/assets/x.js") == "__static__"
    assert prom.route_label({}, "/uploads/logo.png") == "__static__"
    assert prom.route_label({}, "/irgendwas") == "__unmatched__"
    assert prom.route_label({"route": object()}, "/x") == "__unmatched__"


def test_route_label_truncates():
    tpl = "/" + "a" * 300
    assert len(prom.route_label({"route": SimpleNamespace(path=tpl)}, tpl)) == 160


# ---------------------------------------------------------------- Middleware-Vertrag (M4/M8)
def _app_with_middleware():
    app = FastAPI()
    r = APIRouter(prefix="/zones")

    @r.get("/{server_name}/{zone_id:path}/propagation")
    async def prop(server_name: str, zone_id: str):
        return {"ok": True}

    @r.get("/boom")
    async def boom():
        raise RuntimeError("kaputt")

    app.include_router(r, prefix="/api/v1")

    @app.middleware("http")
    async def _prometheus_http(request: Request, call_next):  # wie Spec F13 5.4
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            prom.observe_http(request.method, prom.route_label(request.scope, request.url.path),
                              status_code, time.perf_counter() - start)

    return app


def test_middleware_labels_template_not_zone_name():
    client = TestClient(_app_with_middleware(), raise_server_exceptions=False)
    labels = {"method": "GET", "route": "/api/v1/zones/{server_name}/{zone_id:path}/propagation", "status": "200"}
    before = _val("pdnsmgr_http_requests_total", labels)
    assert client.get("/api/v1/zones/srv/geheim-zone.example./propagation").status_code == 200
    assert _val("pdnsmgr_http_requests_total", labels) == before + 1
    body = prom.render_latest().decode()
    assert "geheim-zone" not in body
    assert "/api/v1/zones/{server_name}/{zone_id:path}/propagation" in body


def test_middleware_exception_counts_500():
    client = TestClient(_app_with_middleware(), raise_server_exceptions=False)
    labels = {"method": "GET", "route": "/api/v1/zones/boom", "status": "500"}
    before = _val("pdnsmgr_http_requests_total", labels)
    assert client.get("/api/v1/zones/boom").status_code == 500
    assert _val("pdnsmgr_http_requests_total", labels) == before + 1


def test_observe_http_other_method_and_never_raises():
    before = _val("pdnsmgr_http_requests_total", {"method": "OTHER", "route": "/x", "status": "418"})
    prom.observe_http("BREW", "/x", 418, 0.01)
    assert _val("pdnsmgr_http_requests_total", {"method": "OTHER", "route": "/x", "status": "418"}) == before + 1
    prom.observe_http("GET", "/x", "kein-int", 0.01)  # wirft nicht


# ---------------------------------------------------------------- Login/Webhook/DynDNS/Propagation (M7)
def test_record_login_unknown_values_ignored_and_series_preinitialised():
    # vorinitialisierte Serie existiert (Wert >= 0, andere Tests koennen sie erhoeht haben)
    assert prom.REGISTRY.get_sample_value(
        "pdnsmgr_login_attempts_total", {"method": "ldap", "result": "success"}) is not None
    before = _val("pdnsmgr_login_attempts_total", {"method": "password", "result": "failure"})
    prom.record_login("password", "bogus")
    prom.record_login("magic", "success")
    assert _val("pdnsmgr_login_attempts_total", {"method": "password", "result": "failure"}) == before
    prom.record_login("password", "failure")
    assert _val("pdnsmgr_login_attempts_total", {"method": "password", "result": "failure"}) == before + 1


@pytest.mark.parametrize(
    "status,label",
    [("succeeded", "success"), ("failed", "retry"), ("dead", "dead"), ("cancelled", "cancelled"), ("success", "success")],
)
def test_record_webhook_delivery_mapping(status, label):
    before = _val("pdnsmgr_webhook_deliveries_total", {"status": label})
    prom.record_webhook_delivery(status)
    assert _val("pdnsmgr_webhook_deliveries_total", {"status": label}) == before + 1


def test_record_webhook_delivery_unknown_ignored_and_no_failed_series():
    prom.record_webhook_delivery("whatever")
    assert prom.REGISTRY.get_sample_value("pdnsmgr_webhook_deliveries_total", {"status": "failed"}) is None
    assert prom.REGISTRY.get_sample_value("pdnsmgr_webhook_deliveries_total", {"status": "whatever"}) is None


def test_record_dyndns_first_token_and_other():
    b_good = _val("pdnsmgr_dyndns_updates_total", {"result": "good"})
    b_other = _val("pdnsmgr_dyndns_updates_total", {"result": "other"})
    prom.record_dyndns("GOOD 192.0.2.1")
    prom.record_dyndns("911")
    prom.record_dyndns("komisch")
    prom.record_dyndns(None)
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "good"}) == b_good + 1
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "other"}) == b_other + 2


def test_record_propagation():
    before = _val("pdnsmgr_propagation_checks_total", {"result": "in_sync"})
    prom.record_propagation("in_sync")
    prom.record_propagation("nonsense")
    assert _val("pdnsmgr_propagation_checks_total", {"result": "in_sync"}) == before + 1


def test_render_latest_contains_info_and_prefix():
    prom.init_static_series("9.9.9-test")  # idempotent, weitere Version
    body = prom.render_latest().decode()
    assert 'pdnsmgr_info{version="' in body
    assert "# HELP pdnsmgr_http_requests_total" in body
    assert "pdnsmgr_login_attempts_total{" in body
    assert prom.CONTENT_TYPE_LATEST.startswith("text/plain")


# ---------------------------------------------------------------- pdns_client-Instrumentierung (M6)
@pytest.fixture
def mock_transport(monkeypatch):
    """Ersetzt httpx.AsyncClient in pdns_client durch einen Client mit MockTransport."""
    real = httpx.AsyncClient
    state = {"handler": None}

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(state["handler"])
        return real(*args, **kwargs)

    monkeypatch.setattr(pdns_client.httpx, "AsyncClient", factory)
    return state


async def test_pdns_request_counted_2xx(mock_transport):
    mock_transport["handler"] = lambda req: httpx.Response(200, json={"version": "4.9"})
    labels = {"server": "s1", "method": "GET", "status": "2xx"}
    before = _val("pdnsmgr_pdns_api_requests_total", labels)
    c = PowerDNSClient("s1", "http://pdns.invalid:8081", "k")
    assert (await c.get_server_info()) == {"version": "4.9"}
    assert _val("pdnsmgr_pdns_api_requests_total", labels) == before + 1
    assert prom.REGISTRY.get_sample_value("pdnsmgr_pdns_api_request_duration_seconds_count", {"server": "s1"}) >= 1


async def test_pdns_request_counted_timeout(mock_transport):
    def handler(req):
        raise httpx.ConnectTimeout("zu langsam", request=req)

    mock_transport["handler"] = handler
    labels = {"server": "s1", "method": "GET", "status": "timeout"}
    before = _val("pdnsmgr_pdns_api_requests_total", labels)
    c = PowerDNSClient("s1", "http://pdns.invalid:8081", "k")
    with pytest.raises(PowerDNSAPIError) as ei:
        await c.list_zones()
    assert ei.value.status_code == 504
    assert _val("pdnsmgr_pdns_api_requests_total", labels) == before + 1


async def test_pdns_request_counted_4xx_and_connect_error(mock_transport):
    mock_transport["handler"] = lambda req: httpx.Response(422, json={"error": "nope"})
    c = PowerDNSClient("s2", "http://pdns.invalid:8081", "k")
    before = _val("pdnsmgr_pdns_api_requests_total", {"server": "s2", "method": "PATCH", "status": "4xx"})
    with pytest.raises(PowerDNSAPIError):
        await c.update_records("example.com.", [])
    assert _val("pdnsmgr_pdns_api_requests_total", {"server": "s2", "method": "PATCH", "status": "4xx"}) == before + 1

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    mock_transport["handler"] = refuse
    before = _val("pdnsmgr_pdns_api_requests_total", {"server": "s2", "method": "GET", "status": "connect_error"})
    with pytest.raises(PowerDNSAPIError) as ei:
        await c.get_zone("example.com.")
    assert ei.value.status_code == 503 and ei.value.transport_error is False
    assert _val("pdnsmgr_pdns_api_requests_total", {"server": "s2", "method": "GET", "status": "connect_error"}) == before + 1


async def test_metrics_failure_never_breaks_request(mock_transport, monkeypatch):
    mock_transport["handler"] = lambda req: httpx.Response(200, json=[])

    def broken(*a, **k):
        raise RuntimeError("registry kaputt")

    monkeypatch.setattr(prom.PDNS_API_REQUESTS, "labels", broken)
    c = PowerDNSClient("s3", "http://pdns.invalid:8081", "k")
    assert await c.list_zones() == []
