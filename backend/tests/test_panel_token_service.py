"""Unit-Tests ``services/panel_token.py`` und Token-Bausteine aus ``core/auth.py`` (F14 9.4 Nr. 17–24, ohne DB)."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from authfakes import FakeSession, make_token, make_user
from app.core.auth import scope_from_token
from app.core.request_context import TokenScope, auth_via_ctx, current_token_scope
from app.core.timeutil import utcnow
from app.models.models import AuditLog, PanelToken
from app.routers.panel_tokens import PanelTokenCreate, create_panel_token, delete_panel_token
from app.services import panel_token as ptk


# --- Nr. 17 ----------------------------------------------------------------------------------
def test_normalize_scope_zones():
    assert ptk.normalize_scope_zones(None) is None
    assert ptk.normalize_scope_zones([" B.example ", "a.example.", "A.EXAMPLE", "b.example."]) == [
        "a.example.", "b.example.",
    ]
    assert ptk.normalize_scope_zones(["bücher.example"]) == ["xn--bcher-kva.example."]
    assert ptk.normalize_scope_zones(["_acme.example."]) == ["_acme.example."]
    for bad in ("foo bar", "*.x.de", "a/b.de", "x" * 300 + ".de", "", "-a.de", "a..de"):
        with pytest.raises(ValueError, match="Ungültiger Zonenname"):
            ptk.normalize_scope_zones([bad])
    with pytest.raises(ValueError, match="Mindestens eine Zone"):
        ptk.normalize_scope_zones([])
    with pytest.raises(ValueError, match="Maximal 500 Zonen"):
        ptk.normalize_scope_zones([f"z{i}.example" for i in range(501)])
    assert len(ptk.normalize_scope_zones([f"z{i}.example" for i in range(500)])) == 500


# --- Nr. 18 ----------------------------------------------------------------------------------
def test_token_status():
    now = utcnow()
    assert ptk.token_status(make_token(), now) == "active"
    assert ptk.token_status(make_token(is_active=False), now) == "paused"
    assert ptk.token_status(make_token(expires_at=now - timedelta(seconds=1)), now) == "expired"
    assert ptk.token_status(make_token(expires_at=now), now) == "expired"
    assert ptk.token_status(make_token(expires_at=now + timedelta(days=1)), now) == "active"
    assert ptk.token_status(make_token(revoked_at=now), now) == "revoked"
    # pausiert vor abgelaufen, widerrufen vor allem
    assert ptk.token_status(make_token(is_active=False, expires_at=now - timedelta(days=1)), now) == "paused"
    assert ptk.token_status(make_token(is_active=False, revoked_at=now), now) == "revoked"
    # tz-aware Werte werden korrekt verglichen
    aware = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert ptk.token_status(make_token(expires_at=aware), now) == "expired"


# --- Nr. 19 ----------------------------------------------------------------------------------
def test_serialize_token():
    now = utcnow()
    row = make_token(
        scope_zones=["b.example.", "a.example."], created_at=datetime(2026, 1, 2, 3, 4, 5),
        last_used_at=datetime(2026, 2, 1), last_used_ip="198.51.100.4", expires_at=now + timedelta(days=3),
    )
    out = ptk.serialize_token(row, owner_role="user", owner_zones={"a.example."}, now=now)
    assert out["scope_zones"] == ["a.example.", "b.example."]
    assert out["inaccessible_zones"] == ["b.example."]
    assert out["created_at"] == "2026-01-02T03:04:05+00:00" and out["last_used_ip"] == "198.51.100.4"
    assert out["status"] == "active" and out["permission"] == "manage"
    assert out["admin_effective"] is False and out["allow_admin"] is False
    assert "token_hash" not in out and row.token_hash not in str(out)
    adm = ptk.serialize_token(make_token(scope_zones=None, allow_admin=True), owner_role="admin",
                              owner_zones=None, now=now)
    assert adm["admin_effective"] is True and adm["scope_zones"] is None and adm["inaccessible_zones"] == []
    # Admin-Besitzer: nie inaccessible_zones; unbekannte permission -> read
    adm2 = ptk.serialize_token(make_token(permission="write"), owner_role="admin", owner_zones=set(), now=now)
    assert adm2["inaccessible_zones"] == [] and adm2["permission"] == "read"
    assert set(out) == {
        "id", "name", "token_prefix", "created_at", "last_used_at", "last_used_ip", "is_active", "status",
        "expires_at", "scope_zones", "permission", "allow_admin", "admin_effective", "inaccessible_zones",
    }


# --- Nr. 20 ----------------------------------------------------------------------------------
def test_touch_last_used_throttle():
    now = utcnow()
    row = make_token()
    assert ptk.touch_last_used(row, "192.0.2.1", now) is True
    assert row.last_used_at == now and row.last_used_ip == "192.0.2.1"
    assert ptk.touch_last_used(row, "192.0.2.1", now + timedelta(seconds=59)) is False
    assert row.last_used_at == now
    assert ptk.touch_last_used(row, "192.0.2.2", now + timedelta(seconds=10)) is True  # IP-Wechsel
    assert ptk.touch_last_used(row, "192.0.2.2", now + timedelta(seconds=71)) is True  # >= 60 s
    assert ptk.touch_last_used(row, None, now + timedelta(seconds=75)) is False
    long_ip = "f" * 80
    assert ptk.touch_last_used(row, long_ip, now + timedelta(seconds=80)) is True
    assert row.last_used_ip == "f" * 64


# --- Nr. 21 ----------------------------------------------------------------------------------
def test_scope_from_token_defensive():
    s = scope_from_token(make_token(permission="WRITE"))
    assert s.permission == "read" and s.read_only
    s = scope_from_token(make_token(scope_zones="a.example."))
    assert s.zones == frozenset() and s.zone_limited and not s.covers_zone("a.example.")
    s = scope_from_token(make_token(scope_zones=None, permission=None))
    assert s.zones is None and not s.zone_limited and s.permission == "manage"
    s = scope_from_token(make_token(scope_zones=["Allowed.Example", ""], allow_admin=1))
    assert s.zones == frozenset({"allowed.example."}) and s.allow_admin is True
    assert (s.token_id, s.name, s.token_prefix) == (7, "ci", "dnsmgr_usr_test…")


# --- Nr. 22 ----------------------------------------------------------------------------------
def test_audit_log_constructor_adds_auth_context():
    scope = TokenScope(token_id=7, name="ci", token_prefix="dnsmgr_usr_test…", zones=None,
                       permission="manage", allow_admin=False)
    t1 = auth_via_ctx.set("panel_token")
    t2 = current_token_scope.set(scope)
    try:
        expected = {"via": "panel_token", "token_id": 7, "token_name": "ci", "token_prefix": "dnsmgr_usr_test…"}
        assert AuditLog(action="X", resource_type="y").details == {"auth": expected}
        assert AuditLog(action="X", resource_type="y", details={"a": 1}).details == {"a": 1, "auth": expected}
        assert AuditLog(action="X", resource_type="y", details={"auth": "eigen"}).details == {"auth": "eigen"}
    finally:
        current_token_scope.reset(t2)
        auth_via_ctx.reset(t1)
    t3 = auth_via_ctx.set("session")
    try:
        assert AuditLog(action="X", resource_type="y", details={"a": 1}).details == {"a": 1}
    finally:
        auth_via_ctx.reset(t3)


# --- Nr. 23 ----------------------------------------------------------------------------------
def test_panel_token_create_schema():
    for bad in ({"name": ""}, {"name": "x" * 101}, {"name": "x", "expires_in_days": 0},
                {"name": "x", "expires_in_days": 3651}, {"name": "x", "permission": "write"},
                {"name": "x", "scope_zones": ["a." * 200]}):
        with pytest.raises(ValidationError):
            PanelTokenCreate(**bad)
    d = PanelTokenCreate(name="ci")
    assert (d.scope_zones, d.permission, d.expires_in_days, d.allow_admin) == (None, "manage", None, False)


# --- Nr. 24: Handler direkt – nie Klartext/Hash im Audit -----------------------------------------
def _audits(session):
    return [o for o in session.added if isinstance(o, AuditLog)]


@pytest.mark.asyncio
async def test_create_handler_audit_never_contains_plaintext():
    user = make_user(role="user", uid=5, username="bob")
    session = FakeSession(user_row=user, zone_access=[("allowed.example.", "manage")])
    data = PanelTokenCreate(name=" ci ", scope_zones=["Allowed.Example"], permission="read", expires_in_days=30)
    res = await create_panel_token(data, db=session, current_user=user)
    plain = res["plaintext_token"]
    assert plain.startswith("dnsmgr_usr_") and len(plain) > 40
    row = next(o for o in session.added if isinstance(o, PanelToken))
    assert row.token_hash == hashlib.sha256(plain.encode()).hexdigest()
    assert row.scope_zones == ["allowed.example."] and row.permission == "read" and row.name == "ci"
    assert row.expires_at is not None and row.allow_admin is False
    (audit,) = _audits(session)
    assert audit.action == "PANEL_TOKEN_CREATE" and audit.user_id == 5
    text = str(audit.details)
    assert plain not in text and row.token_hash not in text
    assert audit.details["scope_zones"] == ["allowed.example."] and audit.details["permission"] == "read"
    assert res["token"]["scope_zones"] == ["allowed.example."] and res["token"]["status"] == "active"
    assert plain not in str(res["token"])


@pytest.mark.asyncio
@pytest.mark.parametrize("user_kw,body,status,detail", [
    ({"role": "user", "uid": 5, "username": "bob"}, {"name": "x", "allow_admin": True}, 403,
     "Admin-Funktionen kann nur ein Administrator freigeben"),
    ({}, {"name": "x", "allow_admin": True, "scope_zones": ["a.example"]}, 400,
     "Admin-Funktionen sind nur für Tokens ohne Zonen-Beschränkung möglich"),
    ({"role": "user", "uid": 5, "username": "bob"}, {"name": "x", "scope_zones": ["fremd.example"]}, 403,
     "Keine Berechtigung für Zone „fremd.example.“ – ein Token darf nur eigene Zonen enthalten"),
    ({}, {"name": "x", "scope_zones": ["a b"]}, 400, "Ungültiger Zonenname: a b"),
    ({}, {"name": "   "}, 400, "Bezeichnung darf nicht leer sein"),
])
async def test_create_handler_validation(user_kw, body, status, detail):
    from fastapi import HTTPException

    user = make_user(**user_kw)
    session = FakeSession(user_row=user, zone_access=[("allowed.example.", "manage")])
    with pytest.raises(HTTPException) as e:
        await create_panel_token(PanelTokenCreate(**body), db=session, current_user=user)
    assert (e.value.status_code, e.value.detail) == (status, detail)
    assert session.added == []


@pytest.mark.asyncio
async def test_create_handler_admin_with_allow_admin_and_limit(monkeypatch):
    admin = make_user()
    session = FakeSession(user_row=admin)
    res = await create_panel_token(PanelTokenCreate(name="ci", allow_admin=True), db=session, current_user=admin)
    assert res["token"]["allow_admin"] is True and res["token"]["admin_effective"] is True
    assert res["token"]["scope_zones"] is None

    async def _full(db, uid):
        return ptk.MAX_TOKENS_PER_USER

    monkeypatch.setattr(ptk, "count_open_tokens", _full)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await create_panel_token(PanelTokenCreate(name="zu viel"), db=FakeSession(user_row=admin), current_user=admin)
    assert e.value.status_code == 400 and "Maximal 50 API-Tokens" in e.value.detail


@pytest.mark.asyncio
async def test_delete_handler_revokes_and_audits():
    from fastapi import HTTPException

    user = make_user()
    tok = make_token()
    session = FakeSession(token_row=tok, user_row=user)
    assert await delete_panel_token(7, db=session, current_user=user) == {"message": "Token widerrufen"}
    assert tok.is_active is False and tok.revoked_at is not None
    (audit,) = _audits(session)
    assert audit.action == "PANEL_TOKEN_DELETE"
    assert audit.details == {"token_id": 7, "name": "ci", "prefix": "dnsmgr_usr_test…"}
    with pytest.raises(HTTPException) as e:
        await delete_panel_token(8, db=FakeSession(user_row=user), current_user=user)
    assert e.value.status_code == 404 and e.value.detail == "Token nicht gefunden"


# --- Service-Funktionen mit Fake-Session ---------------------------------------------------------
@pytest.mark.asyncio
async def test_revoke_all_for_user_and_remove_zone_from_scopes():
    a = make_token(id=1, scope_zones=["a.example.", "b.example."])
    b = make_token(id=2, scope_zones=["c.example."])
    c = make_token(id=3, scope_zones=None)
    d = make_token(id=4, scope_zones=["A.Example"])
    session = FakeSession(extra={PanelToken: [a, b, c, d]})
    assert await ptk.remove_zone_from_scopes(session, "A.EXAMPLE") == 2
    assert a.scope_zones == ["b.example."] and d.scope_zones == [] and b.scope_zones == ["c.example."]
    assert c.scope_zones is None
    assert await ptk.remove_zone_from_scopes(session, "") == 0

    rows = await ptk.revoke_all_for_user(session, 1)
    assert len(rows) == 4 and all(r.revoked_at is not None and r.is_active is False for r in rows)
    assert await ptk.revoke_all_for_user(FakeSession(), 1) == []


@pytest.mark.asyncio
async def test_count_active_by_user_query_shape():
    session = FakeSession(extra={PanelToken: [(1, 2), (5, 1)]})
    assert await ptk.count_active_by_user(session) == {1: 2, 5: 1}
    sql = str(session.executed[0])
    assert "revoked_at IS NULL" in sql and "is_active" in sql and "expires_at" in sql and "GROUP BY" in sql


def test_create_token_rejects_unknown_permission():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(ptk.create_token(FakeSession(), 1, "x", permission="write"))


def test_scope_object_semantics():
    s = TokenScope(token_id=1, name="n", token_prefix="p", zones=frozenset({"a."}), permission="manage",
                   allow_admin=False)
    assert s.covers_zone("a.") and not s.covers_zone("b.") and not s.read_only
