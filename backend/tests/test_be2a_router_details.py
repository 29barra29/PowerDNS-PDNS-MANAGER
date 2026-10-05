"""Einzelpunkte aus dem Block W0-INT-BE2a: ACME-Kontext, Reveal-Audit, Zonen-Index, Loeschketten, DbWrite."""
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from app.core.auth import create_access_token, get_current_user
from app.core.database import DbWrite, get_db
from app.models.models import AcmeToken, AuditLog, ServerConfig, Webhook
from app.routers import acme, auth, settings as settings_router, templates, webhooks


def _session_headers(user):
    return {"Authorization": f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"}


def _audits(session, action):
    return [o for o in session.added if isinstance(o, AuditLog) and o.action == action]


def test_acme_sets_auth_context_and_audits_via_write_audit(monkeypatch):
    from app.services import acme as acme_service

    row = AcmeToken(id=4, name="certbot", created_by_id=1, allowed_zones=["example.com."], is_active=True)

    async def verify(db, plaintext, remote_ip=None):
        return row if plaintext == "acme_ok" else None

    async def present(db, *, fqdn, validation, allowed_zones):
        return {"zone": "example.com.", "fqdn": f"_acme-challenge.{fqdn}."}

    monkeypatch.setattr(acme_service, "verify_token", verify)
    monkeypatch.setattr(acme_service, "present_challenge", present)
    session = FakeSession()
    c = TestClient(build_app(session, acme), raise_server_exceptions=False)
    r = c.post("/api/v1/acme/present", headers={"Authorization": "Bearer acme_ok"},
               json={"domain": "www.example.com", "validation": "abc"})
    assert r.status_code == 200, r.text
    (audit,) = _audits(session, "ACME_PRESENT")
    assert audit.details["auth"] == {"via": "acme_token"}
    assert audit.details["token_name"] == "certbot" and audit.zone_name == "example.com."
    assert audit.client_ip == "testclient" and audit.user_id == 1
    assert c.post("/api/v1/acme/present", headers={"Authorization": "Bearer falsch"},
                  json={"domain": "x", "validation": "y"}).status_code == 401


def test_reveal_api_key_writes_audit(monkeypatch):
    admin = make_user()
    cfg = ServerConfig(id=1, name="ns1", url="http://ns1:8081", api_key="geheim", is_active=True)
    session = FakeSession(user_row=admin, extra={ServerConfig: [cfg]})
    c = TestClient(build_app(session, settings_router), raise_server_exceptions=False)
    r = c.get("/api/v1/settings/servers/1/api-key", headers=_session_headers(admin))
    assert r.status_code == 200 and r.json()["api_key"] == "geheim"
    (audit,) = _audits(session, "REVEAL_API_KEY")
    assert audit.server_name == "ns1" and audit.user_id == 1 and audit.actor_username == "admin"
    assert "geheim" not in str(audit.details)


def test_server_crud_invalidates_zone_index(monkeypatch):
    from app.services import pdns_client, zone_index

    calls = []
    monkeypatch.setattr(zone_index, "invalidate", lambda server=None: calls.append(server))
    monkeypatch.setattr(pdns_client.pdns_manager, "add_server", lambda *a, **k: None)
    monkeypatch.setattr(pdns_client.pdns_manager, "update_server", lambda *a, **k: None)
    monkeypatch.setattr(pdns_client.pdns_manager, "remove_server", lambda *a, **k: None)
    admin = make_user()
    cfg = ServerConfig(id=1, name="ns1", url="http://ns1:8081", api_key="k", is_active=True, allow_writes=True)
    session = FakeSession(user_row=admin, extra={ServerConfig: []})
    c = TestClient(build_app(session, settings_router), raise_server_exceptions=False)
    h = _session_headers(admin)
    r = c.post("/api/v1/settings/servers", headers=h, json={"name": "ns2", "url": "http://ns2:8081", "api_key": "k2"})
    assert r.status_code == 201, r.text
    session.extra[ServerConfig] = [cfg]
    assert c.put("/api/v1/settings/servers/1", headers=h, json={"description": "x"}).status_code == 200
    assert c.delete("/api/v1/settings/servers/1", headers=h).status_code == 200
    assert len(calls) == 3


def test_delete_user_removes_webhook_deliveries():
    admin = make_user()
    session = FakeSession(user_row=admin)
    c = TestClient(build_app(session, auth), raise_server_exceptions=False)
    r = c.delete("/api/v1/auth/users/9", headers=_session_headers(admin))
    assert r.status_code == 200, r.text
    sql = [str(s) for s in session.executed]
    deliveries = next(i for i, s in enumerate(sql) if s.startswith("DELETE FROM webhook_deliveries"))
    hooks = next(i for i, s in enumerate(sql) if s.startswith("DELETE FROM webhooks"))
    assert deliveries < hooks
    (audit,) = _audits(session, "USER_DELETE")
    assert audit.details["deleted_webhook_deliveries"] == 0 and audit.details["deleted_webhooks"] == 0


def test_delete_webhook_removes_its_deliveries():
    user = make_user()
    hook = Webhook(id=3, user_id=1, name="h", url="https://hooks.example/x", secret="s", events=["*"], is_active=True)
    session = FakeSession(user_row=user, extra={Webhook: [hook]})
    c = TestClient(build_app(session, webhooks), raise_server_exceptions=False)
    r = c.delete("/api/v1/auth/me/webhooks/3", headers=_session_headers(user))
    assert r.status_code == 200 and r.json() == {"message": "Webhook gelöscht", "deleted_deliveries": 0}
    assert hook in session.deleted
    assert any(str(s).startswith("DELETE FROM webhook_deliveries") for s in session.executed)


def test_templates_create_uses_flush_not_handler_commit():
    admin = make_user()
    session = FakeSession(user_row=admin)
    c = TestClient(build_app(session, templates), raise_server_exceptions=False)
    r = c.post("/api/v1/templates", headers=_session_headers(admin), json={"name": "Basis"})
    assert r.status_code == 200, r.text
    assert session.commits == 0  # Commit macht die DbWrite-Abhaengigkeit (hier ueberschrieben)


def test_write_route_shares_one_session_with_authentication():
    """get_current_user nutzt get_db mit scope=function -> bei DbWrite-Handlern genau EINE Session (B.7)."""
    admin = make_user()
    created = []

    async def counting_db():
        s = FakeSession(user_row=admin)
        created.append(s)
        yield s

    r = APIRouter()

    @r.post("/probe")
    async def probe(db: DbWrite, current_user=Depends(get_current_user)):
        return {"same": db is created[0]}

    app = build_app(FakeSession(), r)
    app.dependency_overrides[get_db] = counting_db
    res = TestClient(app).post("/api/v1/probe", headers=_session_headers(admin))
    assert res.status_code == 200 and res.json() == {"same": True}
    assert len(created) == 1


@pytest.mark.parametrize("module", [auth, settings_router, templates, webhooks, acme])
def test_mutating_routes_use_function_scoped_sessions(module):
    """Alle get_db-Abhaengigkeiten schreibender Routen der BE2a-Router haben scope=function (DbWrite)."""
    from fastapi.routing import APIRoute

    from app.routers import panel_tokens

    for mod in (module, panel_tokens):
        for route in mod.router.routes:
            if not isinstance(route, APIRoute) or not (route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
                continue
            stack = list(route.dependant.dependencies)
            while stack:
                d = stack.pop()
                if d.call is get_db:
                    assert d.scope == "function", (route.path, route.methods)
                stack.extend(d.dependencies)
