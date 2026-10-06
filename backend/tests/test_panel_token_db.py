"""Token-Verwaltung WS-F14-APP gegen MariaDB (F14 9.5, ``requires_db``, Fixture ``fresh_db``).

Ergaenzt ``test_auth_tokens_db.py`` (Service-SQL, Anlage/Widerruf per HTTP) und ``test_migrations_db.py``
(Datenmigration der Bestandstokens, F14 9.5 Nr. 25) um die neuen Endpunkte ueber die echte App:
Bearbeiten (Pause, Ablauf, Scope, Berechtigung) mit Wirkung auf den Token, Admin-Sicht und -Widerruf,
``panel_token_count`` in der Benutzerliste und den JSON-Roundtrip von ``scope_zones`` (F14 9.5 Nr. 28).
"""
import asyncio
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update

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
        s.add(UserZoneAccess(user_id=user.id, zone_name="Zweite.Example", permission="read"))
        await s.commit()
        return admin, user


def _session_headers(user) -> dict:
    from app.core.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"}


async def _token_row(tid: int):
    from app.core.database import async_session
    from app.models.models import PanelToken

    async with async_session() as s:
        return (await s.execute(select(PanelToken).where(PanelToken.id == tid))).scalar_one()


async def _audits(action: str, **filters):
    from app.core.database import async_session
    from app.models.models import AuditLog

    async with async_session() as s:
        q = select(AuditLog).where(AuditLog.action == action)
        for k, v in filters.items():
            q = q.where(getattr(AuditLog, k) == v)
        return (await s.execute(q.order_by(AuditLog.id))).scalars().all()


def test_update_endpoint_changes_take_effect(fresh_db):
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.main import app
    from app.models.models import PanelToken

    prefix = "t14u_" + uuid.uuid4().hex[:6]
    _, user = _run(_make_users(prefix))
    sh = _session_headers(user)
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/api/v1/auth/me/panel-tokens", headers=sh,
               json={"name": "ci", "scope_zones": ["kunde.example"], "permission": "read", "expires_in_days": 30})
    assert r.status_code == 201, r.text
    tid, plain = r.json()["token"]["id"], r.json()["plaintext_token"]
    th = {"Authorization": f"Bearer {plain}"}
    assert c.get("/api/v1/auth/me", headers=th).status_code == 200

    # fremde Zone -> 403, nichts geaendert
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"scope_zones": ["fremd.example"]})
    assert r.status_code == 403 and "fremd.example." in r.json()["detail"]
    # eigene Zone (gespeichert mit Grossbuchstaben, ohne Punkt) ist erlaubt
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh,
              json={"scope_zones": ["kunde.example.", "zweite.example"]})
    assert r.status_code == 200, r.text
    assert r.json()["token"]["scope_zones"] == ["kunde.example.", "zweite.example."]
    assert r.json()["token"]["inaccessible_zones"] == []

    # pausieren -> 401, Status "paused"
    assert c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"is_active": False}).status_code == 200
    r = c.get("/api/v1/auth/me", headers=th)
    assert r.status_code == 401 and r.json()["detail"] == "API-Token ist deaktiviert"
    listed = c.get("/api/v1/auth/me/panel-tokens", headers=sh).json()["tokens"]
    assert [t["status"] for t in listed if t["id"] == tid] == ["paused"]

    # aktivieren + Schreibrecht + kein Ablauf
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh,
              json={"is_active": True, "permission": "manage", "expires_in_days": None})
    assert r.status_code == 200 and r.json()["token"]["status"] == "active"
    assert r.json()["token"]["expires_at"] is None
    row = _run(_token_row(tid))
    assert row.expires_at is None and row.permission == "manage" and row.is_active
    me = c.get("/api/v1/auth/me", headers=th).json()
    assert me["auth"]["token"]["permission"] == "manage" and me["auth"]["token"]["expires_at"] is None

    # abgelaufen (DB-Datum zuruecksetzen) -> 401; neue Laufzeit ueber PUT -> wieder gueltig
    async def expire():
        async with async_session() as s:
            await s.execute(update(PanelToken).where(PanelToken.id == tid)
                            .values(expires_at=utcnow() - timedelta(minutes=1)))
            await s.commit()

    _run(expire())
    r = c.get("/api/v1/auth/me", headers=th)
    assert r.status_code == 401 and r.json()["detail"] == "API-Token ist abgelaufen"
    listed = c.get("/api/v1/auth/me/panel-tokens", headers=sh).json()["tokens"]
    assert [t["status"] for t in listed if t["id"] == tid] == ["expired"]
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"expires_in_days": 7})
    assert r.status_code == 200 and r.json()["token"]["status"] == "active"
    assert c.get("/api/v1/auth/me", headers=th).status_code == 200

    # alle meine Zonen (null); keine Aenderung -> kein Audit
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"scope_zones": None})
    assert r.status_code == 200 and r.json()["token"]["scope_zones"] is None
    assert _run(_token_row(tid)).scope_zones is None
    n_before = len(_run(_audits("PANEL_TOKEN_UPDATE", user_id=user.id)))
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"name": "ci"})
    assert r.status_code == 200 and r.json()["message"] == "Keine Änderungen"
    audits = _run(_audits("PANEL_TOKEN_UPDATE", user_id=user.id))
    assert len(audits) == n_before == 5
    assert audits[0].details["changed"]["scope_zones"]["to"] == ["kunde.example.", "zweite.example."]
    assert all(plain not in str(a.details) for a in audits)

    # Admin-Freigabe als Nicht-Admin -> 403; widerrufen -> PUT 404 (nicht reaktivierbar)
    assert c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"allow_admin": True}).status_code == 403
    assert c.delete(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh).status_code == 200
    r = c.put(f"/api/v1/auth/me/panel-tokens/{tid}", headers=sh, json={"is_active": True})
    assert r.status_code == 404 and r.json()["detail"] == "Token nicht gefunden"
    assert _run(_token_row(tid)).revoked_at is not None


def test_admin_view_revoke_and_user_count(fresh_db):
    from app.main import app

    prefix = "t14a_" + uuid.uuid4().hex[:6]
    admin, user = _run(_make_users(prefix))
    ah, uh = _session_headers(admin), _session_headers(user)
    c = TestClient(app, raise_server_exceptions=False)

    created = []
    for name in ("eins", "zwei", "drei"):
        r = c.post("/api/v1/auth/me/panel-tokens", headers=uh, json={"name": name, "scope_zones": ["kunde.example"]})
        assert r.status_code == 201, r.text
        created.append((r.json()["token"]["id"], r.json()["plaintext_token"]))
    c.put(f"/api/v1/auth/me/panel-tokens/{created[2][0]}", headers=uh, json={"is_active": False})

    def count_of(uid):
        users = c.get("/api/v1/auth/users", headers=ah).json()["users"]
        return next(u["panel_token_count"] for u in users if u["id"] == uid)

    assert count_of(user.id) == 2  # pausierte zaehlen nicht

    # Nicht-Admin sieht fremde Tokens nicht
    assert c.get(f"/api/v1/auth/users/{admin.id}/panel-tokens", headers=uh).status_code == 403

    r = c.get(f"/api/v1/auth/users/{user.id}/panel-tokens", headers=ah)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == f"{prefix}_usr"
    assert [t["id"] for t in body["tokens"]] == [tid for tid, _ in reversed(created)]
    assert {t["status"] for t in body["tokens"]} == {"active", "paused"}
    assert all("token_hash" not in t for t in body["tokens"])

    # einzelnen Token widerrufen
    tid, plain = created[0]
    assert c.delete(f"/api/v1/auth/users/{user.id}/panel-tokens/{tid}", headers=ah).status_code == 200
    assert c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plain}"}).status_code == 401
    assert c.delete(f"/api/v1/auth/users/{user.id}/panel-tokens/{tid}", headers=ah).status_code == 404
    # Token eines anderen Benutzers ueber die falsche user_id -> 404
    assert c.delete(f"/api/v1/auth/users/{admin.id}/panel-tokens/{created[1][0]}", headers=ah).status_code == 404

    # alle widerrufen (auch pausierte)
    r = c.delete(f"/api/v1/auth/users/{user.id}/panel-tokens", headers=ah)
    assert r.status_code == 200 and r.json()["revoked"] == 2
    assert c.get(f"/api/v1/auth/users/{user.id}/panel-tokens", headers=ah).json()["tokens"] == []
    assert count_of(user.id) == 0
    r = c.delete(f"/api/v1/auth/users/{user.id}/panel-tokens", headers=ah)
    assert r.json() == {"message": "0 Token widerrufen", "revoked": 0}

    audits = _run(_audits("PANEL_TOKEN_ADMIN_REVOKE", user_id=admin.id))
    assert [a.details["count"] for a in audits] == [1, 2]  # kein Audit bei 0
    assert audits[0].resource_name == f"{prefix}_usr" and audits[0].details["target_user_id"] == user.id
    assert sorted(audits[1].details["token_ids"]) == sorted([created[1][0], created[2][0]])

    assert c.get("/api/v1/auth/users/999999/panel-tokens", headers=ah).status_code == 404


def test_json_column_roundtrip_mariadb(fresh_db):
    from app.core.database import async_session
    from app.services import panel_token as ptk

    prefix = "t14j_" + uuid.uuid4().hex[:6]
    admin, _ = _run(_make_users(prefix))
    zones = ptk.normalize_scope_zones(["b.example", "A.example.", "bücher.example", "_srv.example"])

    async def scenario():
        async with async_session() as s:
            row, _ = await ptk.create_token(s, admin.id, "json", scope_zones=zones)
            await s.commit()
            tid = row.id
        first = (await _token_row(tid)).scope_zones
        async with async_session() as s:
            row = await ptk.get_open_token(s, admin.id, tid)
            removed = await ptk.remove_zone_from_scopes(s, "A.EXAMPLE")
            await s.commit()
        second = (await _token_row(tid)).scope_zones
        return first, removed, second

    first, removed, second = _run(scenario())
    assert first == ["_srv.example.", "a.example.", "b.example.", "xn--bcher-kva.example."]
    assert removed >= 1 and second == ["_srv.example.", "b.example.", "xn--bcher-kva.example."]
