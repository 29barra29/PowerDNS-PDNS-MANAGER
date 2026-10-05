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


# =============================================================== metrics_runtime (F13 5.5)
import logging  # noqa: E402

from app.services import metrics_runtime as mr  # noqa: E402
from app.services.metrics_runtime import MetricsConfig  # noqa: E402
from fakes.pdns import FakePowerDNSClient, make_zone  # noqa: E402


@pytest.fixture
def rt(monkeypatch):
    mr.reset_for_tests()
    state = {"env": None, "raw_env": None, "vals": {}, "db_error": None, "reads": 0, "t": 1000.0,
             "db_probe": (True, 3)}

    async def read_db():
        state["reads"] += 1
        if state["db_error"]:
            raise state["db_error"]
        return dict(state["vals"])

    async def db_probe():
        return state["db_probe"]

    monkeypatch.setattr(mr, "_env_token", lambda: state["env"])
    monkeypatch.setattr(mr, "_raw_env_token", lambda: state["raw_env"])
    monkeypatch.setattr(mr, "_read_db_settings", read_db)
    monkeypatch.setattr(mr, "_db_probe", db_probe)
    monkeypatch.setattr(mr, "_now", lambda: state["t"])
    yield state
    mr.reset_for_tests()


async def test_config_disabled_by_default(rt):
    cfg = await mr.get_metrics_config()
    assert cfg == MetricsConfig(False, None, None, False, True)


async def test_config_db_enabled_with_token(rt):
    rt["vals"] = {"metrics_enabled": "true", "metrics_token": " t" * 1 + "x" * 40, "metrics_pdns_probe": "false"}
    cfg = await mr.get_metrics_config()
    assert cfg.effective_enabled and cfg.source == "db" and cfg.token == ("t" + "x" * 40) and cfg.pdns_probe is False


async def test_config_enabled_without_token_stays_off(rt):
    rt["vals"] = {"metrics_enabled": "true", "metrics_token": None}
    cfg = await mr.get_metrics_config()
    assert cfg.effective_enabled is False and cfg.db_enabled is True


async def test_env_token_overrides_db(rt):
    """M5: Env-Token aktiviert /metrics auch ohne DB."""
    rt["env"] = "e" * 30
    rt["vals"] = {"metrics_enabled": "false"}
    cfg = await mr.get_metrics_config()
    assert cfg.effective_enabled and cfg.source == "env" and cfg.token == "e" * 30
    mr.invalidate_config_cache()
    rt["db_error"] = RuntimeError("db weg")
    cfg = await mr.get_metrics_config()
    assert cfg == MetricsConfig(True, "e" * 30, "env", False, True)


async def test_config_cache_and_stale_and_unavailable(rt):
    rt["vals"] = {"metrics_enabled": "true", "metrics_token": "k" * 40}
    first = await mr.get_metrics_config()
    rt["t"] += 5
    assert await mr.get_metrics_config() is first and rt["reads"] == 1
    rt["t"] += 6
    rt["db_error"] = RuntimeError("db weg")
    assert await mr.get_metrics_config() is first  # Altwert bei DB-Fehler
    mr.invalidate_config_cache()
    with pytest.raises(mr.ConfigUnavailable):
        await mr.get_metrics_config()
    rt["db_error"] = None
    await mr.get_metrics_config(force=True)
    assert rt["reads"] == 4


async def test_unreadable_token_logs_once(rt, monkeypatch, caplog):
    class Unreadable(str):
        pass

    monkeypatch.setattr(mr, "_is_unreadable", lambda v: isinstance(v, Unreadable))
    rt["vals"] = {"metrics_enabled": "true", "metrics_token": Unreadable("")}
    with caplog.at_level(logging.ERROR, logger=mr.logger.name):
        assert (await mr.get_metrics_config()).effective_enabled is False
        await mr.get_metrics_config(force=True)
    assert len([r for r in caplog.records if "nicht entschluesselbar" in r.getMessage()]) == 1


@pytest.mark.parametrize("header,ok", [
    (None, False), ("", False), ("Bearer falsch", False), ("Basic dGVzdA==", False),
    ("Bearer " + "t" * 40, True), ("bearer " + "t" * 40 + "  ", True),
])
def test_check_bearer(header, ok):
    cfg = MetricsConfig(True, "t" * 40, "db", True, True)
    assert mr.check_bearer(header, cfg) is ok
    assert mr.check_bearer("Bearer x", MetricsConfig(False, None, None, False, True)) is False


def test_log_auth_failure_rate_limited_and_bounded(rt, caplog, monkeypatch):
    with caplog.at_level(logging.WARNING, logger=mr.logger.name):
        mr.log_auth_failure("192.0.2.1")
        mr.log_auth_failure("192.0.2.1")
        rt["t"] += 61
        mr.log_auth_failure("192.0.2.1")
    assert len([r for r in caplog.records if "192.0.2.1" in r.getMessage()]) == 2
    monkeypatch.setattr(mr, "AUTH_FAIL_MAX_IPS", 3)
    for i in range(10):
        mr.log_auth_failure(f"198.51.100.{i}")
    assert len(mr._auth_fail_log) <= 3  # noqa: SLF001


def test_log_env_token_state(rt, caplog):
    rt["raw_env"] = "kurz"
    with caplog.at_level(logging.WARNING, logger=mr.logger.name):
        mr.log_env_token_state()
        mr.log_env_token_state()
    msgs = [r.getMessage() for r in caplog.records if "METRICS_TOKEN" in r.getMessage()]
    assert msgs == ["METRICS_TOKEN ist kürzer als 24 Zeichen und wird ignoriert – /metrics bleibt über das Panel steuerbar."]
    mr.reset_for_tests()
    caplog.clear()
    rt["raw_env"] = "x" * 30
    mr.log_env_token_state()
    assert not [r for r in caplog.records if "METRICS_TOKEN" in r.getMessage()]


@pytest.fixture
def gauge_servers(monkeypatch):
    up = FakePowerDNSClient("g-up", [make_zone("a.example."), make_zone("b.example.")])
    down = FakePowerDNSClient("g-down")

    async def broken(*a, **k):
        raise PowerDNSAPIError(503, "Cannot connect", "g-down")

    down.get_server_info = broken
    monkeypatch.setattr(pdns_client.pdns_manager, "clients", {"g-up": up, "g-down": down})
    return up, down


async def test_refresh_runtime_gauges(rt, gauge_servers):
    up, down = gauge_servers
    cfg = MetricsConfig(True, "t" * 40, "db", True, True)
    await mr.refresh_runtime_gauges(cfg)
    g = prom.REGISTRY.get_sample_value
    assert g("pdnsmgr_pdns_servers_configured") == 2
    assert g("pdnsmgr_database_up") == 1
    assert g("pdnsmgr_webhook_deliveries_pending") == 3
    assert g("pdnsmgr_pdns_server_up", {"server": "g-up"}) == 1
    assert g("pdnsmgr_pdns_server_up", {"server": "g-down"}) == 0
    assert g("pdnsmgr_pdns_zones", {"server": "g-up"}) == 2
    assert g("pdnsmgr_pdns_zones", {"server": "g-down"}) is None
    assert [c[4] for c in up.calls if c[1] == ""] == [mr.PROBE_TIMEOUT]
    assert [c[4] for c in up.calls if c[1] == "/zones"] == [mr.ZONES_TIMEOUT]

    # innerhalb der TTLs keine neuen PowerDNS-Abfragen, DB-Werte aber frisch
    rt["db_probe"] = (False, None)
    rt["t"] += 30
    await mr.refresh_runtime_gauges(cfg)
    assert up.count("GET", "") == 2  # 1x server info + 1x zones
    assert g("pdnsmgr_database_up") == 0
    assert g("pdnsmgr_webhook_deliveries_pending") == 3  # unveraendert, Tabelle nicht lesbar
    rt["t"] += 31  # UP_TTL abgelaufen, ZONES_TTL nicht
    await mr.refresh_runtime_gauges(cfg)
    assert [c[1] for c in up.calls].count("") == 2 and [c[1] for c in up.calls].count("/zones") == 1


async def test_refresh_without_probe(rt, gauge_servers):
    up, _ = gauge_servers
    await mr.refresh_runtime_gauges(MetricsConfig(True, "t", "db", True, False))
    assert up.calls == []
    assert prom.REGISTRY.get_sample_value("pdnsmgr_pdns_servers_configured") == 2


async def test_refresh_never_raises(rt, monkeypatch):
    def boom():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(pdns_client.pdns_manager, "get_all_clients", boom)
    await mr.refresh_runtime_gauges(MetricsConfig(True, "t", "db", True, True))
