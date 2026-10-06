"""Token-Verwaltung WS-F14-APP (F14 3.2-3.8) ohne DB: PUT, Admin-Sicht, Serializer-Felder, Session-Pflicht.

Die Handler werden ueberwiegend direkt mit ``FakeSession`` aufgerufen (wie ``test_panel_token_service.py``);
die HTTP-Faelle pruefen Routing, Session-Pflicht und Admin-Gate ueber den echten Router.
"""
import hashlib
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from authfakes import PLAIN, FakeSession, bearer, build_app, make_token, make_user
from app.core.auth import SESSION_REQUIRED_DETAIL, create_access_token
from app.core.timeutil import iso_utc, utcnow
from app.models.models import AuditLog, PanelToken
from app.routers import panel_tokens
from app.routers.panel_tokens import (
    PanelTokenCreate,
    PanelTokenUpdate,
    list_panel_tokens,
    list_user_panel_tokens,
    revoke_all_user_panel_tokens,
    revoke_user_panel_token,
    update_panel_token,
)
from app.schemas import panel_tokens as schemas


def _audits(session):
    return [o for o in session.added if isinstance(o, AuditLog)]


def _upd(**kw) -> PanelTokenUpdate:
    return PanelTokenUpdate(**kw)


# ---------------------------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------------------------
def test_schemas_live_in_schemas_module_and_are_reexported():
    assert panel_tokens.PanelTokenCreate is schemas.PanelTokenCreate
    assert panel_tokens.PanelTokenUpdate is schemas.PanelTokenUpdate


def test_update_schema_distinguishes_missing_and_null():
    empty = PanelTokenUpdate()
    assert not empty.given("scope_zones") and not empty.given("expires_in_days")
    nulls = PanelTokenUpdate(scope_zones=None, expires_in_days=None)
    assert nulls.given("scope_zones") and nulls.given("expires_in_days")
    assert nulls.scope_zones is None and nulls.expires_in_days is None
    for bad in ({"name": ""}, {"name": "x" * 101}, {"expires_in_days": 0}, {"expires_in_days": 3651},
                {"permission": "write"}, {"scope_zones": ["a." * 200]}, {"scope_zones": ["a."] * 501}):
        with pytest.raises(ValidationError):
            PanelTokenUpdate(**bad)


def test_create_schema_defaults_stay_api_compatible():
    d = PanelTokenCreate(name="ci")
    assert (d.scope_zones, d.permission, d.expires_in_days, d.allow_admin) == (None, "manage", None, False)


# ---------------------------------------------------------------------------------------------
# GET /auth/me/panel-tokens – Serializer mit Scope, Ablauf, zuletzt benutzt
# ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_returns_scope_expiry_last_used_and_limits():
    user = make_user(role="user", uid=5, username="bob")
    now = utcnow()
    rows = [
        make_token(id=9, user_id=5, scope_zones=["allowed.example.", "weg.example."], permission="read",
                   expires_at=now + timedelta(days=3), last_used_at=now - timedelta(minutes=5),
                   last_used_ip="192.0.2.7", created_at=now - timedelta(days=1)),
        make_token(id=8, user_id=5, scope_zones=None, is_active=False, created_at=now - timedelta(days=2)),
        make_token(id=7, user_id=5, scope_zones=None, expires_at=now - timedelta(minutes=1),
                   created_at=now - timedelta(days=3)),
    ]
    session = FakeSession(user_row=user, zone_access=[("allowed.example.", "manage")],
                          extra={PanelToken: rows})
    res = await list_panel_tokens(db=session, current_user=user)
    assert res["max_tokens"] == 50 and res["max_expiry_days"] == 3650
    first, paused, expired = res["tokens"]
    assert first["scope_zones"] == ["allowed.example.", "weg.example."]
    assert first["inaccessible_zones"] == ["weg.example."]
    assert first["permission"] == "read" and first["status"] == "active"
    assert first["last_used_ip"] == "192.0.2.7" and first["last_used_at"] == iso_utc(rows[0].last_used_at)
    assert first["expires_at"] == iso_utc(rows[0].expires_at)
    assert paused["status"] == "paused" and expired["status"] == "expired"
    for tok in res["tokens"]:
        assert "token_hash" not in tok and tok["admin_effective"] is False


# ---------------------------------------------------------------------------------------------
# PUT /auth/me/panel-tokens/{id}
# ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_rename_pause_and_permission_with_audit():
    user = make_user()
    tok = make_token(scope_zones=None)
    session = FakeSession(token_row=tok, user_row=user)
    res = await update_panel_token(7, _upd(name="  deploy  ", is_active=False, permission="read"),
                                   db=session, current_user=user)
    assert res["message"] == "Token gespeichert"
    assert (tok.name, tok.is_active, tok.permission) == ("deploy", False, "read")
    assert res["token"]["status"] == "paused" and res["token"]["name"] == "deploy"
    (audit,) = _audits(session)
    assert audit.action == "PANEL_TOKEN_UPDATE" and audit.user_id == 1 and audit.resource_name == "admin"
    assert audit.details["token_id"] == 7 and audit.details["name"] == "ci"
    assert audit.details["prefix"] == "dnsmgr_usr_test…"
    assert audit.details["changed"] == {
        "name": {"from": "ci", "to": "deploy"},
        "is_active": {"from": True, "to": False},
        "permission": {"from": "manage", "to": "read"},
    }
    text = str(audit.details) + str(res)
    assert PLAIN not in text and tok.token_hash not in text


@pytest.mark.asyncio
async def test_update_without_changes_returns_message_without_audit():
    user = make_user()
    tok = make_token()
    session = FakeSession(token_row=tok, user_row=user)
    res = await update_panel_token(7, _upd(name="ci", permission="manage", scope_zones=["ALLOWED.example"]),
                                   db=session, current_user=user)
    assert res["message"] == "Keine Änderungen" and res["token"]["id"] == 7
    assert _audits(session) == [] and session.flushes == 0


@pytest.mark.asyncio
async def test_update_scope_and_expiry_missing_vs_null():
    user = make_user()
    old_exp = utcnow() + timedelta(days=10)
    tok = make_token(scope_zones=["allowed.example."], expires_at=old_exp)
    session = FakeSession(token_row=tok, user_row=user)
    # weggelassen -> unveraendert
    await update_panel_token(7, _upd(name="x"), db=session, current_user=user)
    assert tok.scope_zones == ["allowed.example."] and tok.expires_at == old_exp
    # explizit null -> alle Zonen / kein Ablauf
    res = await update_panel_token(7, _upd(scope_zones=None, expires_in_days=None), db=session, current_user=user)
    assert tok.scope_zones is None and tok.expires_at is None
    assert res["token"]["scope_zones"] is None and res["token"]["expires_at"] is None
    changed = _audits(session)[-1].details["changed"]
    assert changed["scope_zones"] == {"from": ["allowed.example."], "to": None}
    assert changed["expires_at"] == {"from": iso_utc(old_exp), "to": None}
    # neue Laufzeit zaehlt ab jetzt; Scope wird normalisiert
    before = utcnow()
    await update_panel_token(7, _upd(expires_in_days=30, scope_zones=["B.example", "a.example."]),
                             db=session, current_user=user)
    assert tok.scope_zones == ["a.example.", "b.example."]
    assert before + timedelta(days=30) - timedelta(seconds=5) <= tok.expires_at <= utcnow() + timedelta(days=30)


@pytest.mark.asyncio
async def test_update_resume_keeps_expired_status():
    user = make_user()
    tok = make_token(is_active=False, expires_at=utcnow() - timedelta(days=1))
    res = await update_panel_token(7, _upd(is_active=True), db=FakeSession(token_row=tok, user_row=user),
                                   current_user=user)
    assert tok.is_active is True and res["token"]["status"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("tok_kw,body,status,detail", [
    ({"scope_zones": None, "allow_admin": True}, {"scope_zones": ["a.example"]}, 400,
     "Admin-Funktionen sind nur für Tokens ohne Zonen-Beschränkung möglich"),
    ({"scope_zones": ["a.example."]}, {"allow_admin": True}, 400,
     "Admin-Funktionen sind nur für Tokens ohne Zonen-Beschränkung möglich"),
    ({}, {"name": "   "}, 400, "Bezeichnung darf nicht leer sein"),
    ({}, {"scope_zones": ["a b"]}, 400, "Ungültiger Zonenname: a b"),
    ({}, {"scope_zones": []}, 400,
     "Mindestens eine Zone angeben – oder scope_zones weglassen bzw. null senden (= alle Zonen)"),
])
async def test_update_validation_admin(tok_kw, body, status, detail):
    user = make_user()
    tok = make_token(**tok_kw)
    snapshot = (tok.name, tok.scope_zones, tok.allow_admin)
    session = FakeSession(token_row=tok, user_row=user)
    with pytest.raises(HTTPException) as e:
        await update_panel_token(7, _upd(**body), db=session, current_user=user)
    assert (e.value.status_code, e.value.detail) == (status, detail)
    assert (tok.name, tok.scope_zones, tok.allow_admin) == snapshot and _audits(session) == []


@pytest.mark.asyncio
async def test_update_admin_switches_to_all_zones_with_allow_admin_in_one_request():
    user = make_user()
    tok = make_token(scope_zones=["a.example."])
    res = await update_panel_token(7, _upd(scope_zones=None, allow_admin=True),
                                   db=FakeSession(token_row=tok, user_row=user), current_user=user)
    assert tok.allow_admin is True and tok.scope_zones is None
    assert res["token"]["admin_effective"] is True


@pytest.mark.asyncio
async def test_update_non_admin_rules():
    bob = make_user(role="user", uid=5, username="bob")
    zones = [("allowed.example.", "manage"), ("mine.example.", "read")]
    # Admin-Freigabe nur durch Administratoren
    tok = make_token(user_id=5, scope_zones=None)
    with pytest.raises(HTTPException) as e:
        await update_panel_token(7, _upd(allow_admin=True), db=FakeSession(token_row=tok, user_row=bob,
                                                                           zone_access=zones), current_user=bob)
    assert (e.value.status_code, e.value.detail) == (403, "Admin-Funktionen kann nur ein Administrator freigeben")
    # fremde Zone hinzufuegen -> 403 (Text nennt nur die eigene Eingabe)
    tok = make_token(user_id=5, scope_zones=["allowed.example.", "weg.example."])
    with pytest.raises(HTTPException) as e:
        await update_panel_token(7, _upd(scope_zones=["allowed.example.", "fremd.example"]),
                                 db=FakeSession(token_row=tok, user_row=bob, zone_access=zones), current_user=bob)
    assert e.value.status_code == 403 and "„fremd.example.“" in e.value.detail
    # unveraenderte Altzone ohne Zugriff (weg.example.) bleibt erlaubt; eigene Zone dazu ok
    session = FakeSession(token_row=tok, user_row=bob, zone_access=zones)
    res = await update_panel_token(7, _upd(scope_zones=["weg.example.", "allowed.example.", "mine.example."]),
                                   db=session, current_user=bob)
    assert tok.scope_zones == ["allowed.example.", "mine.example.", "weg.example."]
    assert res["token"]["inaccessible_zones"] == ["weg.example."]
    # allow_admin=False darf auch ein Nicht-Admin setzen (Altlast eines degradierten Admins entfernen)
    tok = make_token(user_id=5, scope_zones=None, allow_admin=True)
    res = await update_panel_token(7, _upd(allow_admin=False), db=FakeSession(token_row=tok, user_row=bob),
                                   current_user=bob)
    assert tok.allow_admin is False and res["message"] == "Token gespeichert"


@pytest.mark.asyncio
async def test_update_unknown_or_revoked_token_404():
    user = make_user()
    with pytest.raises(HTTPException) as e:
        await update_panel_token(99, _upd(name="x"), db=FakeSession(user_row=user), current_user=user)
    assert (e.value.status_code, e.value.detail) == (404, "Token nicht gefunden")
    session = FakeSession(user_row=user)
    with pytest.raises(HTTPException):
        await update_panel_token(99, _upd(name="x"), db=session, current_user=user)
    sql = str(session.executed[0])
    assert "revoked_at IS NULL" in sql and "user_id" in sql


# ---------------------------------------------------------------------------------------------
# Admin-Sicht
# ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_lists_tokens_of_other_user():
    admin = make_user()
    bob = make_user(role="user", uid=5, username="bob")
    rows = [make_token(id=3, user_id=5, scope_zones=["allowed.example.", "weg.example."], allow_admin=True)]
    session = FakeSession(user_row=bob, zone_access=[("allowed.example.", "manage")], extra={PanelToken: rows})
    res = await list_user_panel_tokens(5, db=session, admin=admin)
    assert res["user_id"] == 5 and res["username"] == "bob"
    (tok,) = res["tokens"]
    # Anzeige bezieht sich auf den BESITZER (Nicht-Admin), nicht auf den aufrufenden Admin
    assert tok["inaccessible_zones"] == ["weg.example."] and tok["admin_effective"] is False
    assert "token_hash" not in tok and _audits(session) == []


@pytest.mark.asyncio
async def test_admin_view_unknown_user_404():
    with pytest.raises(HTTPException) as e:
        await list_user_panel_tokens(77, db=FakeSession(), admin=make_user())
    assert (e.value.status_code, e.value.detail) == (404, "Benutzer nicht gefunden")
    with pytest.raises(HTTPException) as e:
        await revoke_all_user_panel_tokens(77, db=FakeSession(), admin=make_user())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_revokes_single_token_with_audit():
    admin = make_user()
    bob = make_user(role="user", uid=5, username="bob")
    tok = make_token(id=3, user_id=5)
    session = FakeSession(token_row=tok, user_row=bob)
    assert await revoke_user_panel_token(5, 3, db=session, admin=admin) == {"message": "Token widerrufen"}
    assert tok.revoked_at is not None and tok.is_active is False
    (audit,) = _audits(session)
    assert audit.action == "PANEL_TOKEN_ADMIN_REVOKE" and audit.user_id == admin.id
    assert audit.resource_type == "user" and audit.resource_name == "bob"
    assert audit.details == {"target_user_id": 5, "count": 1, "token_ids": [3], "names": ["ci"],
                             "prefixes": ["dnsmgr_usr_test…"]}
    with pytest.raises(HTTPException) as e:
        await revoke_user_panel_token(5, 4, db=FakeSession(user_row=bob), admin=admin)
    assert (e.value.status_code, e.value.detail) == (404, "Token nicht gefunden")


@pytest.mark.asyncio
async def test_admin_revokes_all_tokens():
    admin = make_user()
    bob = make_user(role="user", uid=5, username="bob")
    rows = [make_token(id=3, user_id=5, name="a"), make_token(id=4, user_id=5, name="b", is_active=False)]
    session = FakeSession(user_row=bob, extra={PanelToken: rows})
    res = await revoke_all_user_panel_tokens(5, db=session, admin=admin)
    assert res == {"message": "2 Token widerrufen", "revoked": 2}
    assert all(r.revoked_at is not None for r in rows)
    (audit,) = _audits(session)
    assert audit.details["count"] == 2 and audit.details["token_ids"] == [3, 4]
    assert audit.details["names"] == ["a", "b"] and audit.user_id == admin.id
    # nichts offen -> kein Audit
    empty = FakeSession(user_row=bob)
    assert await revoke_all_user_panel_tokens(5, db=empty, admin=admin) == {"message": "0 Token widerrufen",
                                                                             "revoked": 0}
    assert _audits(empty) == []


# ---------------------------------------------------------------------------------------------
# HTTP: Routing, Session-Pflicht, Admin-Gate
# ---------------------------------------------------------------------------------------------
NEW_ROUTES = [
    ("PUT", "/api/v1/auth/me/panel-tokens/7", {"name": "x"}),
    ("GET", "/api/v1/auth/users/5/panel-tokens", None),
    ("DELETE", "/api/v1/auth/users/5/panel-tokens/7", None),
    ("DELETE", "/api/v1/auth/users/5/panel-tokens", None),
]


def _client(session):
    return TestClient(build_app(session, panel_tokens), raise_server_exceptions=False)


def _session_headers(user):
    return {"Authorization": f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"}


@pytest.mark.parametrize("method,path,body", NEW_ROUTES)
def test_new_routes_reject_panel_token(method, path, body):
    session = FakeSession(token_row=make_token(scope_zones=None, allow_admin=True), user_row=make_user())
    r = _client(session).request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403 and r.json()["detail"] == SESSION_REQUIRED_DETAIL
    assert session.added == []


@pytest.mark.parametrize("method,path,body", NEW_ROUTES[1:])
def test_admin_routes_reject_non_admin_session(method, path, body):
    bob = make_user(role="user", uid=5, username="bob")
    session = FakeSession(token_row=make_token(user_id=5), user_row=bob)
    r = _client(session).request(method, path, headers=_session_headers(bob), json=body)
    assert r.status_code == 403
    assert session.added == []


def test_put_via_session_http_roundtrip():
    user = make_user()
    tok = make_token(scope_zones=["allowed.example."])
    session = FakeSession(token_row=tok, user_row=user)
    c = _client(session)
    r = c.put("/api/v1/auth/me/panel-tokens/7", headers=_session_headers(user),
              json={"scope_zones": None, "allow_admin": True, "expires_in_days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]["scope_zones"] is None and body["token"]["allow_admin"] is True
    assert body["token"]["expires_at"] is not None
    assert c.put("/api/v1/auth/me/panel-tokens/7", headers=_session_headers(user),
                 json={"permission": "write"}).status_code == 422
    assert c.put("/api/v1/auth/me/panel-tokens/abc", headers=_session_headers(user), json={}).status_code == 422


def test_admin_list_via_session_http():
    admin = make_user()
    session = FakeSession(token_row=make_token(), user_row=admin)
    r = _client(session).get("/api/v1/auth/users/1/panel-tokens", headers=_session_headers(admin))
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "admin" and r.json()["tokens"][0]["id"] == 7


def test_all_panel_token_routes_are_session_only():
    """Jede Route mit ``panel-tokens`` im Pfad haengt direkt an einer Session-Dependency (F14 E7)."""
    import route_policy as rp
    from app.main import app

    routes = [r for r in rp.iter_app_routes(app) if "/panel-tokens" in r.path]
    keys = {r.key for r in routes}
    assert {("PUT", "/api/v1/auth/me/panel-tokens/{token_id}"),
            ("GET", "/api/v1/auth/users/{user_id}/panel-tokens"),
            ("DELETE", "/api/v1/auth/users/{user_id}/panel-tokens/{token_id}"),
            ("DELETE", "/api/v1/auth/users/{user_id}/panel-tokens")} <= keys
    assert len(routes) == 7
    for r in routes:
        assert rp.route_category_problem(r, "session") is None, r.key


def test_audit_never_contains_hash_on_update():
    user = make_user()
    tok = make_token(scope_zones=None)
    session = FakeSession(token_row=tok, user_row=user)
    import asyncio

    asyncio.run(update_panel_token(7, _upd(name="neu"), db=session, current_user=user))
    (audit,) = _audits(session)
    assert hashlib.sha256(PLAIN.encode()).hexdigest() not in str(audit.details)
