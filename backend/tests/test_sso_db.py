"""MariaDB-Integrationstests fuer F10 (9.2 Nr. 1-5): Schema, Upgrade aus 2.4.1, externe Konten real, eindeutige
Identitaet und ein OIDC-Durchlauf Start -> Callback -> ``/auth/me`` mit echtem Lifespan.

Gate ``requires_db`` (``CI=true`` oder ``RUN_DB_TESTS=1`` plus ``DATABASE_URL``; lokal ``scripts/dev/test-db.sh``).
Jeder Test bereitet die Wegwerf-Datenbank selbst vor (``prepare_fresh``/``prepare_241``), damit die Faelle
unabhaengig von der Reihenfolge laufen. Testdaten: Benutzer ``t10_<uuid8>``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from dbutil import prepare_241, prepare_fresh, requires_db  # noqa: E402

ISS = "https://idp.example.com/realms/t10"

pytestmark = requires_db


def _engine():
    from app.core.database import engine

    return engine


async def _q(sql: str, params: dict | None = None) -> list:
    async with _engine().connect() as conn:
        return list((await conn.execute(text(sql), params or {})).all())


def _run(coro):
    try:
        return asyncio.run(coro)
    finally:
        asyncio.run(_engine().dispose(close=False))


async def _with_session(fn):
    from app.core.database import async_session

    async with async_session() as s:
        try:
            result = await fn(s)
            await s.commit()
            return result
        except Exception:
            await s.rollback()
            raise


def _fast_hash(monkeypatch):
    from app.services import sso_provisioning as prov

    monkeypatch.setattr(prov, "hash_password", lambda pw: "fake$" + hashlib.sha256(pw.encode()).hexdigest())


# ---------------------------------------------------------------------------------------------
# Nr. 1 init_db zweimal: Spalten, binaere Kollation, UNIQUE-Index
# ---------------------------------------------------------------------------------------------
def test_init_db_twice_columns_collation_index():
    from app.core.database import MIGRATION_ERRORS, init_db

    _run(prepare_fresh(_engine()))
    MIGRATION_ERRORS.clear()
    _run(init_db())
    assert MIGRATION_ERRORS == []
    cols = {r[0]: (r[1], r[2], r[3]) for r in _run(_q(
        "SELECT COLUMN_NAME, DATA_TYPE, COLLATION_NAME, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
        "AND COLUMN_NAME IN ('auth_source', 'external_issuer', 'external_id')"))}
    assert set(cols) == {"auth_source", "external_issuer", "external_id"}
    assert cols["auth_source"][0] == "varchar" and cols["auth_source"][2] == "NO"
    assert cols["external_issuer"][1] == "utf8mb4_bin" and cols["external_id"][1] == "utf8mb4_bin"
    idx = _run(_q(
        "SELECT NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND INDEX_NAME = 'uq_users_external' "
        "GROUP BY NON_UNIQUE"))
    assert [(int(a), b) for a, b in idx] == [(0, "auth_source,external_issuer,external_id")]


# ---------------------------------------------------------------------------------------------
# Nr. 2 Upgrade aus 2.4.1: Bestandsnutzer wird lokal
# ---------------------------------------------------------------------------------------------
def test_upgrade_from_241_existing_user_is_local():
    from app.core.database import init_db

    _run(prepare_241(_engine()))

    async def seed():
        async with _engine().begin() as conn:
            await conn.execute(text(
                "INSERT INTO users (username, email, hashed_password, display_name, role, is_active, totp_enabled) "
                "VALUES ('t10_alt', 't10_alt@example.com', 'x', 'Alt', 'admin', 1, 0)"))

    _run(seed())
    _run(init_db())
    rows = _run(_q("SELECT auth_source, external_issuer, external_id FROM users WHERE username = 't10_alt'"))
    assert [tuple(r) for r in rows] == [("local", None, None)]


# ---------------------------------------------------------------------------------------------
# Nr. 3 resolve_external_user real; Nr. 4 Unique-Constraint
# ---------------------------------------------------------------------------------------------
def _profile(sub: str, username: str):
    from app.services.sso_provisioning import ExternalProfile

    return ExternalProfile(source="oidc", issuer=ISS, subject=sub, username_hint=username, email=None,
                           email_verified=None, display_name=None, groups=None)


def test_resolve_external_user_real(monkeypatch):
    from app.models.models import User
    from app.services.sso_provisioning import ProvisioningPolicy, resolve_external_user

    _fast_hash(monkeypatch)
    _run(prepare_fresh(_engine()))
    policy = ProvisioningPolicy(source="oidc", jit_enabled=True, jit_allow_any_account=True)
    tag = uuid.uuid4().hex[:8]

    async def scenario(s):
        s.add(User(username="admin", hashed_password="x", role="admin", is_active=True))
        await s.flush()
        first = await resolve_external_user(s, _profile(f"Abc-{tag}", f"t10_{tag}"), policy)
        again = await resolve_external_user(s, _profile(f"Abc-{tag}", f"t10_{tag}"), policy)
        lower = await resolve_external_user(s, _profile(f"abc-{tag}", f"t10_{tag}"), policy)
        clash = await resolve_external_user(s, _profile(f"sub-admin-{tag}", "admin"), policy)
        return first, again, lower, clash

    first, again, lower, clash = _run(_with_session(scenario))
    assert first.created and not again.created and again.user.id == first.user.id
    assert lower.created and lower.user.id != first.user.id     # sub ist case-sensitiv (utf8mb4_bin)
    assert clash.user.username == "admin-2"
    rows = _run(_q("SELECT external_id FROM users WHERE auth_source = 'oidc' ORDER BY id"))
    assert [r[0] for r in rows] == [f"Abc-{tag}", f"abc-{tag}", f"sub-admin-{tag}"]


def test_unique_external_identity():
    from sqlalchemy.exc import IntegrityError

    from app.models.models import User

    _run(prepare_fresh(_engine()))

    async def dup(s):
        s.add(User(username="t10_a", hashed_password="x", auth_source="ldap", external_issuer="ldap",
                   external_id="guid-1"))
        await s.flush()
        s.add(User(username="t10_b", hashed_password="x", auth_source="ldap", external_issuer="ldap",
                   external_id="guid-1"))
        await s.flush()

    with pytest.raises(IntegrityError):
        _run(_with_session(dup))


# ---------------------------------------------------------------------------------------------
# Nr. 5 Ende-zu-Ende OIDC mit Lifespan: Start -> Callback -> /auth/me
# ---------------------------------------------------------------------------------------------
class _IdP:
    def __init__(self):
        from joserfc.jwk import RSAKey

        self.key = RSAKey.generate_key(2048, parameters={"kid": "t10-rsa"})
        self.nonce = None
        self.sub = f"t10-sub-{uuid.uuid4().hex[:8]}"
        self.username = f"t10_{uuid.uuid4().hex[:8]}"
        self.token_requests = 0

    def metadata(self):
        return {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
                "jwks_uri": ISS + "/certs", "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"]}

    def id_token(self):
        from joserfc import jwt

        now = int(time.time())
        claims = {"iss": ISS, "sub": self.sub, "aud": "pdns", "exp": now + 300, "iat": now, "nonce": self.nonce,
                  "preferred_username": self.username, "name": "T10 Nutzer"}
        return jwt.encode({"alg": "RS256", "kid": self.key.kid}, claims, self.key, algorithms=["RS256"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self.metadata())
        if path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if path.endswith("/token"):
            self.token_requests += 1
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["authorization_code"] and body["code"] == ["code-t10"]
            assert body.get("code_verifier")
            return httpx.Response(200, json={"access_token": "at-t10", "token_type": "Bearer",
                                             "id_token": self.id_token()})
        return httpx.Response(404, json={})


def test_oidc_end_to_end_with_lifespan():
    from fastapi.testclient import TestClient

    from app.core.database import async_session
    from app.main import app
    from app.services import sso_oidc
    from app.services.system_settings import set_settings

    _run(prepare_fresh(_engine()))
    idp = _IdP()
    sso_oidc.reset_for_tests()
    sso_oidc.set_transport_for_tests(httpx.MockTransport(idp.handler))
    keys = {"app_base_url": "https://testserver", "oidc_enabled": "true", "oidc_issuer": ISS,
            "oidc_client_id": "pdns", "oidc_client_secret": "t10-client-secret", "oidc_jit_enabled": "true",
            "oidc_jit_allow_any_account": "true", "oidc_use_userinfo": "false"}
    try:
        with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:   # Lifespan an

            async def seed():
                async with async_session() as s:
                    await set_settings(s, keys)
                    await s.commit()

            _run(seed())
            r = c.get("/api/v1/auth/oidc/start")
            assert r.status_code == 302, r.text
            q = parse_qs(urlsplit(r.headers["location"]).query)
            idp.nonce = q["nonce"][0]
            r = c.get("/api/v1/auth/oidc/callback", params={"code": "code-t10", "state": q["state"][0]})
            assert r.status_code == 303 and r.headers["location"] == "/", r.headers.get("location")
            me = c.get("/api/v1/auth/me")
            assert me.status_code == 200, me.text
            assert me.json()["auth_source"] == "oidc" and me.json()["username"] == idp.username
            assert idp.token_requests == 1
        stored = _run(_q("SELECT value FROM system_settings WHERE `key` = 'oidc_client_secret'"))
        assert stored and stored[0][0].startswith("enc:v1:")   # Secret verschluesselt (F5)
        login = _run(_q("SELECT details FROM audit_logs WHERE action = 'LOGIN' ORDER BY id DESC LIMIT 1"))
        details = login[0][0] if isinstance(login[0][0], dict) else json.loads(login[0][0])
        assert details["method"] == "oidc" and details["jit"] is True and details["issuer"] == ISS
    finally:
        sso_oidc.reset_for_tests()

        async def cleanup():
            async with _engine().begin() as conn:
                await conn.execute(text("DELETE FROM users WHERE username LIKE 't10\\_%'"))
                await conn.execute(text("DELETE FROM system_settings WHERE `key` LIKE 'oidc\\_%'"))

        _run(cleanup())
