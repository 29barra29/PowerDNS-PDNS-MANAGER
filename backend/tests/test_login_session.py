"""Tests fuer services/login_session.py (F10 Nr. 29, Plan B.4) und core/request_context.py (B.3)."""
import asyncio
import contextvars
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.core import metrics as prom
from app.core import request_context as rc
from app.core.config import settings
from app.services import login_session as ls
from fakes.pdns import FakeDB, FakeResult


def _request(ip="203.0.113.5"):
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/login", "headers": [],
                    "query_string": b"", "client": (ip, 40000)})


def _user(**kw):
    base = dict(id=7, username="alice", role="user", hashed_password="$2b$12$abc", email="a@example.com",
                display_name="Alice", is_active=True, created_at=datetime(2026, 1, 1, 12, 0), last_login=None,
                totp_enabled=False, totp_secret=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def captured(monkeypatch):
    state = {"audit": [], "cleared": []}

    async def fake_write_audit(db, action, resource_type, resource_name=None, **kw):
        state["audit"].append((action, resource_type, resource_name, kw))

    def fake_clear(ip, username=None):
        state["cleared"].append((ip, username))

    monkeypatch.setattr(ls, "write_audit", fake_write_audit)
    monkeypatch.setattr(ls, "clear_login_fails", fake_clear)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    return state


def _cookie_header(resp):
    return [v.decode() for k, v in resp.raw_headers if k.decode().lower() == "set-cookie"]


def _run_isolated(coro):
    """Eigener Kontext je Aufruf (wie ein Request); liefert (Ergebnis, Kontextwerte nach dem Lauf)."""
    async def wrapper():
        result = await coro
        return result, {"actor": rc.actor_username_ctx.get(), "ip": rc.client_ip_ctx.get()}

    ctx = contextvars.copy_context()
    return ctx.run(asyncio.run, wrapper())


def test_redirect_passthrough_cookie_audit_and_clear(captured, monkeypatch):
    db = FakeDB()
    user = _user()
    redirect = RedirectResponse("/", status_code=303)
    before = prom.REGISTRY.get_sample_value("pdnsmgr_login_attempts_total", {"method": "oidc", "result": "success"})
    resp, ctx = _run_isolated(ls.complete_login(db, user, _request(), method="oidc", response=redirect,
                                                audit_extra={"issuer": "https://idp.example"}))
    assert resp is redirect and resp.status_code == 303
    cookies = _cookie_header(resp)
    assert len(cookies) == 1
    c = cookies[0].lower()
    assert c.startswith(f"{settings.AUTH_COOKIE_NAME}=")
    assert "httponly" in c and "path=/" in c
    assert f"max-age={settings.AUTH_COOKIE_MAX_AGE}" in c
    assert f"samesite={settings.AUTH_COOKIE_SAMESITE}".lower() in c
    assert ("secure" in c.split("; ")) is bool(settings.AUTH_COOKIE_SECURE)
    assert captured["audit"] == [("LOGIN", "user", "alice", {"user_id": 7, "details": {
        "ip": "203.0.113.5", "method": "oidc", "issuer": "https://idp.example"}})]
    assert captured["cleared"] == [("203.0.113.5", "alice")]
    assert user.last_login is not None and db.flushes == 1
    after = prom.REGISTRY.get_sample_value("pdnsmgr_login_attempts_total", {"method": "oidc", "result": "success"})
    assert after == before + 1
    assert ctx == {"actor": "alice", "ip": "203.0.113.5"}
    assert rc.actor_username_ctx.get() is None  # kein Leck in den aeusseren Kontext


def test_cookie_parameters_identical_to_241_helper(captured):
    """set_session_cookie setzt exakt die Parameter des 2.4.1-Helpers routers.auth._set_session_cookie."""
    from fastapi.responses import JSONResponse
    from app.routers import auth as auth_router

    a = ls.set_session_cookie(JSONResponse({}), "tok")
    b = auth_router._set_session_cookie(JSONResponse({}), "tok")
    assert _cookie_header(a) == _cookie_header(b)


def test_clear_fails_false_does_not_clear(captured):
    _run_isolated(ls.complete_login(FakeDB(), _user(), _request(), method="password",
                                    response=RedirectResponse("/"), clear_fails=False))
    assert captured["cleared"] == []
    assert captured["audit"][0][3]["details"]["method"] == "password"


def test_json_response_with_body_extra(captured, monkeypatch):
    async def fake_user_to_dict(user, db):
        return {"id": user.id, "username": user.username}

    monkeypatch.setattr(ls, "user_to_dict", fake_user_to_dict)
    resp, _ = _run_isolated(ls.complete_login(FakeDB(), _user(), _request(), method="password+totp",
                                              status_code=201, body_extra={"hint": "x", "user": "ignored"}))
    assert resp.status_code == 201
    import json
    assert json.loads(resp.body) == {"hint": "x", "user": {"id": 7, "username": "alice"}}
    assert _cookie_header(resp)[0].startswith(f"{settings.AUTH_COOKIE_NAME}=")


def test_existing_context_is_not_overwritten(captured):
    async def run():
        rc.actor_username_ctx.set("schon-gesetzt")
        await ls.complete_login(FakeDB(), _user(), _request(), method="passkey", response=RedirectResponse("/"))
        return rc.actor_username_ctx.get()

    ctx = contextvars.copy_context()
    assert ctx.run(asyncio.run, run()) == "schon-gesetzt"


def test_token_is_valid_session_jwt(captured):
    from app.core.auth import decode_token

    resp, _ = _run_isolated(ls.complete_login(FakeDB(), _user(), _request(), method="password",
                                              response=RedirectResponse("/")))
    token = _cookie_header(resp)[0].split(";")[0].split("=", 1)[1]
    payload = decode_token(token)
    assert payload["sub"] == "7" and payload["typ"] == "access" and payload["role"] == "user" and "pwv" in payload


@pytest.mark.parametrize("method,label", [
    ("password", "password"), ("password+totp", "totp"), ("passkey", "passkey"), ("ldap", "ldap"),
    ("ldap+totp", "totp"), ("oidc", "oidc"), ("oidc+totp", "totp"), ("setup", "setup"),
])
def test_login_metric_method(method, label):
    assert ls.login_metric_method(method) == label


def test_transient_cookies(monkeypatch):
    from fastapi.responses import JSONResponse

    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    assert ls.transient_cookie_secure("https://dns.example.com") is True
    assert ls.transient_cookie_secure("http://dns.example.com") is False
    assert ls.transient_cookie_secure(None) is False
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    assert ls.transient_cookie_secure("http://x") is True

    r = JSONResponse({})
    ls.set_transient_cookie(r, ls.OIDC_STATE_COOKIE, "v", path=ls.OIDC_STATE_COOKIE_PATH, max_age=600, secure=True)
    c = _cookie_header(r)[0].lower()
    assert c.startswith("pdnsmgr_oidc=v") and "path=/api/v1/auth/oidc" in c and "httponly" in c
    assert "samesite=lax" in c and "max-age=600" in c and "secure" in c
    r2 = JSONResponse({})
    ls.delete_transient_cookie(r2, ls.TWO_FACTOR_COOKIE, path=ls.TWO_FACTOR_COOKIE_PATH)
    c2 = _cookie_header(r2)[0].lower()
    assert c2.startswith("pdnsmgr_2fa=") and "max-age=0" in c2 and "path=/api/v1/auth/login/2fa" in c2


@pytest.mark.wave_integration
async def test_user_to_dict_fields():
    """Braucht core.secrets.is_unreadable (W0-SECRETS, Welle 0a parallel)."""
    from app.core.secrets import UNREADABLE

    db = FakeDB(execute_handler=lambda stmt: FakeResult([("example.com.", "read"), ("b.example.", None)]))
    d = await ls.user_to_dict(_user(totp_enabled=True, totp_secret=UNREADABLE), db)
    assert d["zones"] == ["example.com.", "b.example."]
    assert d["zone_permissions"] == {"example.com.": "read", "b.example.": "manage"}
    assert d["totp_unreadable"] is True
    assert d["must_change_password"] is False and d["auth_source"] == "local"
    assert d["created_at"] == "2026-01-01T12:00:00+00:00"
    d2 = await ls.user_to_dict(_user(totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP", must_change_password=True,
                                     auth_source="oidc"), db)
    assert d2["totp_unreadable"] is False and d2["must_change_password"] is True and d2["auth_source"] == "oidc"


# ------------------------------------------------------------------ request_context (B.3)
def test_token_scope_properties():
    s = rc.TokenScope(1, "ci", "dnsmgr_usr_abcd", frozenset({"example.com."}), "read", False)
    assert s.read_only is True and s.zone_limited is True
    assert s.covers_zone("example.com.") and not s.covers_zone("other.org.")
    full = rc.TokenScope(2, "all", "dnsmgr_usr_efgh", None, "manage", True)
    assert full.read_only is False and full.zone_limited is False and full.covers_zone("x.")
    weird = rc.TokenScope(3, "w", "p", None, "MANAGE", False)
    assert weird.read_only is True  # fail-safe


def test_audit_auth_context_and_reset():
    def run():
        assert rc.get_auth_via() is None and rc.audit_auth_context() is None
        rc.auth_via_ctx.set("session")
        assert rc.audit_auth_context() is None
        rc.auth_via_ctx.set("panel_token")
        rc.current_token_scope.set(rc.TokenScope(5, "deploy", "dnsmgr_usr_1234", None, "manage", False))
        assert rc.audit_auth_context() == {"via": "panel_token", "token_id": 5, "token_name": "deploy",
                                           "token_prefix": "dnsmgr_usr_1234"}
        rc.auth_via_ctx.set("dyndns_token")
        rc.current_token_scope.set(None)
        assert rc.audit_auth_context() == {"via": "dyndns_token"}
        rc.actor_username_ctx.set("bob")
        rc.client_ip_ctx.set("192.0.2.1")
        assert rc.get_actor_username() == "bob" and rc.get_client_ip_ctx() == "192.0.2.1"
        rc.reset_request_context()
        assert (rc.get_auth_via(), rc.get_token_scope(), rc.get_actor_username(), rc.get_client_ip_ctx()) == (
            None, None, None, None)

    contextvars.copy_context().run(run)
    assert rc.get_auth_via() is None  # kein Leck in den aeusseren Kontext


def test_request_context_has_no_app_imports():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rc))
    mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + \
           [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in mods if m and m.startswith("app")]
