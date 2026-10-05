"""Gate "Passwortwechsel erforderlich" in ``get_current_user`` (F2/F3 3.2.11, 5.6; F3 9.1 Nr. 1–4)."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from authfakes import FakeSession, bearer, build_app, make_token, make_user
from app.core import auth as core_auth
from app.core.auth import (
    PASSWORD_CHANGE_HEADER,
    PASSWORD_CHANGE_REQUIRED_DETAIL,
    _PASSWORD_CHANGE_ALLOWED,
    _password_change_allowed,
    create_access_token,
    get_current_user,
)


def _request(method: str, path: str, *, root_path: str = "", headers=None) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": b"",
        "client": ("192.0.2.10", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


# --- Nr. 1 -----------------------------------------------------------------------------------
def test_password_change_gate_paths():
    for method, path in _PASSWORD_CHANGE_ALLOWED:
        assert _password_change_allowed(_request(method, path)), (method, path)
        assert _password_change_allowed(_request(method, path + "/")), (method, path)
    for method, path in (("GET", "/api/v1/servers"), ("GET", "/api/v1/records/x/auth/me"),
                         ("PUT", "/api/v1/auth/me"), ("POST", "/api/v1/auth/me/password"),
                         ("GET", "/api/v1/auth/me/panel-tokens")):
        assert not _password_change_allowed(_request(method, path)), (method, path)
    assert _password_change_allowed(_request("GET", "/pdns/api/v1/auth/me", root_path="/pdns"))
    assert not _password_change_allowed(_request("GET", "/pdns/api/v1/servers", root_path="/pdns"))


# --- Nr. 2 -----------------------------------------------------------------------------------
def test_gate_paths_exist_in_app():
    import route_policy as rp
    from app.main import app

    keys = {r.key for r in rp.iter_app_routes(app)}
    missing = [k for k in _PASSWORD_CHANGE_ALLOWED if k not in keys]
    assert not missing, missing


# --- Nr. 3 -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_current_user_blocks_flagged_session():
    u = make_user(role="user", uid=3, username="neu")
    u.must_change_password = True
    token = create_access_token(data={"sub": "3"}, user=u)
    db = FakeSession(user_row=u)
    with pytest.raises(HTTPException) as e:
        await get_current_user(_request("GET", "/api/v1/servers"), db=db, token=token)
    assert e.value.status_code == 403
    assert e.value.detail == PASSWORD_CHANGE_REQUIRED_DETAIL
    assert e.value.headers == {PASSWORD_CHANGE_HEADER: "1"}
    assert await get_current_user(_request("GET", "/api/v1/auth/me"), db=db, token=token) is u
    assert await get_current_user(_request("PUT", "/api/v1/auth/me/password"), db=db, token=token) is u
    assert await get_current_user(_request("POST", "/api/v1/auth/logout"), db=db, token=token) is u


@pytest.mark.asyncio
async def test_get_current_user_unflagged_session_passes_and_sets_auth_time():
    u = make_user(role="user", uid=3, username="neu")
    token = create_access_token(data={"sub": "3"}, user=u)
    req = _request("GET", "/api/v1/servers")
    assert await get_current_user(req, db=FakeSession(user_row=u), token=token) is u
    assert req.state.auth_via == "session"
    assert isinstance(req.state.auth_time, (int, float)) and req.state.auth_time > 0
    assert core_auth.actor_username_ctx.get() == "neu" and core_auth.client_ip_ctx.get() == "192.0.2.10"


# --- Nr. 4 -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_current_user_panel_token_ignores_flag():
    from authfakes import PLAIN

    u = make_user()
    u.must_change_password = True
    db = FakeSession(token_row=make_token(scope_zones=None), user_row=u)
    req = _request("GET", "/api/v1/servers", headers=bearer())
    assert await get_current_user(req, db=db, token=PLAIN) is u
    assert req.state.auth_via == "panel_token" and req.state.auth_time is None


def test_gate_over_http_with_header():
    from app.routers import auth, servers

    u = make_user(role="user", uid=3, username="neu")
    u.must_change_password = True
    session = FakeSession(user_row=u)
    c = TestClient(build_app(session, auth, servers), raise_server_exceptions=False)
    jwt = create_access_token(data={"sub": "3"}, user=u)
    h = {"Authorization": f"Bearer {jwt}"}
    r = c.get("/api/v1/servers", headers=h)
    assert r.status_code == 403 and r.headers.get(PASSWORD_CHANGE_HEADER) == "1"
    me = c.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    # Passwortwechsel loest das Gate
    from app.core.auth import hash_password

    u.hashed_password = hash_password("altes-passwort")
    jwt = create_access_token(data={"sub": "3"}, user=u)
    h = {"Authorization": f"Bearer {jwt}"}
    r = c.put("/api/v1/auth/me/password", headers=h,
              json={"current_password": "altes-passwort", "new_password": "neues-passwort-1"})
    assert r.status_code == 200, r.text
    assert u.must_change_password is False
