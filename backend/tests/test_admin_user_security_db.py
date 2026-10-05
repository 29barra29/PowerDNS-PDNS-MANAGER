"""F3 gegen MariaDB (Spec F2/F3 9.2; ``requires_db``, Fixture ``fresh_db``).

1. ``init_db()`` zweimal, Spalte ``users.must_change_password`` (NOT NULL, Default 0).
2. Ende-zu-Ende ueber die echte App: Admin legt Benutzer mit erzwungenem Passwortwechsel an, Login liefert das
   Flag, Gate (403 + Header), Passwortwechsel loest es; Admin-Zufallspasswort beendet die alte Sitzung.
3. E-Mail-Duplikat -> 409 (Anlegen und Aendern).
4. Selbst-/Relativfaelle der Admin-Schutzregeln (der "letzter Admin"-Fall ist nur im Unit-Test pruefbar, weil die
   CI-Datenbank weitere Admins enthalten kann).
Zusaetzlich ([S9]): ``revoke-access``/``access-summary`` auf echten Tabellen (revoked_at, DynDNS, Webhooks,
``queued`` -> ``cancelled``), 2FA-/Passkey-Reset und ``panel_token_count``/``passkey_count`` in der Liste.
Alle Testdaten tragen ein eindeutiges Praefix und werden am Ende geloescht.
"""
import asyncio
import os
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from dbutil import requires_db

pytestmark = requires_db

PW_ADMIN = "admin-passwort-1"


def _run(coro):
    return asyncio.run(coro)


def _keys():
    from app.core import secrets as secret_store

    secret_store.configure_for_tests(os.environ["SECRET_ENCRYPTION_KEY"])


async def _make_admins(prefix: str, n: int = 2) -> list[int]:
    from app.core.auth import hash_password
    from app.core.database import async_session
    from app.models.models import User

    async with async_session() as s:
        rows = [User(username=f"{prefix}_adm{i}", hashed_password=hash_password(PW_ADMIN), role="admin",
                     is_active=True, display_name=f"A{i}") for i in range(n)]
        s.add_all(rows)
        await s.commit()
        return [r.id for r in rows]


async def _cleanup(prefix: str) -> None:
    from app.core.database import async_session
    from app.models.models import (
        AuditLog, DynDnsToken, PanelToken, User, WebAuthnCredential, Webhook, WebhookDelivery,
    )

    async with async_session() as s:
        ids = [r[0] for r in (await s.execute(select(User.id).where(User.username.like(f"{prefix}%")))).all()]
        if ids:
            for model in (PanelToken, DynDnsToken, WebhookDelivery, Webhook, WebAuthnCredential):
                await s.execute(delete(model).where(model.user_id.in_(ids)))
            await s.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
            await s.execute(delete(User).where(User.id.in_(ids)))
        await s.commit()


def _login(username: str, password: str) -> tuple[TestClient, dict]:
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c, r.json()


# --- Nr. 1 ---------------------------------------------------------------------------------------------------
def test_init_db_twice_and_column(fresh_db):
    from app.core.database import init_db

    _run(init_db())
    _run(init_db())

    async def column():
        async with fresh_db.connect() as conn:
            return (await conn.execute(text("SHOW COLUMNS FROM users LIKE 'must_change_password'"))).mappings().all()

    (col,) = _run(column())
    assert col["Null"] == "NO" and str(col["Default"]) == "0"


# --- Nr. 2 + 3 -----------------------------------------------------------------------------------------------
def test_http_create_flagged_user_gate_and_admin_reset(fresh_db):
    prefix = "t3e_" + uuid.uuid4().hex[:6]
    admin_id, other_admin_id = _run(_make_admins(prefix))
    try:
        admin, _ = _login(f"{prefix}_adm0", PW_ADMIN)
        r = admin.post("/api/v1/auth/users", json={
            "username": f"{prefix}_neu", "password": "start-passwort-1", "email": f"{prefix}@example.org",
            "must_change_password": True,
        })
        assert r.status_code == 201, r.text
        new_id = r.json()["id"]
        assert r.json()["must_change_password"] is True

        # Nr. 3: E-Mail-Duplikat beim Anlegen und beim Aendern -> 409
        dup = admin.post("/api/v1/auth/users", json={
            "username": f"{prefix}_dup", "password": "start-passwort-1", "email": f"{prefix}@example.org"})
        assert dup.status_code == 409 and dup.json()["detail"] == "E-Mail wird bereits verwendet"
        dup = admin.put(f"/api/v1/auth/users/{other_admin_id}", json={"email": f"{prefix}@example.org"})
        assert dup.status_code == 409

        user, body = _login(f"{prefix}_neu", "start-passwort-1")
        assert body["user"]["must_change_password"] is True
        r = user.get("/api/v1/servers")
        assert r.status_code == 403 and r.headers.get("x-password-change-required") == "1"
        assert user.get("/api/v1/auth/me").status_code == 200
        same = user.put("/api/v1/auth/me/password",
                        json={"current_password": "start-passwort-1", "new_password": "start-passwort-1"})
        assert same.status_code == 400 and same.json()["code"] == "password_unchanged"
        r = user.put("/api/v1/auth/me/password",
                     json={"current_password": "start-passwort-1", "new_password": "eigenes-passwort-2"})
        assert r.status_code == 200, r.text
        assert user.get("/api/v1/servers").status_code == 200

        # Admin-Zufallspasswort (ohne Body): Flag wieder an, alte Sitzung ungueltig (pwv)
        r = admin.put(f"/api/v1/auth/users/{new_id}/reset-password")
        assert r.status_code == 200, r.text
        assert len(r.json()["new_password"]) == 16 and r.json()["must_change_password"] is True
        assert user.get("/api/v1/auth/me").status_code == 401
        _, body = _login(f"{prefix}_neu", r.json()["new_password"])
        assert body["user"]["must_change_password"] is True

        # Liste: Flag und Zaehler
        listed = admin.get("/api/v1/auth/users").json()
        row = next(u for u in listed["users"] if u["id"] == new_id)
        assert row["must_change_password"] is True and row["passkey_count"] == 0 and row["panel_token_count"] == 0
        assert isinstance(listed["password_reset_mail_available"], bool)
    finally:
        _run(_cleanup(prefix))


# --- Nr. 4 ---------------------------------------------------------------------------------------------------
def test_http_admin_self_and_relative_guards(fresh_db):
    prefix = "t3s_" + uuid.uuid4().hex[:6]
    admin_id, other_admin_id = _run(_make_admins(prefix))
    try:
        admin, _ = _login(f"{prefix}_adm0", PW_ADMIN)
        for body, detail in (({"is_active": False}, "Du kannst dich nicht selbst deaktivieren"),
                             ({"role": "user"}, "Du kannst dir die Admin-Rolle nicht selbst entziehen"),
                             ({"password": "neues-passwort-1"}, "Das eigene Passwort bitte unter Einstellungen ändern")):
            r = admin.put(f"/api/v1/auth/users/{admin_id}", json=body)
            assert r.status_code == 400 and r.json()["detail"] == detail
        assert admin.post(f"/api/v1/auth/users/{admin_id}/reset-2fa", json={}).status_code == 400
        assert admin.delete(f"/api/v1/auth/users/{admin_id}/webauthn-credentials").status_code == 400
        assert admin.post(f"/api/v1/auth/users/{admin_id}/revoke-access", json={}).status_code == 400
        # zweiten Admin deaktivieren ist erlaubt (der handelnde Admin bleibt aktiv), danach reaktivieren
        r = admin.put(f"/api/v1/auth/users/{other_admin_id}", json={"is_active": False})
        assert r.status_code == 200 and r.json()["is_active"] is False
        r = admin.put(f"/api/v1/auth/users/{other_admin_id}", json={"is_active": True, "role": "user"})
        assert r.status_code == 200 and r.json()["role"] == "user"
    finally:
        _run(_cleanup(prefix))


# --- [S9] Zugangs-Widerruf -------------------------------------------------------------------------------------
async def _seed_access(prefix: str, user_id: int) -> dict:
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import DynDnsToken, WebAuthnCredential, Webhook, WebhookDelivery
    from app.services import panel_token as ptk

    async with async_session() as s:
        t1, _ = await ptk.create_token(s, user_id, "aktiv")
        t2, _ = await ptk.create_token(s, user_id, "pausiert")
        t2.is_active = False
        dyn = DynDnsToken(user_id=user_id, name="home", token_prefix="dyn_x", token_hash=uuid.uuid4().hex,
                          hostnames=["home.example."], allowed_types=["A"])
        hook = Webhook(user_id=user_id, name=f"{prefix}-hook", url="https://hooks.example/x", secret="s3cret",
                       events=["*"], is_active=True)
        s.add_all([dyn, hook])
        await s.flush()
        deliveries = []
        for status in ("queued", "failed", "succeeded"):
            d = WebhookDelivery(delivery_id=str(uuid.uuid4()), event_id=str(uuid.uuid4()), webhook_id=hook.id,
                                user_id=user_id, event="zone.created", body="{}", signature="sha256=" + "0" * 64,
                                status=status, next_attempt_at=utcnow())
            s.add(d)
            deliveries.append(d)
        s.add(WebAuthnCredential(user_id=user_id, name="yubi", credential_id=uuid.uuid4().hex, public_key="pk",
                                 sign_count=0))
        await s.commit()
        return {"tokens": [t1.id, t2.id], "dyn": dyn.id, "hook": hook.id, "deliveries": [d.id for d in deliveries]}


def test_http_access_summary_and_revoke_access(fresh_db):
    from app.core.auth import hash_password
    from app.core.database import async_session
    from app.models.models import AuditLog, DynDnsToken, PanelToken, User, WebAuthnCredential, Webhook, WebhookDelivery

    _keys()
    prefix = "t3r_" + uuid.uuid4().hex[:6]
    admin_id, _ = _run(_make_admins(prefix))

    async def make_user():
        async with async_session() as s:
            u = User(username=f"{prefix}_ziel", hashed_password=hash_password("x-passwort-1"), role="user",
                     is_active=True, display_name="Z", totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
            s.add(u)
            await s.commit()
            return u.id

    user_id = _run(make_user())
    ids = _run(_seed_access(prefix, user_id))
    try:
        admin, _ = _login(f"{prefix}_adm0", PW_ADMIN)
        summary = admin.get(f"/api/v1/auth/users/{user_id}/access-summary").json()
        assert summary == {"user_id": user_id, "panel_tokens": 2, "dyndns_tokens": 1, "webhooks": 1,
                           "pending_deliveries": 2, "passkeys": 1, "totp_enabled": True}
        listed = admin.get("/api/v1/auth/users").json()
        row = next(u for u in listed["users"] if u["id"] == user_id)
        assert row["panel_token_count"] == 1 and row["passkey_count"] == 1  # pausierte Tokens zaehlen nicht

        r = admin.post(f"/api/v1/auth/users/{user_id}/revoke-access",
                       json={"reset_2fa": True, "remove_passkeys": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revoked"] == {"panel_tokens": 2, "dyndns_tokens": 1, "webhooks": 1, "cancelled_deliveries": 2}
        assert body["totp_reset"] is True and body["passkeys_removed"] == 1
        assert body["summary"] == {"panel_tokens": 0, "dyndns_tokens": 0, "webhooks": 0, "pending_deliveries": 0}

        async def check():
            async with async_session() as s:
                toks = (await s.execute(select(PanelToken).where(PanelToken.id.in_(ids["tokens"])))).scalars().all()
                dyn = (await s.execute(select(DynDnsToken).where(DynDnsToken.id == ids["dyn"]))).scalar_one()
                hook = (await s.execute(select(Webhook).where(Webhook.id == ids["hook"]))).scalar_one()
                dl = (await s.execute(select(WebhookDelivery.status).where(
                    WebhookDelivery.id.in_(ids["deliveries"])).order_by(WebhookDelivery.id))).scalars().all()
                creds = (await s.execute(select(WebAuthnCredential).where(
                    WebAuthnCredential.user_id == user_id))).scalars().all()
                user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
                audits = (await s.execute(select(AuditLog.action).where(
                    AuditLog.user_id == admin_id).order_by(AuditLog.id))).scalars().all()
                return toks, dyn, hook, dl, creds, user, audits

        toks, dyn, hook, dl, creds, user, audits = _run(check())
        assert all(t.revoked_at is not None and not t.is_active for t in toks)
        assert dyn.is_active is False and hook.is_active is False
        assert dl == ["cancelled", "cancelled", "succeeded"]
        assert creds == [] and user.totp_enabled is False and user.totp_secret is None
        assert {"USER_2FA_RESET", "USER_PASSKEYS_RESET", "USER_ACCESS_REVOKE"} <= set(audits)
    finally:
        _run(_cleanup(prefix))
