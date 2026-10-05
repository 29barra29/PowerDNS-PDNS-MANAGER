"""Monitoring-Einstellungen, Scrape-Token und Login-Metriken gegen MariaDB (F12/F13 9.2 D1-D5, ``requires_db``).

Laeuft ueber die echte App (``TestClient`` ohne lifespan) mit einer Browser-Session (JWT des Admins) bzw. einem
Admin-Panel-Token. Prueft zusaetzlich ``metrics_runtime.get_metrics_config`` gegen die echte Datenbank
(verschluesselter Token, Weitergabe W0-SHARED-BE).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from dbutil import requires_db

pytestmark = requires_db

PASSWORD = "x-passwort-1"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(fresh_db, monkeypatch):
    """Admin + Benutzer mit Leserecht auf ``a.example.``; Geheimnis-Schluessel aktiv; frische Metrik-Caches."""
    from app.core import secrets as secret_store
    from app.core.auth import create_access_token, hash_password
    from app.core.config import settings
    from app.core.database import async_session
    from app.main import app
    from app.models.models import User, UserZoneAccess
    from app.services import metrics_runtime, propagation

    secret_store.configure_for_tests()
    monkeypatch.setattr(settings, "METRICS_TOKEN", None)
    metrics_runtime.reset_for_tests()
    propagation.reset_for_tests()
    prefix = "f13_" + uuid.uuid4().hex[:6]

    async def make():
        async with async_session() as s:
            admin = User(username=f"{prefix}_adm", hashed_password=hash_password(PASSWORD), role="admin",
                         is_active=True, display_name="A")
            user = User(username=f"{prefix}_usr", hashed_password=hash_password(PASSWORD), role="user",
                        is_active=True, display_name="U")
            s.add_all([admin, user])
            await s.flush()
            s.add(UserZoneAccess(user_id=user.id, zone_name="a.example.", permission="read"))
            await s.commit()
            await s.refresh(admin)
            await s.refresh(user)
            return admin, user

    admin, user = _run(make())
    yield {
        "client": TestClient(app, raise_server_exceptions=False),
        "admin": admin,
        "user": user,
        "admin_h": {"Authorization": f"Bearer {create_access_token(data={'sub': str(admin.id)}, user=admin)}"},
        "user_h": {"Authorization": f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"},
    }
    metrics_runtime.reset_for_tests()
    propagation.reset_for_tests()


async def _audits(action: str):
    from app.core.database import async_session
    from app.models.models import AuditLog

    async with async_session() as s:
        return (await s.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))).scalars().all()


async def _raw_setting(key: str):
    from app.core.database import async_session

    async with async_session() as s:
        return (await s.execute(text("SELECT value FROM system_settings WHERE `key` = :k"), {"k": key})).scalar()


def test_d1_propagation_settings_roundtrip_and_audit(env):
    c, h = env["client"], env["admin_h"]
    r = c.put("/api/v1/settings/propagation", headers=h,
              json={"enabled": True, "ipv6": True, "resolvers": ["2606:4700:4700:0::1111", " 10.0.0.53 ", "10.0.0.53"]})
    assert r.status_code == 200, r.text
    got = c.get("/api/v1/settings/propagation", headers=h).json()
    assert got["enabled"] is True and got["ipv6"] is True and got["check_authoritative"] is True
    assert got["resolvers"] == ["2606:4700:4700::1111", "10.0.0.53"]
    audits = _run(_audits("PROPAGATION_SETTINGS_UPDATE"))
    assert len(audits) == 1 and audits[0].resource_type == "settings" and audits[0].resource_name == "propagation"
    assert audits[0].details["changed"]["enabled"] == {"from": False, "to": True}
    assert audits[0].user_id == env["admin"].id

    r = c.put("/api/v1/settings/propagation", headers=h, json={"resolvers": ["127.0.0.1"]})
    assert r.status_code == 422 and "Loopback" in r.json()["detail"]
    assert c.get("/api/v1/settings/propagation", headers=h).json()["resolvers"] == got["resolvers"]
    assert len(_run(_audits("PROPAGATION_SETTINGS_UPDATE"))) == 1


def test_d2_admin_panel_token_is_rejected(env):
    from app.core.database import async_session
    from app.services import panel_token as ptk

    async def make_token():
        async with async_session() as s:
            _row, plain = await ptk.create_token(s, env["admin"].id, "ci-admin", allow_admin=True)
            await s.commit()
            return plain

    th = {"Authorization": f"Bearer {_run(make_token())}"}
    c = env["client"]
    assert c.get("/api/v1/metrics", headers=th).status_code == 200  # Token selbst ist gueltig (allow_admin)
    for method, path in (("GET", "propagation"), ("PUT", "propagation"), ("GET", "metrics"), ("PUT", "metrics"),
                         ("POST", "metrics/token"), ("DELETE", "metrics/token"), ("GET", "monitoring/status")):
        kw = {"json": {}} if method in ("PUT", "POST") else {}
        r = c.request(method, f"/api/v1/settings/{path}", headers=th, **kw)
        assert r.status_code == 403, (method, path, r.text)
    # Nicht-Admin mit Session ebenfalls 403
    assert c.get("/api/v1/settings/monitoring/status", headers=env["user_h"]).status_code == 403
    assert c.get("/api/v1/settings/monitoring/status", headers=env["admin_h"]).status_code == 200


def test_d3_metrics_token_lifecycle(env):
    from app.services import metrics_runtime

    c, h = env["client"], env["admin_h"]
    assert c.get("/metrics").status_code == 404
    r = c.post("/api/v1/settings/metrics/token", headers=h, json={})
    assert r.status_code == 201, r.text
    tok = r.json()["token"]
    raw = _run(_raw_setting("metrics_token"))
    assert raw.startswith("enc:v1:") and tok not in raw
    info = c.get("/api/v1/settings/metrics", headers=h).json()
    assert info["token_set"] and info["token_hint"] == tok[:19] + "…" and tok not in str(info)
    assert info["effective_enabled"] is False

    assert c.delete("/api/v1/settings/metrics/token", headers=h).status_code == 200
    r = c.put("/api/v1/settings/metrics", headers=h, json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "Bitte zuerst einen Scrape-Token erzeugen."

    tok = c.post("/api/v1/settings/metrics/token", headers=h, json={}).json()["token"]
    r = c.put("/api/v1/settings/metrics", headers=h, json={"enabled": True})
    assert r.status_code == 200 and r.json()["effective_enabled"] is True

    # metrics_runtime liest den verschluesselten Token aus der echten DB
    cfg = _run(metrics_runtime.get_metrics_config(force=True))
    assert cfg.effective_enabled and cfg.source == "db" and cfg.token == tok
    metrics_runtime.reset_for_tests()
    r = c.get("/metrics", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and "pdnsmgr_database_up 1.0" in r.text
    assert c.get("/metrics", headers={"Authorization": "Bearer falsch"}).status_code == 401

    r = c.delete("/api/v1/settings/metrics/token", headers=h)
    assert r.status_code == 200
    assert _run(_raw_setting("metrics_enabled")) == "false" and _run(_raw_setting("metrics_token")) is None
    assert c.get("/metrics", headers={"Authorization": f"Bearer {tok}"}).status_code == 404
    actions = [a.action for a in _run(_audits("METRICS_TOKEN_CREATE"))] + \
        [a.action for a in _run(_audits("METRICS_TOKEN_DELETE"))]
    assert actions.count("METRICS_TOKEN_CREATE") == 2 and actions.count("METRICS_TOKEN_DELETE") == 2
    assert all(tok not in str(a.details) for a in _run(_audits("METRICS_TOKEN_CREATE")))


def test_d4_propagation_acl(env, monkeypatch):
    from app.services import propagation as prop
    from app.services.pdns_client import pdns_manager
    from fakes.pdns import FakePowerDNSClient, make_zone

    monkeypatch.setattr(pdns_manager, "clients", {"srv": FakePowerDNSClient("srv", [make_zone("a.example."),
                                                                                    make_zone("b.example.")])})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    c, h = env["client"], env["user_h"]
    # externe Abfragen aus (D1 hat sie in derselben Testdatenbank eingeschaltet) – kein Netzverkehr im Test
    assert c.put("/api/v1/settings/propagation", headers=env["admin_h"], json={"enabled": False}).status_code == 200
    r = c.get("/api/v1/zones/srv/a.example./propagation", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["zone"] == "a.example." and data["sources"][0]["is_reference"] and data["external"]["enabled"] is False
    r = c.get("/api/v1/zones/srv/b.example./propagation", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "Keine Berechtigung für diese Zone"
    # Einstellungen aus der echten DB wirken (load_settings), Ergebnis-Cache wird nach PUT geleert
    assert prop._cache  # Ergebnis aus dem ersten Aufruf
    c.put("/api/v1/settings/propagation", headers=env["admin_h"], json={"check_authoritative": False})
    assert not prop._cache


def test_d5_login_metrics(env):
    from app.core import metrics as prom

    def val(result):
        return prom.REGISTRY.get_sample_value("pdnsmgr_login_attempts_total",
                                              {"method": "password", "result": result}) or 0.0

    c = env["client"]
    f0, s0 = val("failure"), val("success")
    r = c.post("/api/v1/auth/login", data={"username": env["user"].username, "password": "falsch-falsch"})
    assert r.status_code in (400, 401), r.text
    assert val("failure") == f0 + 1
    r = c.post("/api/v1/auth/login", data={"username": env["user"].username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert val("success") == s0 + 1


def test_monitoring_status_with_db(env):
    r = env["client"].get("/api/v1/settings/monitoring/status", headers=env["admin_h"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) >= {"ok", "background", "migration_errors", "servers_not_loaded", "secrets", "checked_at"}
    assert data["secrets"]["mode"] == "encrypted"
