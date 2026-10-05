"""Panel-Token-Scope per HTTP gegen die Router von W0-INT-BE2a (F14 9.2 Nr. 2–15, ohne DB).

Test-App mit ``include_router`` (auth, panel_tokens, webhooks, search, servers, templates, settings) und
einer kleinen Probe-Route, die ``assert_zone_access`` aufruft (stellvertretend fuer zones/records/dnssec,
deren HTTP-Faelle W0-INT-BE2b in ``test_panel_token_scope_zones.py`` liefert).
Header immer ``Authorization: Bearer <PLAIN>``; Besitzer ist Admin, damit ``assert_zone_access`` ohne
Zonenrechte-Abfrage auskommt – der Token-Scope greift trotzdem (vor dem Admin-Shortcut).
"""
from datetime import timedelta

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from authfakes import PLAIN, FakePDNS, FakeSession, bearer, build_app, make_token, make_user
from app.core.auth import (
    SESSION_REQUIRED_DETAIL,
    TOKEN_NO_ADMIN_DETAIL,
    TOKEN_READ_ONLY_DETAIL,
    assert_zone_access,
    create_access_token,
    get_current_user,
)
from app.core.config import settings
from app.core.timeutil import utcnow
from app.routers import auth, panel_tokens, search, servers, settings as settings_router, templates, webhooks
from app.routers.servers import STATISTICS_ZONE_TOKEN_DETAIL
from app.services.pdns_client import pdns_manager

probe = APIRouter(prefix="/probe")


@probe.get("/{zone_id:path}")
async def probe_read(zone_id: str, current_user=Depends(get_current_user)):
    await assert_zone_access(None, current_user, zone_id)
    return {"ok": True}


@probe.post("/{zone_id:path}")
async def probe_write(zone_id: str, current_user=Depends(get_current_user)):
    await assert_zone_access(None, current_user, zone_id, write=True)
    return {"ok": True}


ROUTERS = (auth, panel_tokens, webhooks, search, servers, templates, settings_router, probe)


@pytest.fixture
def pdns(monkeypatch):
    fake = FakePDNS()
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": fake})
    return fake


def _client(token_row=None, user_row=None, **kw):
    session = FakeSession(token_row=token_row, user_row=user_row if user_row is not None else make_user(), **kw)
    return TestClient(build_app(session, *ROUTERS), raise_server_exceptions=False), session


# --- Nr. 2/3: Zonen-Scope ueber assert_zone_access ----------------------------------------------
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_out_of_scope_zone_returns_403_without_pdns_call(pdns, method):
    c, _ = _client(make_token())
    r = c.request(method, "/api/v1/probe/other.example.", headers=bearer())
    assert r.status_code == 403
    assert "„other.example.“ nicht freigegeben" in r.json()["detail"]
    assert pdns.calls == {}


@pytest.mark.parametrize("zone", ["allowed.example.", "ALLOWED.example"])
def test_in_scope_zone_ok_case_insensitive(pdns, zone):
    c, _ = _client(make_token())
    assert c.get(f"/api/v1/probe/{zone}", headers=bearer()).status_code == 200
    assert c.post(f"/api/v1/probe/{zone}", headers=bearer()).status_code == 200


# --- Nr. 5: Suche ------------------------------------------------------------------------------
def test_search_filtered_by_scope(pdns):
    c, _ = _client(make_token())
    r = c.get("/api/v1/search/srv1?q=exa", headers=bearer())
    assert r.status_code == 200, r.text
    zones = {x["zone_id"] for x in r.json()["results"]}
    assert zones == {"allowed.example."}
    r = c.get("/api/v1/search?q=exa", headers=bearer())
    assert {x["zone_id"] for x in r.json()["servers"]["srv1"]["results"]} == {"allowed.example."}


# --- Nr. 6: Server-Zaehler, Statistik, url -----------------------------------------------------
def test_servers_zone_count_statistics_and_url(pdns):
    c, _ = _client(make_token())
    srv = c.get("/api/v1/servers", headers=bearer()).json()["servers"][0]
    assert srv["zone_count"] == 1 and srv["is_reachable"] is True
    assert "url" not in srv  # Token ohne Admin-Freigabe sieht keine interne Adresse [S5]
    info = c.get("/api/v1/servers/srv1", headers=bearer()).json()
    assert info["statistics"] == [] and info["zone_count"] == 1 and "url" not in info
    r = c.get("/api/v1/servers/srv1/statistics", headers=bearer())
    assert r.status_code == 403 and r.json()["detail"] == STATISTICS_ZONE_TOKEN_DETAIL


def test_servers_without_scope_admin_token(pdns):
    # Admin-Token ohne Scope, ohne allow_admin: alle Zonen, aber keine Admin-Daten (E-F14-1)
    c, _ = _client(make_token(scope_zones=None))
    assert c.get("/api/v1/servers", headers=bearer()).json()["servers"][0]["zone_count"] == 3
    info = c.get("/api/v1/servers/srv1", headers=bearer()).json()
    assert info["statistics"] == [] and "url" not in info
    r = c.get("/api/v1/servers/srv1/statistics", headers=bearer())
    assert r.status_code == 403 and r.json()["detail"] == TOKEN_NO_ADMIN_DETAIL
    # mit Admin-Freigabe: Statistik und url
    c, _ = _client(make_token(scope_zones=None, allow_admin=True))
    assert c.get("/api/v1/servers/srv1/statistics", headers=bearer()).status_code == 200
    info = c.get("/api/v1/servers/srv1", headers=bearer()).json()
    assert info["statistics"] and info["url"] == FakePDNS.url
    assert c.get("/api/v1/servers", headers=bearer()).json()["servers"][0]["url"] == FakePDNS.url


def test_servers_non_admin_session_sees_no_url_or_statistics(pdns):
    user = make_user(role="user", uid=5, username="bob")
    c, _ = _client(None, user, zone_access=[("allowed.example.", "manage")])
    jwt = create_access_token(data={"sub": "5"}, user=user)
    h = {"Authorization": f"Bearer {jwt}"}
    srv = c.get("/api/v1/servers", headers=h).json()["servers"][0]
    assert srv["zone_count"] == 1 and "url" not in srv
    assert c.get("/api/v1/servers/srv1/statistics", headers=h).status_code == 403


# --- Nr. 7/8: Admin-Endpunkte ------------------------------------------------------------------
ADMIN_CASES = [
    ("GET", "/api/v1/audit-log", None),
    ("GET", "/api/v1/audit-log/export", None),
    ("GET", "/api/v1/auth/users", None),
    ("GET", "/api/v1/auth/users/1/zones", None),
    ("POST", "/api/v1/templates", {"name": "t1"}),
    ("PUT", "/api/v1/templates/1", {"name": "t2"}),
    ("DELETE", "/api/v1/templates/1", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_CASES)
def test_admin_endpoints_require_allow_admin(pdns, method, path, body):
    c, s = _client(make_token(scope_zones=None))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == TOKEN_NO_ADMIN_DETAIL
    assert s.added == [] and s.deleted == []


def test_admin_endpoint_allowed_with_allow_admin(pdns):
    c, _ = _client(make_token(scope_zones=None, allow_admin=True))
    r = c.get("/api/v1/audit-log", headers=bearer())
    assert r.status_code == 200 and r.json()["entries"] == []


def test_non_admin_owner_with_allow_admin_flag_gets_no_admin(pdns):
    # allow_admin wirkt nur bei Admin-Besitzern (Herabstufung macht das Flag wirkungslos)
    c, _ = _client(make_token(scope_zones=None, allow_admin=True), make_user(role="user", uid=1, username="u"))
    r = c.get("/api/v1/audit-log", headers=bearer())
    assert r.status_code == 403 and r.json()["detail"] == "Nur Administratoren haben Zugriff"


# --- Nr. 9: Lese-Token -------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v1/templates", {"name": "t1"}),
    ("PUT", "/api/v1/templates/1", {"name": "t2"}),
    ("DELETE", "/api/v1/templates/1", None),
    ("POST", "/api/v1/probe/allowed.example.", None),
    ("PUT", "/api/v1/auth/me", {"display_name": "x"}),
    ("DELETE", "/api/v1/auth/me/panel-tokens/7", None),
])
def test_read_token_blocks_mutations(pdns, method, path, body):
    c, s = _client(make_token(scope_zones=None, permission="read", allow_admin=True))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403 and r.json()["detail"] == TOKEN_READ_ONLY_DETAIL
    assert s.flushes == 0 and pdns.calls == {}


def test_read_token_allows_reads(pdns):
    c, _ = _client(make_token(scope_zones=None, permission="read", allow_admin=True))
    assert c.get("/api/v1/templates", headers=bearer()).status_code == 200
    assert c.get("/api/v1/probe/allowed.example.", headers=bearer()).status_code == 200
    assert c.get("/api/v1/audit-log", headers=bearer()).status_code == 200


def test_unknown_permission_is_read_only(pdns):
    c, _ = _client(make_token(scope_zones=None, permission="write"))
    assert c.post("/api/v1/probe/allowed.example.", headers=bearer()).status_code == 403


# --- Nr. 11/12: Token-Zustaende ------------------------------------------------------------------
@pytest.mark.parametrize("kw,detail", [
    ({"is_active": False}, "API-Token ist deaktiviert"),
    ({"expires_at": utcnow() - timedelta(minutes=1)}, "API-Token ist abgelaufen"),
    ({"revoked_at": utcnow()}, "Ungültiger API-Token"),
])
def test_token_states_401(pdns, kw, detail):
    c, _ = _client(make_token(**kw))
    r = c.get("/api/v1/auth/me", headers=bearer())
    assert r.status_code == 401 and r.json()["detail"] == detail
    assert r.headers.get("www-authenticate") == "Bearer"


def test_unknown_token_and_inactive_owner_401(pdns):
    c, _ = _client(None)
    r = c.get("/api/v1/auth/me", headers=bearer())
    assert r.status_code == 401 and r.json()["detail"] == "Ungültiger API-Token"
    owner = make_user()
    owner.is_active = False
    c, _ = _client(make_token(), owner)
    assert c.get("/api/v1/auth/me", headers=bearer()).status_code == 401


def test_token_not_expired_yet_ok(pdns):
    c, _ = _client(make_token(expires_at=utcnow() + timedelta(days=1)))
    assert c.get("/api/v1/auth/me", headers=bearer()).status_code == 200


def test_panel_token_in_cookie_rejected(pdns):
    c, _ = _client(make_token(scope_zones=None, allow_admin=True))
    c.cookies.set(settings.AUTH_COOKIE_NAME, PLAIN)
    r = c.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["detail"] == "Ungültiger API-Token"


# --- Nr. 13: /auth/me ---------------------------------------------------------------------------
def test_auth_me_reports_token_scope(pdns):
    c, _ = _client(make_token())
    r = c.get("/api/v1/auth/me", headers=bearer())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth"]["via"] == "panel_token"
    tok = body["auth"]["token"]
    assert tok["scope_zones"] == ["allowed.example."] and tok["permission"] == "manage"
    assert tok["allow_admin"] is False and tok["id"] == 7 and tok["token_prefix"] == "dnsmgr_usr_test…"
    assert PLAIN not in r.text and make_token().token_hash not in r.text


def test_auth_me_session_block(pdns):
    user = make_user()
    c, _ = _client(None, user)
    jwt = create_access_token(data={"sub": "1"}, user=user)
    body = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {jwt}"}).json()
    assert body["auth"] == {"via": "session", "token": None}


# --- Nr. 14: Session-Pflicht --------------------------------------------------------------------
SESSION_ONLY = [
    ("GET", "/api/v1/auth/me/panel-tokens", None),
    ("POST", "/api/v1/auth/me/panel-tokens", {"name": "x"}),
    ("GET", "/api/v1/auth/me/totp/status", None),
    ("GET", "/api/v1/auth/me/webauthn/credentials", None),
    ("POST", "/api/v1/auth/me/webhooks", {"name": "h", "url": "https://example.com/x"}),
    ("PUT", "/api/v1/auth/me/webhooks/1", {"name": "h"}),
    ("DELETE", "/api/v1/auth/me/webhooks/1", None),
    ("POST", "/api/v1/auth/users", {"username": "neu", "password": "geheim123"}),
    ("GET", "/api/v1/settings/servers", None),
    ("GET", "/api/v1/settings/servers/1/api-key", None),
    ("PUT", "/api/v1/settings/smtp", {"host": "x"}),
    ("POST", "/api/v1/settings/acme/tokens", {"name": "a", "allowed_zones": ["allowed.example."]}),
    ("GET", "/api/v1/settings/admin-info", None),
]


@pytest.mark.parametrize("method,path,body", SESSION_ONLY)
def test_session_only_endpoints_reject_token(pdns, method, path, body):
    c, s = _client(make_token(scope_zones=None, allow_admin=True))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, (path, r.text)
    assert r.json()["detail"] == SESSION_REQUIRED_DETAIL
    assert s.added == []


def test_public_app_info_stays_public(pdns):
    c, _ = _client(None)
    assert c.get("/api/v1/settings/app-info").status_code == 200


# --- Webhook-Liste per Token: ohne url (F6 3.1) ---------------------------------------------------
def test_webhook_list_hides_url_for_token(pdns):
    from app.models.models import Webhook

    hook = Webhook(id=3, user_id=1, name="h", url="https://hooks.example/T0K3N", secret="s", events=["*"], is_active=True)
    user = make_user()
    session = FakeSession(token_row=make_token(scope_zones=None), user_row=user, extra={Webhook: [hook]})
    c = TestClient(build_app(session, *ROUTERS), raise_server_exceptions=False)
    r = c.get("/api/v1/auth/me/webhooks", headers=bearer())
    assert r.status_code == 200 and r.json()["webhooks"][0]["url"] is None
    assert "T0K3N" not in r.text
    jwt = create_access_token(data={"sub": "1"}, user=user)
    r = c.get("/api/v1/auth/me/webhooks", headers={"Authorization": f"Bearer {jwt}"})
    assert r.json()["webhooks"][0]["url"] == "https://hooks.example/T0K3N"


# --- Nr. 15: last_used gedrosselt ----------------------------------------------------------------
def test_last_used_throttled(pdns):
    tok = make_token()
    c, s = _client(tok)
    c.get("/api/v1/auth/me", headers=bearer())
    first = tok.last_used_at
    flushes_after_first = s.flushes
    assert first is not None and tok.last_used_ip == "testclient"
    c.get("/api/v1/auth/me", headers=bearer())
    assert tok.last_used_at == first and s.flushes == flushes_after_first


# --- Kontext wird zwischen Requests nicht vererbt ---------------------------------------------------
def test_context_not_leaking_between_requests(pdns):
    user = make_user()
    c, _ = _client(make_token(), user)
    assert c.get("/api/v1/auth/me", headers=bearer()).json()["auth"]["via"] == "panel_token"
    jwt = create_access_token(data={"sub": "1"}, user=user)
    h = {"Authorization": f"Bearer {jwt}"}
    # Browser-Session danach: kein Token-Scope mehr -> Admin sieht alle Zonen und die Statistik
    assert c.get("/api/v1/auth/me", headers=h).json()["auth"]["via"] == "session"
    assert c.get("/api/v1/servers/srv1/statistics", headers=h).status_code == 200
