"""Sitzungs-Widerruf (L3, WS-W2-NACHARBEIT): ``users.sessions_revoked_at`` beendet Browser-Sitzungen.

"Alle Zugaenge widerrufen" (``access_revocation.revoke_all``) und der Admin-Passwort-Reset setzen den Zeitstempel;
``core.auth.get_current_user`` lehnt danach jede Sitzung (JWT) ab, deren ``iat`` davor liegt – mit demselben Text
wie eine abgelaufene Sitzung (401). Eine neue Anmeldung danach funktioniert.

Laeuft gegen SQLite hinter dem AsyncSession-Adapter aus ``f10fakes`` (echte Router, echte Abfragen).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from f10fakes import F10Env, session_headers
from app.core import auth as core_auth
from app.core import login_rate_limit as lrl
from app.core import secrets as secret_store
from app.core.auth import SESSION_EXPIRED_DETAIL, session_revoked
from app.core.timeutil import utcnow_naive
from app.models.models import DynDnsToken, PanelToken, User
from app.routers import auth as auth_router
from app.routers import panel_tokens as panel_tokens_router
from app.services import access_revocation as ar
from app.services import panel_token as ptk

PW = "Passwort-123"
ME = "/api/v1/auth/me"


@pytest.fixture(autouse=True)
def _fresh_state():
    lrl.reset_for_tests()
    core_auth._TOTP_USED.clear()
    secret_store.configure_for_tests()
    yield
    lrl.reset_for_tests()
    core_auth._TOTP_USED.clear()


@pytest.fixture
def env(monkeypatch):
    from app.services import audit

    async def _fake_detached(*a, **kw):
        return None

    monkeypatch.setattr(audit, "write_audit_detached", _fake_detached)
    e = F10Env(auth_router, panel_tokens_router)
    yield e
    e.close()


def _old_session(user: User) -> dict[str, str]:
    """Sitzung, die vor dem Widerruf ausgestellt wurde (iat 10 s in der Vergangenheit)."""
    return session_headers(user, iat=int(time.time()) - 10)


def _revoked_at(env: F10Env, user: User):
    env.session.expire_all()
    return env.session.execute(select(User.sessions_revoked_at).where(User.id == user.id)).scalar_one()


# ---------------------------------------------------------------------------------------------
# Kernpruefung (ohne HTTP)
# ---------------------------------------------------------------------------------------------
def test_session_revoked_compares_iat_with_timestamp():
    user = User(id=1, username="anna", hashed_password="x")
    now = int(time.time())
    assert session_revoked({"iat": now - 100}, user) is False          # kein Widerruf gesetzt
    user.sessions_revoked_at = datetime.fromtimestamp(now, tz=timezone.utc).replace(tzinfo=None)
    assert session_revoked({"iat": now - 1}, user) is True
    assert session_revoked({"iat": now}, user) is False                  # ab dem Zeitstempel gueltig
    assert session_revoked({"iat": now + 5}, user) is False
    assert session_revoked({}, user) is True                             # ohne iat: fail-closed
    assert session_revoked({"iat": True}, user) is True
    assert session_revoked({"iat": "123"}, user) is True
    # aware Zeitstempel werden wie naive UTC behandelt
    user.sessions_revoked_at = datetime.fromtimestamp(now, tz=timezone.utc)
    assert session_revoked({"iat": now - 1}, user) is True and session_revoked({"iat": now}, user) is False


# ---------------------------------------------------------------------------------------------
# "Alle Zugaenge widerrufen"
# ---------------------------------------------------------------------------------------------
def test_revoke_access_ends_existing_browser_session(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    admin_h, anna_h = session_headers(admin), _old_session(anna)
    c = env.client()
    assert c.get(ME, headers=anna_h).status_code == 200

    r = c.post(f"/api/v1/auth/users/{anna.id}/revoke-access", headers=admin_h, json={})
    assert r.status_code == 200, r.text
    assert _revoked_at(env, anna) is not None

    r = c.get(ME, headers=anna_h)
    assert r.status_code == 401 and r.json()["detail"] == SESSION_EXPIRED_DETAIL
    # Panel-Tokens anlegen geht mit der alten Sitzung auch nicht mehr (Kontouebernahme-Szenario L3)
    r = c.post("/api/v1/auth/me/panel-tokens", headers=anna_h, json={"name": "angreifer"})
    assert r.status_code == 401
    # der handelnde Admin bleibt angemeldet
    assert c.get(ME, headers=admin_h).status_code == 200


def test_revoke_access_with_only_2fa_reset_still_ends_sessions(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW, totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    anna_h = _old_session(anna)
    c = env.client()
    body = {"panel_tokens": False, "dyndns_tokens": False, "webhooks": False, "reset_2fa": True}
    r = c.post(f"/api/v1/auth/users/{anna.id}/revoke-access", headers=session_headers(admin), json=body)
    assert r.status_code == 200, r.text
    assert c.get(ME, headers=anna_h).status_code == 401


def test_new_login_after_revocation_works(env, monkeypatch):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    anna_h = _old_session(anna)
    # Widerruf "vor 5 Sekunden" (sonst muesste der Test bis zur naechsten vollen Sekunde warten)
    monkeypatch.setattr(ar, "utcnow_naive", lambda: utcnow_naive() - timedelta(seconds=5))
    c = env.client()
    assert c.post(f"/api/v1/auth/users/{anna.id}/revoke-access", headers=session_headers(admin),
                  json={}).status_code == 200
    assert c.get(ME, headers=anna_h).status_code == 401

    fresh = env.client(ip="198.51.100.20")
    r = fresh.post("/api/v1/auth/login", data={"username": "anna", "password": PW})
    assert r.status_code == 200, r.text
    r = fresh.get(ME)   # Cookie aus der Anmeldung
    assert r.status_code == 200 and r.json()["username"] == "anna"
    # die alte Sitzung bleibt ungueltig
    assert c.get(ME, headers=anna_h).status_code == 401


def test_session_issued_in_same_second_as_revocation_is_rejected(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    c = env.client()
    assert c.post(f"/api/v1/auth/users/{anna.id}/revoke-access", headers=session_headers(admin),
                  json={}).status_code == 200
    revoked = _revoked_at(env, anna)
    same_second = int(revoked.replace(tzinfo=timezone.utc).timestamp()) - 1
    assert c.get(ME, headers=session_headers(anna, iat=same_second)).status_code == 401
    at_mark = int(revoked.replace(tzinfo=timezone.utc).timestamp())
    assert c.get(ME, headers=session_headers(anna, iat=at_mark)).status_code == 200


def test_revoke_all_also_locks_dyndns_and_panel_tokens(env):
    """Zusammenspiel: Sitzungen + Panel-Tokens + DynDNS-Sicherheitssperre (A1 WS-F9F11-BE fix2) in einem Aufruf."""
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    import asyncio

    async def seed():
        await ptk.create_token(env.db, anna.id, "t1")
        env.session.add_all([
            DynDnsToken(user_id=anna.id, name="aktiv", token_prefix="dnsmgr_dyn_a", token_hash="a" * 64,
                        hostnames=["home.example."], allowed_types=["A"], is_active=True),
            DynDnsToken(user_id=anna.id, name="pausiert", token_prefix="dnsmgr_dyn_b", token_hash="b" * 64,
                        hostnames=["nas.example."], allowed_types=["A"], is_active=False),
        ])
        await env.db.commit()

    asyncio.run(seed())
    c = env.client()
    r = c.post(f"/api/v1/auth/users/{anna.id}/revoke-access", headers=session_headers(admin), json={})
    assert r.status_code == 200, r.text
    assert r.json()["revoked"]["dyndns_tokens"] == 2 and r.json()["revoked"]["panel_tokens"] == 1
    assert r.json()["summary"]["dyndns_tokens"] == 0
    env.session.expire_all()
    dyn = env.session.execute(select(DynDnsToken).where(DynDnsToken.user_id == anna.id)).scalars().all()
    assert all(t.is_active is False and t.token_hash.startswith("revoked:") for t in dyn)
    toks = env.session.execute(select(PanelToken).where(PanelToken.user_id == anna.id)).scalars().all()
    assert all(t.revoked_at is not None for t in toks)


# ---------------------------------------------------------------------------------------------
# Admin-Aktionen mit Passwortwechsel
# ---------------------------------------------------------------------------------------------
def test_admin_password_reset_sets_timestamp_and_ends_session(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    anna_h = _old_session(anna)
    c = env.client()
    assert c.get(ME, headers=anna_h).status_code == 200
    r = c.put(f"/api/v1/auth/users/{anna.id}/reset-password", headers=session_headers(admin))
    assert r.status_code == 200, r.text
    assert _revoked_at(env, anna) is not None
    r = c.get(ME, headers=anna_h)
    assert r.status_code == 401 and r.json()["detail"] == SESSION_EXPIRED_DETAIL


def test_update_user_with_revoke_all_access_sets_timestamp(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    anna_h = _old_session(anna)
    c = env.client()
    r = c.put(f"/api/v1/auth/users/{anna.id}", headers=session_headers(admin),
              json={"display_name": "Anna", "revoke_all_access": True})
    assert r.status_code == 200, r.text
    assert _revoked_at(env, anna) is not None
    assert c.get(ME, headers=anna_h).status_code == 401


def test_unrelated_admin_actions_keep_sessions(env):
    admin = env.add_user("chef", role="admin")
    anna = env.add_user("anna", password=PW)
    anna_h = _old_session(anna)
    c = env.client()
    r = c.put(f"/api/v1/auth/users/{anna.id}", headers=session_headers(admin), json={"display_name": "Anna B."})
    assert r.status_code == 200, r.text
    assert _revoked_at(env, anna) is None
    assert c.get(ME, headers=anna_h).status_code == 200
