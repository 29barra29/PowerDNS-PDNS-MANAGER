import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from app.core.auth import assert_zone_access
from app.models.models import User


def _user(role: str) -> User:
    u = User(
        id=1,
        username="u1",
        email=None,
        hashed_password="x",
        display_name="U",
        role=role,
        is_active=True,
    )
    return u


@pytest.mark.asyncio
async def test_admin_always_ok():
    db = AsyncMock()
    u = _user("admin")
    await assert_zone_access(db, u, "example.com.", write=True)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_user_write_blocks_when_read():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "read"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(HTTPException) as e:
        await assert_zone_access(db, u, "example.com", write=True)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_user_write_ok_when_manage():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "manage"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    await assert_zone_access(db, u, "EXAMPLE.com", write=True)


@pytest.mark.asyncio
async def test_user_read_ok_when_read():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "read"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    await assert_zone_access(db, u, "example.com.", write=False)


# ---------------------------------------------------------------------------------------------
# F14 9.3: Token-Scope, Lese-Token, Schnittmenge mit der Benutzer-ACL, Admin-Freigabe
# ---------------------------------------------------------------------------------------------
from contextlib import contextmanager  # noqa: E402

from app.core.auth import (  # noqa: E402
    TOKEN_NO_ADMIN_DETAIL,
    TOKEN_READ_ONLY_DETAIL,
    assert_effective_admin,
    assert_not_zone_scoped,
    assert_token_scope,
    effective_zone_filter,
    get_admin_user,
    has_zone_access,
    is_effective_admin,
    set_auth_context,
)
from app.core.request_context import TokenScope, current_token_scope  # noqa: E402


def _scope(zones=("allowed.example.",), permission="manage", allow_admin=False):
    return TokenScope(
        token_id=7, name="ci", token_prefix="dnsmgr_usr_test…",
        zones=None if zones is None else frozenset(zones), permission=permission, allow_admin=allow_admin,
    )


@contextmanager
def _token(scope):
    t = current_token_scope.set(scope)
    try:
        yield
    finally:
        current_token_scope.reset(t)


def _acl_db(rows_for_permission=None, zone_rows=()):
    """DB-Mock: Permission-Abfrage -> rows_for_permission[zone] bzw. None; Zonenliste -> zone_rows."""
    rows_for_permission = rows_for_permission or {}

    async def execute(stmt):
        res = MagicMock()
        params = [v for v in stmt.compile().params.values() if isinstance(v, str)]
        res.scalar_one_or_none.return_value = next((rows_for_permission[p] for p in params if p in rows_for_permission), None)
        res.all.return_value = [(z,) for z in zone_rows]
        return res

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.mark.asyncio
async def test_token_scope_blocks_admin_outside_scope():
    db = AsyncMock()
    with _token(_scope()):
        with pytest.raises(HTTPException) as e:
            await assert_zone_access(db, _user("admin"), "other.example.")
    assert e.value.status_code == 403 and "„other.example.“ nicht freigegeben" in e.value.detail
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_token_scope_allows_admin_inside_scope_case_insensitive():
    db = AsyncMock()
    with _token(_scope()):
        await assert_zone_access(db, _user("admin"), "ALLOWED.Example", write=True)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_read_token_blocks_write_even_for_admin():
    with _token(_scope(zones=None, permission="read", allow_admin=True)):
        await assert_zone_access(AsyncMock(), _user("admin"), "x.example.")  # lesen ok
        with pytest.raises(HTTPException) as e:
            await assert_zone_access(AsyncMock(), _user("admin"), "x.example.", write=True)
    assert e.value.status_code == 403 and e.value.detail == TOKEN_READ_ONLY_DETAIL


@pytest.mark.asyncio
async def test_token_scope_intersects_user_acl():
    db = _acl_db({"a.": "manage"})
    with _token(_scope(zones=("a.", "b."))):
        await assert_zone_access(db, _user("user"), "a.", write=True)
        with pytest.raises(HTTPException) as e:
            await assert_zone_access(db, _user("user"), "b.")
    assert e.value.detail == "Keine Berechtigung für diese Zone"


@pytest.mark.asyncio
async def test_no_token_context_unchanged():
    assert current_token_scope.get() is None
    db = AsyncMock()
    await assert_zone_access(db, _user("admin"), "irgendwas.example.", write=True)
    db.execute.assert_not_called()
    assert_token_scope("irgendwas.", write=True)  # No-op ohne Token
    assert_not_zone_scoped("darf nicht werfen")


@pytest.mark.asyncio
async def test_has_zone_access_returns_bool():
    db = _acl_db({"a.": "read"})
    assert await has_zone_access(db, _user("user"), "a.") is True
    assert await has_zone_access(db, _user("user"), "a.", write=True) is False
    assert await has_zone_access(db, _user("user"), "c.") is False
    assert await has_zone_access(db, _user("user"), "") is False  # 400 Zone-Name fehlt -> False
    with _token(_scope()):
        assert await has_zone_access(AsyncMock(), _user("admin"), "other.example.") is False


@pytest.mark.asyncio
async def test_effective_zone_filter_variants():
    # Admin ohne Token -> None (keine Einschraenkung)
    assert await effective_zone_filter(AsyncMock(), _user("admin")) is None
    # Admin + Scope -> Scope
    with _token(_scope(zones=("a.", "b."))):
        assert await effective_zone_filter(AsyncMock(), _user("admin")) == {"a.", "b."}
    # Admin + Token ohne Scope -> None
    with _token(_scope(zones=None)):
        assert await effective_zone_filter(AsyncMock(), _user("admin")) is None
    # Benutzer ohne Scope -> eigene Zonen (normalisiert)
    db = _acl_db(zone_rows=("A.", "c"))
    assert await effective_zone_filter(db, _user("user")) == {"a.", "c."}
    # Benutzer + Scope -> Schnittmenge
    with _token(_scope(zones=("a.", "b."))):
        assert await effective_zone_filter(_acl_db(zone_rows=("a.", "c.")), _user("user")) == {"a."}
    # leerer Scope -> nichts
    with _token(_scope(zones=())):
        assert await effective_zone_filter(AsyncMock(), _user("admin")) == set()


def test_is_effective_admin():
    assert is_effective_admin(_user("admin")) is True
    assert is_effective_admin(_user("user")) is False
    with _token(_scope(zones=None, allow_admin=False)):
        assert is_effective_admin(_user("admin")) is False
    with _token(_scope(zones=None, allow_admin=True)):
        assert is_effective_admin(_user("admin")) is True
        assert is_effective_admin(_user("user")) is False


@pytest.mark.asyncio
async def test_admin_user_dependency_rejects_token_without_allow_admin():
    assert await get_admin_user(_user("admin")) is not None
    with _token(_scope(zones=None, allow_admin=False)):
        with pytest.raises(HTTPException) as e:
            await get_admin_user(_user("admin"))
        assert e.value.status_code == 403 and e.value.detail == TOKEN_NO_ADMIN_DETAIL
    with _token(_scope(zones=None, allow_admin=True)):
        assert await get_admin_user(_user("admin")) is not None
        with pytest.raises(HTTPException) as e:
            await get_admin_user(_user("user"))
        assert e.value.detail == "Nur Administratoren haben Zugriff"
    with pytest.raises(HTTPException):
        assert_effective_admin(_user("user"))


def test_assert_not_zone_scoped():
    with _token(_scope()):
        with pytest.raises(HTTPException) as e:
            assert_not_zone_scoped("Text")
        assert (e.value.status_code, e.value.detail) == (403, "Text")
    with _token(_scope(zones=None)):
        assert_not_zone_scoped("Text")


def test_set_auth_context_sets_and_resets_everything():
    from types import SimpleNamespace

    from app.core import request_context as rc

    req = SimpleNamespace(state=SimpleNamespace())
    scope = _scope()
    set_auth_context(req, "panel_token", scope, "ROW", username="u" * 150, client_ip="198.51.100.7")
    assert (req.state.auth_via, req.state.token_scope, req.state.panel_token) == ("panel_token", scope, "ROW")
    assert rc.get_auth_via() == "panel_token" and rc.get_token_scope() is scope
    assert rc.get_actor_username() == "u" * 100 and rc.get_client_ip_ctx() == "198.51.100.7"
    assert rc.audit_auth_context()["token_id"] == 7
    set_auth_context(req, None)
    assert req.state.auth_via is None and req.state.panel_token is None
    assert (rc.get_auth_via(), rc.get_token_scope(), rc.get_actor_username(), rc.get_client_ip_ctx()) == (
        None, None, None, None)
    set_auth_context(None, "acme_token")
    assert rc.audit_auth_context() == {"via": "acme_token"}


def test_normalize_alias_and_reset_token_ttl():
    from datetime import datetime, timezone

    from app.core import auth as core_auth
    from app.core.names import normalize_zone_name

    assert core_auth._normalize_zone_name is normalize_zone_name
    payload = core_auth.decode_token(core_auth.create_password_reset_token(1, "h", expires_minutes=1440))
    now = datetime.now(timezone.utc).timestamp()
    assert abs(payload["exp"] - (now + 1440 * 60)) < 120
    payload = core_auth.decode_token(core_auth.create_password_reset_token(1, "h"))
    assert abs(payload["exp"] - (now + 3600)) < 120
    assert core_auth.decode_password_reset_payload(core_auth.create_password_reset_token(1, "h"))["sub"] == "1"
