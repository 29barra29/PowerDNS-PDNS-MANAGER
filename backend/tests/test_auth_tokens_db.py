"""Panel-Token-Service und Token-Authentifizierung gegen MariaDB (``requires_db``, Fixture ``fresh_db``).

Prueft die SQL-Pfade aus ``services/panel_token.py`` (JSON-Scope, Zaehlungen, Widerruf, Scope-Bereinigung) und
den kompletten HTTP-Ablauf Session -> Token anlegen -> Token benutzen -> widerrufen ueber die echte App
(``get_db`` mit scope=function in ``get_current_user``). Die Endpunkt-Tests von F14-APP
(``test_panel_token_db.py``) bauen darauf auf.
"""
import asyncio
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from dbutil import requires_db

pytestmark = requires_db


def _run(coro):
    return asyncio.run(coro)


async def _make_users(prefix: str):
    from app.core.auth import hash_password
    from app.core.database import async_session
    from app.models.models import User, UserZoneAccess

    async with async_session() as s:
        admin = User(username=f"{prefix}_adm", hashed_password=hash_password("x-passwort-1"), role="admin",
                     is_active=True, display_name="A")
        user = User(username=f"{prefix}_usr", hashed_password=hash_password("x-passwort-1"), role="user",
                    is_active=True, display_name="U")
        s.add_all([admin, user])
        await s.flush()
        s.add(UserZoneAccess(user_id=user.id, zone_name="kunde.example.", permission="manage"))
        await s.commit()
        return admin.id, user.id


def test_service_roundtrip_and_counts(fresh_db):
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import PanelToken
    from app.services import panel_token as ptk

    prefix = "t14s_" + uuid.uuid4().hex[:6]
    admin_id, user_id = _run(_make_users(prefix))

    async def scenario():
        async with async_session() as s:
            a, plain_a = await ptk.create_token(s, user_id, "scoped", scope_zones=["kunde.example."],
                                                permission="read", expires_at=utcnow() + timedelta(days=1))
            b, _ = await ptk.create_token(s, user_id, "paused")
            b.is_active = False
            c, _ = await ptk.create_token(s, user_id, "expired", expires_at=utcnow() - timedelta(days=1))
            d, _ = await ptk.create_token(s, admin_id, "admin", allow_admin=True)
            await s.commit()
            ids = (a.id, b.id, c.id, d.id)

        async with async_session() as s:
            rows = await ptk.list_tokens(s, user_id)
            assert [r.id for r in rows] == sorted(ids[:3], reverse=True)
            row = next(r for r in rows if r.id == ids[0])
            assert row.scope_zones == ["kunde.example."]  # JSON-Roundtrip
            owner = await ptk.owner_zone_set(s, user_id)
            out = ptk.serialize_token(row, owner_role="user", owner_zones=owner, now=utcnow())
            assert out["status"] == "active" and out["permission"] == "read" and out["inaccessible_zones"] == []
            assert await ptk.count_open_tokens(s, user_id) == 3
            counts = await ptk.count_active_by_user(s)
            assert counts.get(user_id) == 1 and counts.get(admin_id) == 1  # pausiert/abgelaufen zaehlen nicht

            assert (await ptk.revoke_token(s, user_id, ids[0])) is not None
            assert await ptk.revoke_token(s, user_id, ids[0]) is None
            assert await ptk.revoke_token(s, admin_id, ids[1]) is None  # fremder Token
            await s.commit()

        async with async_session() as s:
            assert ids[0] not in [r.id for r in await ptk.list_tokens(s, user_id)]
            revoked = (await s.execute(select(PanelToken).where(PanelToken.id == ids[0]))).scalar_one()
            assert revoked.revoked_at is not None and revoked.is_active is False
            rest = await ptk.revoke_all_for_user(s, user_id)
            assert sorted(r.id for r in rest) == sorted(ids[1:3])
            await s.commit()
            assert await ptk.count_open_tokens(s, user_id) == 0
        return plain_a

    plain = _run(scenario())
    assert plain.startswith("dnsmgr_usr_")


def test_remove_zone_from_scopes(fresh_db):
    from app.core.database import async_session
    from app.models.models import PanelToken
    from app.services import panel_token as ptk

    prefix = "t14z_" + uuid.uuid4().hex[:6]
    _, user_id = _run(_make_users(prefix))

    async def scenario():
        async with async_session() as s:
            t1, _ = await ptk.create_token(s, user_id, "zwei", scope_zones=["gone.example.", "kunde.example."])
            t2, _ = await ptk.create_token(s, user_id, "eine", scope_zones=["gone.example."])
            t3, _ = await ptk.create_token(s, user_id, "alle")
            await s.commit()
            ids = (t1.id, t2.id, t3.id)
        async with async_session() as s:
            assert await ptk.remove_zone_from_scopes(s, "GONE.example") == 2
            await s.commit()
        async with async_session() as s:
            rows = {r.id: r for r in (await s.execute(select(PanelToken).where(PanelToken.id.in_(ids)))).scalars()}
            assert rows[ids[0]].scope_zones == ["kunde.example."]
            assert rows[ids[1]].scope_zones == []
            assert rows[ids[2]].scope_zones is None

    _run(scenario())


def test_http_session_creates_token_and_token_is_enforced(fresh_db):
    from app.core.auth import create_access_token
    from app.core.database import async_session
    from app.main import app
    from app.models.models import AuditLog, PanelToken, User

    prefix = "t14h_" + uuid.uuid4().hex[:6]
    _, user_id = _run(_make_users(prefix))

    async def load_user():
        async with async_session() as s:
            return (await s.execute(select(User).where(User.id == user_id))).scalar_one()

    user = _run(load_user())
    session_h = {"Authorization": f"Bearer {create_access_token(data={'sub': str(user_id)}, user=user)}"}
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/api/v1/auth/me/panel-tokens", headers=session_h,
               json={"name": "ci", "scope_zones": ["Kunde.Example"], "permission": "read"})
    assert r.status_code == 201, r.text
    tok, tid = r.json()["plaintext_token"], r.json()["token"]["id"]
    token_h = {"Authorization": f"Bearer {tok}"}

    me = c.get("/api/v1/auth/me", headers=token_h)
    assert me.status_code == 200 and me.json()["auth"]["token"]["scope_zones"] == ["kunde.example."]
    assert c.put("/api/v1/auth/me", headers=token_h, json={"display_name": "x"}).json()["detail"] == \
        "Dieser API-Token hat nur Leserechte"
    assert c.get("/api/v1/auth/me/panel-tokens", headers=token_h).status_code == 403
    listed = c.get("/api/v1/auth/me/panel-tokens", headers=session_h).json()
    assert [t["id"] for t in listed["tokens"]] == [tid] and listed["max_tokens"] == 50

    async def check_db():
        async with async_session() as s:
            row = (await s.execute(select(PanelToken).where(PanelToken.id == tid))).scalar_one()
            audits = (await s.execute(select(AuditLog).where(AuditLog.action == "PANEL_TOKEN_CREATE",
                                                             AuditLog.user_id == user_id))).scalars().all()
            return row, audits

    row, audits = _run(check_db())
    assert row.last_used_at is not None and row.last_used_ip  # touch_last_used committet
    assert len(audits) == 1 and tok not in str(audits[0].details)
    assert audits[0].actor_username == f"{prefix}_usr"

    assert c.delete(f"/api/v1/auth/me/panel-tokens/{tid}", headers=session_h).status_code == 200
    r = c.get("/api/v1/auth/me", headers=token_h)
    assert r.status_code == 401 and r.json()["detail"] == "Ungültiger API-Token"
    assert c.delete(f"/api/v1/auth/me/panel-tokens/{tid}", headers=session_h).status_code == 404


def test_http_password_change_gate_db(fresh_db):
    from app.core.auth import create_access_token, hash_password
    from app.core.database import async_session
    from app.main import app
    from app.models.models import User

    name = "t3g_" + uuid.uuid4().hex[:6]

    async def make():
        async with async_session() as s:
            u = User(username=name, hashed_password=hash_password("alt-passwort-1"), role="user",
                     is_active=True, display_name=name, must_change_password=True)
            s.add(u)
            await s.commit()
            return u

    u = _run(make())
    h = {"Authorization": f"Bearer {create_access_token(data={'sub': str(u.id)}, user=u)}"}
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/v1/servers", headers=h)
    assert r.status_code == 403 and r.headers.get("x-password-change-required") == "1"
    assert c.get("/api/v1/auth/me", headers=h).json()["must_change_password"] is True
    r = c.put("/api/v1/auth/me/password", headers=h,
              json={"current_password": "alt-passwort-1", "new_password": "neu-passwort-2"})
    assert r.status_code == 200, r.text

    async def flag_now():
        async with async_session() as s:
            return (await s.execute(select(User.must_change_password).where(User.id == u.id))).scalar_one()

    assert _run(flag_now()) in (False, 0)
