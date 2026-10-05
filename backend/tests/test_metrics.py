"""Prometheus-Metriken (F13 9.1 M1-M8) und die Monitoring-Einstellungen ohne Datenbank (WS-F12F13-BE).

Grundbausteine (``core/metrics.py``, ``metrics_runtime``, ``/metrics`` in ``main.py``) stammen aus Welle 0 und haben
eigene Tests (``test_metrics_core.py``, ``test_metrics_endpoint.py``). Hier: die Spec-Faelle M1-M8 gegen die
integrierte App, die neuen Status-Gauges und die Endpunkte aus ``routers/settings_monitoring.py``
(Fachlogik; die Session-Pflicht gegen echte Tokens prueft ``test_monitoring_settings_db.py``).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import main
from app.core import metrics as prom
from app.core.auth import get_admin_session_user, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.services import metrics_runtime as mr
from app.services import pdns_client
from app.services import propagation as prop
from app.services.metrics_runtime import MetricsConfig
from authfakes import FakeSession, make_user
from fakes.pdns import FakePowerDNSClient, make_zone

TOKEN = "t" * 40


def _val(name, labels=None):
    return prom.REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.fixture
def scrape(monkeypatch):
    """``/metrics`` mit gepatchter Konfiguration; Gauge-Refresh ist ein No-op."""
    mr.reset_for_tests()
    state = {"cfg": MetricsConfig(True, TOKEN, "db", True, True)}

    async def get_cfg(force=False):
        return state["cfg"]

    async def refresh(cfg):
        return None

    monkeypatch.setattr(mr, "get_metrics_config", get_cfg)
    monkeypatch.setattr(mr, "refresh_runtime_gauges", refresh)
    state["client"] = TestClient(main.app, raise_server_exceptions=False)
    yield state
    mr.reset_for_tests()


# ------------------------------------------------------------------------------------------------ M1/M2
def test_m1_disabled_is_json_404(scrape):
    scrape["cfg"] = MetricsConfig(False, None, None, False, True)
    r = scrape["client"].get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404 and r.json() == {"error": "Not found"}
    assert r.headers["content-type"].startswith("application/json")


def test_m2_bearer_token(scrape):
    c = scrape["client"]
    before = _val("pdnsmgr_metrics_auth_failures_total")
    r = c.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="pdns-manager-metrics"'
    assert r.json() == {"detail": "Ungültiger oder fehlender Metrik-Token"}
    assert c.get("/metrics", headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert c.get(f"/metrics?token={TOKEN}").status_code == 401  # nie per Query-Parameter
    assert _val("pdnsmgr_metrics_auth_failures_total") == before + 4
    r = c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "no-store"
    assert "pdnsmgr_http_requests_total" in r.text and 'pdnsmgr_info{version="' in r.text
    assert "pdnsmgr_propagation_checks_total" in r.text


# ------------------------------------------------------------------------------------------------ M3
def test_m3_route_label():
    class R:
        path = "/zones/{server_name}/{zone_id:path}/propagation"

    assert prom.route_label({"route": R()}, "/api/v1/zones/a/b./propagation") == \
        "/api/v1/zones/{server_name}/{zone_id:path}/propagation"

    class Spa:
        path = "/{path:path}"

    assert prom.route_label({"route": Spa()}, "/zones/x") == "__spa__"
    assert prom.route_label({}, "/assets/x.js") == "__static__"
    assert prom.route_label({}, "/irgendwas") == "__unmatched__"


# ------------------------------------------------------------------------------------------------ M4
def minimal_response(zone: str):
    from app.schemas.propagation import PropagationExternal, PropagationResponse, PropagationSummary

    return PropagationResponse(
        zone=zone, server="srv", checked_at="2026-10-05T00:00:00Z", cached=False, duration_ms=1, timed_out=False,
        expected_serial=1, reference_serial_raw=1, nameservers=[], content_compared=False,
        external=PropagationExternal(enabled=False, authoritative=False, resolvers=[], ipv6=False),
        comparable_types=list(prop.COMPARABLE_TYPES), sources=[],
        summary=PropagationSummary(total=0, ok=0, mismatch=0, failed=0, skipped=0, in_sync=True),
    )


def test_m4_zone_name_never_in_labels(scrape, monkeypatch):
    async def user_dep():
        return make_user(role="admin")

    async def db_dep():
        yield FakeSession()

    async def load_settings(db):
        return prop.PropagationSettings()

    async def check_zone(**kw):
        return minimal_response(kw["zone_norm"])

    monkeypatch.setattr(prop, "load_settings", load_settings)
    monkeypatch.setattr(prop, "check_zone", check_zone)
    monkeypatch.setattr(pdns_client.pdns_manager, "clients", {"srv": FakePowerDNSClient("srv")})
    main.app.dependency_overrides[get_current_user] = user_dep
    main.app.dependency_overrides[get_db] = db_dep
    try:
        r = scrape["client"].get("/api/v1/zones/srv/geheim-zone.example./propagation")
        assert r.status_code == 200, r.text
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        main.app.dependency_overrides.pop(get_db, None)
    body = scrape["client"].get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).text
    assert 'route="/api/v1/zones/{server_name}/{zone_id:path}/propagation"' in body
    assert "geheim-zone" not in body


# ------------------------------------------------------------------------------------------------ M5
def test_m5_env_override_without_db(monkeypatch):
    mr.reset_for_tests()

    async def broken_db():
        raise RuntimeError("DB weg")

    async def refresh(cfg):
        return None

    monkeypatch.setattr(mr, "_read_db_settings", broken_db)
    monkeypatch.setattr(mr, "refresh_runtime_gauges", refresh)
    monkeypatch.setattr(settings, "METRICS_TOKEN", "x" * 30)
    mr.invalidate_config_cache()
    c = TestClient(main.app, raise_server_exceptions=False)
    assert c.get("/metrics", headers={"Authorization": "Bearer " + "x" * 30}).status_code == 200
    assert c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    monkeypatch.setattr(settings, "METRICS_TOKEN", "kurz")
    assert settings.get_metrics_env_token() is None
    mr.invalidate_config_cache()
    assert c.get("/metrics").status_code == 503  # weder DB noch gueltiger Env-Token
    mr.reset_for_tests()


# ------------------------------------------------------------------------------------------------ M6
async def test_m6_pdns_client_instrumentation(monkeypatch):
    real = httpx.AsyncClient
    state = {"handler": lambda req: httpx.Response(200, json={"version": "4.9"})}

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda req: state["handler"](req))
        return real(*args, **kwargs)

    monkeypatch.setattr(pdns_client.httpx, "AsyncClient", factory)
    c = pdns_client.PowerDNSClient("m6-srv", "http://pdns.invalid:8081", "k")
    ok = {"server": "m6-srv", "method": "GET", "status": "2xx"}
    before = _val("pdnsmgr_pdns_api_requests_total", ok)
    await c.get_server_info()
    assert _val("pdnsmgr_pdns_api_requests_total", ok) == before + 1

    def timeout(req):
        raise httpx.ConnectTimeout("zu langsam", request=req)

    state["handler"] = timeout
    with pytest.raises(pdns_client.PowerDNSAPIError):
        await c.get_zone_meta("example.com.")
    assert _val("pdnsmgr_pdns_api_requests_total", {"server": "m6-srv", "method": "GET", "status": "timeout"}) == 1


# ------------------------------------------------------------------------------------------------ M7/M8
def test_m7_login_series():
    before = _val("pdnsmgr_login_attempts_total", {"method": "password", "result": "failure"})
    prom.record_login("password", "bogus")
    prom.record_login("bogus", "failure")
    assert _val("pdnsmgr_login_attempts_total", {"method": "password", "result": "failure"}) == before
    assert prom.REGISTRY.get_sample_value("pdnsmgr_login_attempts_total", {"method": "ldap", "result": "success"}) \
        is not None


def test_m8_middleware_counts_500_on_exception():
    app = FastAPI()

    @app.get("/m8-boom")
    async def boom():
        raise RuntimeError("kaputt")

    app.middleware("http")(main._prometheus_http)
    labels = {"method": "GET", "route": "/m8-boom", "status": "500"}
    before = _val("pdnsmgr_http_requests_total", labels)
    r = TestClient(app, raise_server_exceptions=False).get("/m8-boom")
    assert r.status_code == 500
    assert _val("pdnsmgr_http_requests_total", labels) == before + 1


# ------------------------------------------------------------------------------------------------ Status-Gauges
async def test_status_gauges_and_unloaded_servers(monkeypatch):
    from app.core import database
    from app.core import secrets as secret_store
    from app.services import background

    mr.reset_for_tests()

    async def db_probe():
        return True, 0

    monkeypatch.setattr(mr, "_db_probe", db_probe)
    up = FakePowerDNSClient("st-up", [make_zone("a.example.")])
    monkeypatch.setattr(pdns_client.pdns_manager, "clients", {"st-up": up})
    monkeypatch.setattr(pdns_client.pdns_manager, "unloaded", {"st-weg": "api key unreadable"})
    monkeypatch.setattr(database, "MIGRATION_ERRORS", [("ALTER TABLE x", "kaputt")])
    monkeypatch.setattr(background, "state", lambda: {"enabled": True, "tasks": {
        "webhook_worker": {"running": True}, "audit_purge": {"running": False}}})
    monkeypatch.setattr(secret_store, "runtime_unreadable_counts", lambda: {"server_configs.api_key": 2})
    await mr.refresh_runtime_gauges(MetricsConfig(True, TOKEN, "db", True, True))
    g = prom.REGISTRY.get_sample_value
    assert g("pdnsmgr_pdns_servers_configured") == 2
    assert g("pdnsmgr_pdns_server_up", {"server": "st-up"}) == 1
    assert g("pdnsmgr_pdns_server_up", {"server": "st-weg"}) == 0
    assert g("pdnsmgr_pdns_zones", {"server": "st-up"}) == 1
    assert g("pdnsmgr_migration_errors") == 1
    assert g("pdnsmgr_background_task_running", {"task": "webhook_worker"}) == 1
    assert g("pdnsmgr_background_task_running", {"task": "audit_purge"}) == 0
    assert g("pdnsmgr_secrets_unreadable_reads", {"field": "server_configs.api_key"}) == 2
    mr.reset_for_tests()


# ------------------------------------------------------------------------------------------------ Einstellungen
class SettingsStore:
    """In-Memory-Ersatz fuer ``services/system_settings`` (Klartext; Verschluesselung prueft der DB-Test)."""

    def __init__(self, monkeypatch, values=None):
        from app.services import system_settings as ss

        self.values: dict[str, str] = dict(values or {})

        def store(v):
            return ("true" if v else "false") if isinstance(v, bool) else v

        async def get_settings(db, keys):
            return {k: self.values[k] for k in keys if k in self.values}

        async def get_setting(db, key, default=None):
            return self.values.get(key, default)

        async def set_settings(db, mapping):
            for k, v in mapping.items():
                self.values[k] = store(v)

        async def set_setting(db, key, value):
            self.values[key] = store(value)

        async def delete_setting(db, key):
            self.values.pop(key, None)

        for name, fn in (("get_settings", get_settings), ("get_setting", get_setting),
                         ("set_settings", set_settings), ("set_setting", set_setting),
                         ("delete_setting", delete_setting)):
            monkeypatch.setattr(ss, name, fn)


@pytest.fixture
def admin_api(monkeypatch):
    from app.routers import settings_monitoring as sm

    audits: list[tuple] = []

    async def fake_audit(db, action, resource_type, resource_name=None, **kw):
        audits.append((action, resource_type, resource_name, kw.get("details")))

    async def admin_dep():
        return make_user(role="admin")

    async def db_dep():
        yield FakeSession()

    monkeypatch.setattr(sm, "write_audit", fake_audit)
    monkeypatch.setattr(settings, "METRICS_TOKEN", None)
    main.app.dependency_overrides[get_admin_session_user] = admin_dep
    main.app.dependency_overrides[get_db] = db_dep
    store = SettingsStore(monkeypatch, {"app_base_url": "https://dns.example.com/"})
    yield {"client": TestClient(main.app, raise_server_exceptions=False), "store": store, "audits": audits}
    main.app.dependency_overrides.pop(get_admin_session_user, None)
    main.app.dependency_overrides.pop(get_db, None)
    mr.reset_for_tests()


def test_metrics_settings_token_flow(admin_api):
    c, store, audits = admin_api["client"], admin_api["store"], admin_api["audits"]
    r = c.get("/api/v1/settings/metrics")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "effective_enabled": False, "env_override": False, "token_set": False,
                        "token_unreadable": False, "token_hint": None, "token_created_at": None, "pdns_probe": True,
                        "endpoint_path": "/metrics", "scrape_url": "https://dns.example.com/metrics"}
    r = c.put("/api/v1/settings/metrics", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "Bitte zuerst einen Scrape-Token erzeugen."
    assert c.delete("/api/v1/settings/metrics/token").status_code == 404

    r = c.post("/api/v1/settings/metrics/token", json={})
    assert r.status_code == 201
    created = r.json()
    tok = created["token"]
    assert tok.startswith("dnsmgr_metrics_") and len(tok) >= 15 + 43
    assert created["token_hint"] == tok[:19] + "…" and created["warning"] == "Dieser Token wird nur jetzt angezeigt."
    assert store.values["metrics_token"] == tok and "metrics_enabled" not in store.values
    assert audits[-1][:3] == ("METRICS_TOKEN_CREATE", "settings", "metrics")
    assert audits[-1][3] == {"hint": created["token_hint"], "rotated": False} and tok not in str(audits)

    info = c.get("/api/v1/settings/metrics").json()
    assert info["token_set"] and info["token_hint"] == created["token_hint"] and "token" not in info
    r = c.put("/api/v1/settings/metrics", json={"enabled": True, "pdns_probe": False})
    assert r.status_code == 200 and r.json()["effective_enabled"] is True and r.json()["pdns_probe"] is False
    assert audits[-1][0] == "METRICS_SETTINGS_UPDATE"
    assert audits[-1][3] == {"changed": {"enabled": {"from": False, "to": True},
                                         "pdns_probe": {"from": True, "to": False}}}
    n = len(audits)
    assert c.put("/api/v1/settings/metrics", json={"enabled": True}).status_code == 200
    assert len(audits) == n  # nichts geaendert -> kein Audit

    rot = c.post("/api/v1/settings/metrics/token", json={}).json()
    assert rot["token"] != tok and audits[-1][3]["rotated"] is True
    assert store.values["metrics_enabled"] == "true"  # Rotation laesst den Endpunkt aktiv

    r = c.delete("/api/v1/settings/metrics/token")
    assert r.status_code == 200 and r.json() == {"message": "Scrape-Token gelöscht, /metrics deaktiviert."}
    assert "metrics_token" not in store.values and store.values["metrics_enabled"] == "false"
    assert audits[-1] == ("METRICS_TOKEN_DELETE", "settings", "metrics", {"hint": rot["token_hint"]})


def test_metrics_settings_env_override(admin_api, monkeypatch):
    c = admin_api["client"]
    monkeypatch.setattr(settings, "METRICS_TOKEN", "e" * 30)
    info = c.get("/api/v1/settings/metrics").json()
    assert info["env_override"] is True and info["effective_enabled"] is True
    env_text = "Metriken werden über die Umgebungsvariable METRICS_TOKEN gesteuert."
    assert c.put("/api/v1/settings/metrics", json={"enabled": False}).json()["detail"] == env_text
    assert c.put("/api/v1/settings/metrics", json={"pdns_probe": False}).status_code == 200
    tok_text = "METRICS_TOKEN ist gesetzt – der Token wird über die Umgebung verwaltet."
    r = c.post("/api/v1/settings/metrics/token", json={})
    assert r.status_code == 409 and r.json()["detail"] == tok_text
    assert c.delete("/api/v1/settings/metrics/token").status_code == 409


def test_metrics_settings_unreadable_token_and_no_base_url(admin_api, monkeypatch):
    from app.core import secrets as secret_store

    store = admin_api["store"]
    store.values.pop("app_base_url")
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "")
    store.values.update({"metrics_token": secret_store.UNREADABLE, "metrics_token_hint": "dnsmgr_metrics_AbCd…",
                         "metrics_enabled": "true"})
    info = admin_api["client"].get("/api/v1/settings/metrics").json()
    assert info["token_set"] is False and info["token_unreadable"] is True and info["effective_enabled"] is False
    assert info["token_hint"] == "dnsmgr_metrics_AbCd…" and info["scrape_url"] is None
    r = admin_api["client"].post("/api/v1/settings/metrics/token", json={})
    assert r.status_code == 201 and admin_api["audits"][-1][3]["rotated"] is True


def test_propagation_settings_endpoints(admin_api):
    c, store, audits = admin_api["client"], admin_api["store"], admin_api["audits"]
    r = c.get("/api/v1/settings/propagation")
    assert r.json() == {"enabled": False, "check_authoritative": True, "ipv6": False,
                        "resolvers": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
                        "default_resolvers": ["1.1.1.1", "8.8.8.8", "9.9.9.9"]}
    r = c.put("/api/v1/settings/propagation", json={"enabled": True, "resolvers": ["10.0.0.53", "", "10.0.0.53"]})
    assert r.status_code == 200 and r.json()["resolvers"] == ["10.0.0.53"] and r.json()["enabled"] is True
    assert audits[-1][:3] == ("PROPAGATION_SETTINGS_UPDATE", "settings", "propagation")
    assert set(audits[-1][3]["changed"]) == {"enabled", "resolvers"}
    n = len(audits)
    assert c.put("/api/v1/settings/propagation", json={"enabled": True}).status_code == 200
    assert len(audits) == n
    r = c.put("/api/v1/settings/propagation", json={"resolvers": ["dns.google"]})
    assert r.status_code == 422
    assert r.json()["detail"] == "Ungültige Resolver-Adresse: dns.google (nur IPv4/IPv6-Adressen erlaubt)."
    assert store.values["propagation_resolvers"] == '["10.0.0.53"]'


def test_monitoring_status(admin_api, monkeypatch):
    from app.core import database
    from app.core import secrets as secret_store

    monkeypatch.setattr(pdns_client.pdns_manager, "unloaded", {})
    monkeypatch.setattr(database, "MIGRATION_ERRORS", [])
    secret_store.configure_for_tests()
    r = admin_api["client"].get("/api/v1/settings/monitoring/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True and data["migration_errors"] == 0 and data["servers_not_loaded"] == {}
    assert data["secrets"]["mode"] == "encrypted" and data["background"]["enabled"] is False
    assert data["version"] == settings.APP_VERSION

    monkeypatch.setattr(pdns_client.pdns_manager, "unloaded", {"ns9": "api key unreadable"})
    monkeypatch.setattr(database, "MIGRATION_ERRORS", [("x", "y")])
    data = admin_api["client"].get("/api/v1/settings/monitoring/status").json()
    assert data["ok"] is False and data["migration_errors"] == 1
    assert data["servers_not_loaded"] == {"ns9": "api key unreadable"}


def test_settings_endpoints_require_session(monkeypatch):
    """Ohne Session-Kennzeichen (z. B. Panel-Token) -> 403, auch fuer Admins."""
    async def user_dep(request: Request):
        request.state.auth_via = "panel_token"
        return make_user(role="admin")

    async def db_dep():
        yield FakeSession()

    main.app.dependency_overrides[get_current_user] = user_dep
    main.app.dependency_overrides[get_db] = db_dep
    try:
        c = TestClient(main.app, raise_server_exceptions=False)
        for method, path in (("GET", "propagation"), ("PUT", "propagation"), ("GET", "metrics"),
                             ("PUT", "metrics"), ("POST", "metrics/token"), ("DELETE", "metrics/token"),
                             ("GET", "monitoring/status")):
            kw = {"json": {}} if method in ("PUT", "POST") else {}
            r = c.request(method, f"/api/v1/settings/{path}", **kw)
            assert r.status_code == 403, (method, path, r.status_code)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        main.app.dependency_overrides.pop(get_db, None)

