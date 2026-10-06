"""Routen und Auth-Abhaengigkeiten von DynDNS/PTR (F9 9 test_dyndns_routes, Bauplan B.13 [S5, S6, S11]).

- ``GET /nic/update`` ist eine Root-Route (nur GET) vor dem SPA-Catch-all und liefert Text statt index.html.
- Token-Verwaltung haengt direkt an ``get_session_user``, Admin-Uebersicht und Settings an
  ``get_admin_session_user``; ein Panel-Token-Kontext bekommt 403.
- ``/dyndns/info``: ``trust_proxy_headers`` und ``proxy_warning`` nur fuer Admins.
- Settings ``/settings/dyndns`` und ``/settings/ptr`` speichern und auditieren nur echte Aenderungen.
- ``/ptr/config`` und ``/ptr/lookup`` (422 bei ungueltiger IP).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import auth as core_auth  # noqa: E402
from app.core.config import settings as app_settings  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import User  # noqa: E402
from app.routers import ptr as ptr_router  # noqa: E402
from app.routers import settings_dyndns  # noqa: E402
from app.services import dyndns, ptr, zone_index  # noqa: E402
from fakes.pdns import FakeDB, fake_pdns, make_zone  # noqa: E402,F401
from route_policy import iter_app_routes  # noqa: E402

A = "/api/v1"


def _deps(path: str, method: str) -> list[str]:
    for r in iter_app_routes(app):
        if r.path == path and r.method == method:
            return [getattr(d.call, "__name__", "") for d in r.dependant.dependencies]
    raise AssertionError(f"Route {method} {path} fehlt")


# ---------------------------------------------------------------------------------------------------------------------
# Routen-Walk
# ---------------------------------------------------------------------------------------------------------------------
def test_nic_update_root_route_get_only_before_spa():
    paths = []
    for rc in app.routes:
        paths.append(getattr(rc, "path", None))
    routes = {(r.method, r.path) for r in iter_app_routes(app)}
    assert ("GET", "/nic/update") in routes
    assert not any(p == "/nic/update" and m != "GET" for m, p in routes)
    # Position in app.routes: /nic/update vor dem SPA-Catch-all
    flat = []
    for r in app.routes:
        sub = getattr(getattr(r, "original_router", None), "routes", None)
        if sub:
            flat.extend((getattr(x, "path", None) for x in sub))
        flat.append(getattr(r, "path", None))
    assert "/{path:path}" in flat and "/nic/update" in flat
    assert flat.index("/nic/update") < flat.index("/{path:path}")


@pytest.mark.parametrize("method, path", [
    ("GET", f"{A}/dyndns/tokens"), ("POST", f"{A}/dyndns/tokens"), ("PUT", f"{A}/dyndns/tokens/{{token_id}}"),
    ("DELETE", f"{A}/dyndns/tokens/{{token_id}}"), ("POST", f"{A}/dyndns/tokens/{{token_id}}/rotate"),
    ("GET", f"{A}/dyndns/zones"),
])
def test_token_routes_require_session(method, path):
    assert "get_session_user" in _deps(path, method)


@pytest.mark.parametrize("method, path", [
    ("GET", f"{A}/dyndns/admin/tokens"), ("GET", f"{A}/settings/dyndns"), ("PUT", f"{A}/settings/dyndns"),
    ("GET", f"{A}/settings/ptr"), ("PUT", f"{A}/settings/ptr"),
])
def test_admin_routes_require_admin_session(method, path):
    assert "get_admin_session_user" in _deps(path, method)


@pytest.mark.parametrize("method, path", [
    ("GET", "/nic/update"), ("GET", f"{A}/dyndns/update"), ("POST", f"{A}/dyndns/update"),
    ("GET", f"{A}/dyndns/whoami"),
])
def test_update_routes_have_no_session_auth(method, path):
    names = _deps(path, method)
    assert not {"get_current_user", "get_session_user", "get_admin_session_user", "get_admin_user"} & set(names)


def test_info_and_ptr_routes_use_current_user():
    for path in (f"{A}/dyndns/info", f"{A}/ptr/config", f"{A}/ptr/lookup"):
        assert "get_current_user" in _deps(path, "GET")


# ---------------------------------------------------------------------------------------------------------------------
# Laufzeit
# ---------------------------------------------------------------------------------------------------------------------
class Db(FakeDB):
    pass


@pytest.fixture
def client(monkeypatch, fake_pdns):
    zone_index.invalidate()
    dyndns.reset_state_for_tests()
    store: dict = {}

    async def get_bool_setting(db, key, default):
        v = store.get(key)
        return default if v is None else v == "true"

    async def set_settings(db, mapping):
        for k, v in mapping.items():
            store[k] = "true" if v is True else "false" if v is False else v

    for mod in (dyndns, settings_dyndns, ptr_router):
        monkeypatch.setattr(mod, "get_bool_setting", get_bool_setting)
    monkeypatch.setattr(settings_dyndns, "set_settings", set_settings)
    db = Db()

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    c = TestClient(app, raise_server_exceptions=False)
    yield SimpleNamespace(client=c, store=store, db=db, pdns=fake_pdns)
    for dep in (get_db, core_auth.get_current_user, core_auth.get_session_user, core_auth.get_admin_session_user):
        app.dependency_overrides.pop(dep, None)
    zone_index.invalidate()


def _as(user: User, via: str = "session"):
    async def current(request: Request):
        core_auth.set_auth_context(request, via, None, username=user.username)
        return user

    app.dependency_overrides[core_auth.get_current_user] = current


ADMIN = User(id=1, username="root", role="admin", is_active=True, hashed_password="x")
BOB = User(id=5, username="bob", role="user", is_active=True, hashed_password="x")


def test_nic_update_without_auth_is_text_badauth(client):
    r = client.client.get("/nic/update")
    assert r.status_code == 401 and r.text == "badauth" and "html" not in r.headers["content-type"]


def test_panel_token_cannot_manage_dyndns_tokens(client):
    _as(ADMIN, via="panel_token")
    r = client.client.post(f"{A}/dyndns/tokens", json={"name": "x", "hostnames": ["a.example.com"]},
                           headers={"Authorization": "Bearer dnsmgr_usr_x"})
    assert r.status_code == 403
    assert client.client.get(f"{A}/dyndns/tokens").status_code == 403
    assert client.client.get(f"{A}/settings/dyndns").status_code == 403
    assert client.client.get(f"{A}/dyndns/info").status_code == 200  # user-Route: Token erlaubt


def test_info_admin_only_fields(client, monkeypatch):
    monkeypatch.setattr(app_settings, "TRUST_PROXY_HEADERS", False)

    async def base(db):
        return "https://dns.example.com"

    import app.services.password_reset_mail as prm
    monkeypatch.setattr(prm, "resolve_public_base_url", base)
    _as(BOB)
    body = client.client.get(f"{A}/dyndns/info").json()
    assert body["enabled"] is True and body["base_url"] == "https://dns.example.com"
    assert body["update_path"] == "/nic/update" and body["max_tokens"] == 50
    assert "trust_proxy_headers" not in body and body["proxy_warning"] is False
    _as(ADMIN)
    monkeypatch.setattr("app.routers.dyndns._peer_is_private", lambda request: True)
    body = client.client.get(f"{A}/dyndns/info").json()
    assert body["trust_proxy_headers"] is False and body["proxy_warning"] is True
    monkeypatch.setattr(app_settings, "TRUST_PROXY_HEADERS", True)
    body = client.client.get(f"{A}/dyndns/info").json()
    assert body["trust_proxy_headers"] is True and body["proxy_warning"] is False


def test_settings_dyndns_roundtrip_with_audit(client, monkeypatch):
    audits = []

    async def write_audit(db, action, rtype, name=None, **kw):
        audits.append((action, rtype, name, kw))

    monkeypatch.setattr(settings_dyndns, "write_audit", write_audit)
    _as(ADMIN)
    r = client.client.get(f"{A}/settings/dyndns")
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["allow_private_ips"] is False
    r = client.client.put(f"{A}/settings/dyndns", json={"allow_private_ips": True})
    assert r.status_code == 200 and r.json()["settings"] == {"enabled": True, "allow_private_ips": True}
    assert client.store[dyndns.KEY_ALLOW_PRIVATE] == "true"
    assert audits == [("DYNDNS_SETTINGS_UPDATE", "settings", "dyndns",
                       {"user_id": 1, "details": {"changed": {"allow_private_ips": {"from": False, "to": True}}}})]
    client.client.put(f"{A}/settings/dyndns", json={"allow_private_ips": True})
    assert len(audits) == 1  # keine Aenderung -> kein Audit
    _as(BOB)
    assert client.client.put(f"{A}/settings/dyndns", json={"enabled": False}).status_code == 403


def test_settings_ptr_roundtrip(client, monkeypatch):
    audits = []

    async def write_audit(db, action, rtype, name=None, **kw):
        audits.append(action)

    monkeypatch.setattr(settings_dyndns, "write_audit", write_audit)
    _as(ADMIN)
    assert client.client.get(f"{A}/settings/ptr").json() == {"auto_default": False}
    r = client.client.put(f"{A}/settings/ptr", json={"auto_default": True})
    assert r.status_code == 200 and r.json()["settings"] == {"auto_default": True}
    assert client.client.get(f"{A}/settings/ptr").json() == {"auto_default": True}
    assert audits == ["PTR_SETTINGS_UPDATE"]


def test_ptr_config_and_lookup_validation(client, monkeypatch):
    client.pdns.ns1.add_zone(make_zone("2.0.192.in-addr.arpa."))
    client.pdns.ns1.add_zone(make_zone("example.com."))
    _as(ADMIN)
    r = client.client.get(f"{A}/ptr/config")
    assert r.json() == {"auto_default": False, "reverse_zones_available": 1}
    r = client.client.get(f"{A}/ptr/lookup", params={"ip": "1.2.3"})
    assert r.status_code == 422 and r.json()["detail"] == "Ungueltige IP-Adresse"
    r = client.client.get(f"{A}/ptr/lookup", params={"ip": "192.0.2.10", "name": "host.example.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["would"] == "set" and r.json()["zone"] == "2.0.192.in-addr.arpa."


def test_ptr_lookup_without_read_hides_zone(client, monkeypatch):
    client.pdns.ns1.add_zone(make_zone("2.0.192.in-addr.arpa."))

    async def no_access(db, user, zone, *, write=False):
        return False

    monkeypatch.setattr(ptr, "has_zone_access", no_access)
    _as(BOB)
    body = client.client.get(f"{A}/ptr/lookup", params={"ip": "192.0.2.10"}).json()
    assert body["status"] == "forbidden" and body["zone"] is None and body["current"] is None
    assert "in-addr.arpa" not in (body["detail"] or "")


def test_router_orders():
    from app.routers import dyndns as dyndns_router

    assert dyndns_router.ROUTER_ORDER == 130 and ptr_router.ROUTER_ORDER == 135
    assert settings_dyndns.ROUTER_ORDER == 103
    assert dyndns_router.root_routers == [dyndns_router.compat_router]
    assert all(isinstance(r, APIRoute) and r.methods == {"GET"} for r in dyndns_router.compat_router.routes)
