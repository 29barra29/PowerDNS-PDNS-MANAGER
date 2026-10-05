"""F5 §9.2 test_secrets_integration (+ F8 Nr. 16, A.8-Downgrade): gegen eine echte MariaDB.

Gate ``dbutil.requires_db`` (``CI=true`` oder ``RUN_DB_TESTS=1`` und ``DATABASE_URL``; lokal
``scripts/dev/test-db.sh``). Fixture ``fresh_db``: leere Testdatenbank mit 3.0-Schema, einmal je Modul.
Testzeilen heissen ``f5test*`` und werden nach jedem Test entfernt; benutzt wird nur der globale
Testschluessel aus ``conftest`` (Fremdschluessel nur fuer gezielt unlesbare Werte, die der Test wieder loescht).
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core import secrets as secret_store
from app.core.secrets import PREFIX, SecretBox, parse_key
from dbutil import requires_db

pytestmark = requires_db

TEST_KEY = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="
SECOND_KEY = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="
FOREIGN = SecretBox(parse_key(SECOND_KEY, source_name="test"))
F5_SETTING_KEYS = ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from_email", "smtp_from_name",
                   "smtp_encryption", "smtp_enabled", "app_name", "app_base_url", "f5test_plain")

SECRET_COLUMN_SQL = (
    "SELECT api_key AS v FROM server_configs UNION ALL SELECT secret FROM webhooks UNION ALL SELECT url FROM webhooks "
    "UNION ALL SELECT totp_secret FROM users UNION ALL SELECT totp_pending_secret FROM users UNION ALL "
    "SELECT `value` FROM system_settings WHERE `key` IN ('smtp_password','captcha_secret_key','oidc_client_secret',"
    "'ldap_bind_password','metrics_token')"
)


def _cfg(tmp_path) -> secret_store.KeyConfig:
    return secret_store.KeyConfig(env_primary=TEST_KEY, env_previous=(), file_path=tmp_path / ".secret_key",
                                  file_explicit=False)


async def _exec(engine, sql: str, params: dict | None = None) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params or {})


async def _rows(engine, sql: str, params: dict | None = None) -> list:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).all()


async def _val(engine, sql: str, params: dict | None = None):
    rows = await _rows(engine, sql, params)
    return rows[0][0] if rows else None


async def _cleanup(engine) -> None:
    await _exec(engine, "DELETE FROM server_configs WHERE name LIKE 'f5test%'")
    await _exec(engine, "DELETE FROM webhooks WHERE name LIKE 'f5test%'")
    await _exec(engine, "DELETE FROM panel_tokens WHERE name LIKE 'f5test%'")
    await _exec(engine, "DELETE FROM users WHERE username LIKE 'f5test%'")
    keys = ", ".join(f"'{k}'" for k in F5_SETTING_KEYS)
    await _exec(engine, f"DELETE FROM system_settings WHERE `key` IN ({keys})")


@pytest.fixture(autouse=True)
def _f5_rows(fresh_db):
    import asyncio

    secret_store.reset_for_tests()
    asyncio.run(_cleanup(fresh_db))
    yield fresh_db
    secret_store.reset_for_tests()
    asyncio.run(_cleanup(fresh_db))
    asyncio.run(fresh_db.dispose(close=False))


async def _insert_user(engine, name: str, *, role: str = "user", totp: str | None = None) -> int:
    await _exec(engine, "INSERT INTO users (username, hashed_password, role, is_active, totp_enabled, totp_secret, "
                        "must_change_password, auth_source) VALUES (:n, 'x', :r, 1, :te, :t, 0, 'local')",
                {"n": name, "r": role, "te": 1 if totp else 0, "t": totp})
    return int(await _val(engine, "SELECT id FROM users WHERE username = :n", {"n": name}))


async def _insert_server(engine, name: str, raw_key: str) -> None:
    await _exec(engine, "INSERT INTO server_configs (name, display_name, url, api_key, is_active, allow_writes, "
                        "sort_order) VALUES (:n, :n, 'http://127.0.0.1:9', :k, 1, 1, 99)", {"n": name, "k": raw_key})


async def _upsert_setting(engine, key: str, value: str) -> None:
    await _exec(engine, "INSERT INTO system_settings (`key`, `value`) VALUES (:k, :v) "
                        "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)", {"k": key, "v": value})


async def _secret_values(engine) -> list[str]:
    return [r[0] for r in await _rows(engine, SECRET_COLUMN_SQL) if r[0]]


# ---------------------------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------------------------


async def test_init_db_widens_columns(fresh_db):
    from app.core.database import MIGRATION_ERRORS, init_db

    narrow = (("users", "totp_secret", "VARCHAR(64) NULL", 64),
              ("server_configs", "api_key", "VARCHAR(500) NOT NULL", 500),
              ("webhooks", "secret", "VARCHAR(256) NOT NULL", 256))
    narrowed = []
    for table, col, ddl, width in narrow:
        longest = await _val(fresh_db, f"SELECT COALESCE(MAX(CHAR_LENGTH({col})), 0) FROM {table}")
        if int(longest) <= width:  # geteilte DB kann schon laengere Chiffretexte enthalten
            await _exec(fresh_db, f"ALTER TABLE {table} MODIFY COLUMN {col} {ddl}")
            narrowed.append(table)
    MIGRATION_ERRORS.clear()
    await init_db()
    types = {(r[0], r[1]): (r[2], r[3]) for r in await _rows(fresh_db, (
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME IN ('api_key','secret','url','totp_secret',"
        "'totp_pending_secret') AND TABLE_NAME IN ('users','server_configs','webhooks')"))}
    assert types[("server_configs", "api_key")][0] == "text"
    assert types[("webhooks", "secret")][0] == "text" and types[("webhooks", "url")][0] == "text"
    assert types[("users", "totp_secret")] == ("varchar", 512)
    assert types[("users", "totp_pending_secret")] == ("varchar", 512)
    await init_db()
    assert MIGRATION_ERRORS == []
    assert narrowed  # in der frischen Test-DB lassen sich alle drei verengen


# ---------------------------------------------------------------------------------------------
# 2./3. Startmigration und Settings-Helfer
# ---------------------------------------------------------------------------------------------


async def test_startup_migration_end_to_end(fresh_db, tmp_path):
    from app.core.database import async_session
    from app.models.models import ServerConfig, User, Webhook

    uid = await _insert_user(fresh_db, "f5test_user", totp="JBSWY3DPEHPK3PXP")
    await _insert_server(fresh_db, "f5test_srv1", "klartext-key-1")
    await _exec(fresh_db, "INSERT INTO webhooks (user_id, name, url, secret, events, is_active, created_at, scope, "
                          "consecutive_failures) VALUES (:u, 'f5test_hook', 'https://hooks.example/x', 'whsec-klar', "
                          "'[]', 1, UTC_TIMESTAMP(), 'own', 0)", {"u": uid})
    await _upsert_setting(fresh_db, "smtp_password", "smtp-klartext")

    report = await secret_store.init_secrets(fresh_db, _cfg(tmp_path))
    assert report.mode == "encrypted" and report.key_source == "env"
    for field in ("server_configs.api_key", "webhooks.url", "webhooks.secret", "users.totp_secret",
                  "system_settings.smtp_password"):
        assert report.migrated.get(field, 0) >= 1, field
    raw = await _val(fresh_db, "SELECT api_key FROM server_configs WHERE name = 'f5test_srv1'")
    assert raw.startswith(PREFIX)
    assert all(v.startswith(PREFIX) for v in await _secret_values(fresh_db))

    async with async_session() as s:
        srv = await s.scalar(select(ServerConfig).where(ServerConfig.name == "f5test_srv1"))
        hook = await s.scalar(select(Webhook).where(Webhook.name == "f5test_hook"))
        user = await s.scalar(select(User).where(User.id == uid))
        assert srv.api_key == "klartext-key-1" and hook.secret == "whsec-klar"
        assert hook.url == "https://hooks.example/x" and user.totp_secret == "JBSWY3DPEHPK3PXP"

    before = await _secret_values(fresh_db)
    again = await secret_store.init_secrets(fresh_db, _cfg(tmp_path))
    assert again.migrated == {} and again.rotated == {}
    assert await _secret_values(fresh_db) == before


async def test_system_settings_helpers_async(fresh_db):
    from app.core.database import async_session
    from app.services.system_settings import get_settings, set_setting

    secret_store.configure_for_tests(TEST_KEY)
    async with async_session() as db:
        await set_setting(db, "smtp_password", "x-geheim")
        await set_setting(db, "f5test_plain", "y")
        await db.commit()
    assert (await _val(fresh_db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'")).startswith(PREFIX)
    assert await _val(fresh_db, "SELECT `value` FROM system_settings WHERE `key` = 'f5test_plain'") == "y"
    async with async_session() as db:
        assert await get_settings(db, ["smtp_password", "f5test_plain"]) == {"smtp_password": "x-geheim",
                                                                             "f5test_plain": "y"}


# ---------------------------------------------------------------------------------------------
# 4. Lifespan laedt Server mit entschluesseltem Key
# ---------------------------------------------------------------------------------------------


def test_lifespan_loads_server_with_decrypted_key(fresh_db):
    import asyncio

    from app.main import app
    from app.services.pdns_client import pdns_manager

    asyncio.run(_insert_server(fresh_db, "f5test_srv", "f5-lifespan-key"))
    try:
        with TestClient(app):
            assert pdns_manager.get_client("f5test_srv").api_key == "f5-lifespan-key"
        raw = asyncio.run(_val(fresh_db, "SELECT api_key FROM server_configs WHERE name = 'f5test_srv'"))
        assert raw.startswith(PREFIX)
    finally:
        pdns_manager.remove_server("f5test_srv")


# ---------------------------------------------------------------------------------------------
# 5./6. CLI status und reset-unreadable
# ---------------------------------------------------------------------------------------------


async def test_cli_status_json(fresh_db, capsys):
    from app.cli import secrets as cli

    await _insert_server(fresh_db, "f5test_srv_status", secret_store.SecretBox(
        parse_key(TEST_KEY, source_name="t")).encrypt("status-key"))
    assert await cli.main(["status", "--json"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    data = json.loads(out)
    assert {c["id"] for c in data["columns"]} >= {"server_configs.api_key", "webhooks.url",
                                                   "system_settings.metrics_token"}
    assert data["key_source"] == "env" and "status-key" not in out and PREFIX not in out


async def test_cli_reset_unreadable_dry_run_and_yes(fresh_db, capsys):
    from app.cli import secrets as cli

    foreign = FOREIGN.encrypt("fremder-key")
    await _insert_server(fresh_db, "f5test_srv_foreign", foreign)
    assert await cli.main(["reset-unreadable"]) == cli.EXIT_OK
    assert "f5test_srv_foreign" in capsys.readouterr().out
    assert await _val(fresh_db, "SELECT api_key FROM server_configs WHERE name = 'f5test_srv_foreign'") == foreign

    assert await cli.main(["reset-unreadable", "--yes"]) == cli.EXIT_OK
    assert await _val(fresh_db, "SELECT api_key FROM server_configs WHERE name = 'f5test_srv_foreign'") == ""
    details = await _val(fresh_db, "SELECT details FROM audit_logs WHERE action = 'SECRETS_RESET_UNREADABLE' "
                                   "ORDER BY id DESC LIMIT 1")
    details = json.loads(details) if isinstance(details, str) else details
    assert "f5test_srv_foreign" in details["servers"] and details["key_generated"] is False
    assert await cli.main(["reset-unreadable", "--yes"]) == cli.EXIT_NOTHING_TO_DO


# ---------------------------------------------------------------------------------------------
# 7. prepare-downgrade und erneutes Upgrade (A.8 [D6])
# ---------------------------------------------------------------------------------------------


async def _token(engine, uid: int, name: str, **cols) -> None:
    base = {"scope_zones": None, "permission": "manage", "expires_at": None, "allow_admin": 0}
    base.update(cols)
    await _exec(engine, "INSERT INTO panel_tokens (user_id, name, token_prefix, token_hash, created_at, is_active, "
                        "scope_zones, permission, expires_at, allow_admin) VALUES (:u, :n, 'dnsmgr_', :h, "
                        "UTC_TIMESTAMP(), 1, :scope_zones, :permission, :expires_at, :allow_admin)",
                {"u": uid, "n": name, "h": f"f5test-{name}", **base})


async def test_cli_prepare_downgrade_and_reupgrade(fresh_db, tmp_path, capsys):
    from app.cli import secrets as cli
    from app.core.database import init_db
    from app.core.timeutil import utcnow_naive

    box = SecretBox(parse_key(TEST_KEY, source_name="t"))
    admin_id = await _insert_user(fresh_db, "f5test_admin", role="admin", totp=box.encrypt("JBSWY3DPEHPK3PXP"))
    user_id = await _insert_user(fresh_db, "f5test_alice")
    await _insert_server(fresh_db, "f5test_srv_dg", box.encrypt("downgrade-key"))
    await _upsert_setting(fresh_db, "smtp_password", box.encrypt("smtp-dg"))
    await _token(fresh_db, user_id, "f5test_expired", expires_at=utcnow_naive() - timedelta(days=1))
    await _token(fresh_db, user_id, "f5test_ok")
    await _token(fresh_db, user_id, "f5test_zones", scope_zones='["alpha.example."]')
    await _token(fresh_db, admin_id, "f5test_admin_noflag", allow_admin=0)
    await _token(fresh_db, admin_id, "f5test_admin_flag", allow_admin=1)
    await secret_store.init_secrets(fresh_db, _cfg(tmp_path))  # Rest der DB ebenfalls verschluesselt
    assert await _val(fresh_db, "SELECT COUNT(*) FROM system_settings WHERE `key` = :k", {"k": cli.F14_MARKER}) == 1

    assert await cli.main(["prepare-downgrade", "--yes"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "downgrade-key" not in out and "smtp-dg" not in out

    assert not any(v.startswith(PREFIX) for v in await _secret_values(fresh_db))
    assert await _val(fresh_db, "SELECT api_key FROM server_configs WHERE name = 'f5test_srv_dg'") == "downgrade-key"
    assert await _val(fresh_db, "SELECT totp_secret FROM users WHERE id = :i", {"i": admin_id}) == "JBSWY3DPEHPK3PXP"
    state = {r[0]: (r[1], r[2]) for r in await _rows(
        fresh_db, "SELECT name, is_active, revoked_at FROM panel_tokens WHERE name LIKE 'f5test%'")}
    assert {n: a for n, (a, _) in state.items()} == {"f5test_expired": 0, "f5test_ok": 1, "f5test_zones": 0,
                                                     "f5test_admin_noflag": 0, "f5test_admin_flag": 1}
    assert all(r is None for _, r in state.values())
    assert await _val(fresh_db, "SELECT COUNT(*) FROM system_settings WHERE `key` IN (:a, :b)",
                      {"a": cli.F14_MARKER, "b": cli.AUDIT_BACKFILL_MARKER}) == 0
    details = await _val(fresh_db, "SELECT details FROM audit_logs WHERE action = 'DOWNGRADE_PREPARED' "
                                   "ORDER BY id DESC LIMIT 1")
    details = json.loads(details) if isinstance(details, str) else details
    assert details["tokens_deactivated"] >= 3 and details["decrypted"]["server_configs.api_key"] >= 1

    # Erneuter 3.0-Start: Migrationen laufen wieder (fail-closed), Geheimnisse werden wieder verschluesselt
    await init_db()
    state = {r[0]: (r[1], r[2]) for r in await _rows(
        fresh_db, "SELECT name, is_active, revoked_at FROM panel_tokens WHERE name LIKE 'f5test%'")}
    for name in ("f5test_expired", "f5test_zones", "f5test_admin_noflag"):
        assert state[name][0] == 0 and state[name][1] is not None, name
    assert state["f5test_ok"] == (1, None) and state["f5test_admin_flag"] == (1, None)
    assert await _val(fresh_db, "SELECT COUNT(*) FROM system_settings WHERE `key` IN (:a, :b)",
                      {"a": cli.F14_MARKER, "b": cli.AUDIT_BACKFILL_MARKER}) == 2
    report = await secret_store.init_secrets(fresh_db, _cfg(tmp_path))
    assert report.migrated.get("server_configs.api_key", 0) >= 1
    assert all(v.startswith(PREFIX) for v in await _secret_values(fresh_db))


# ---------------------------------------------------------------------------------------------
# 8./9. HTTP gegen die echte DB: App-Info (F8 Nr. 16) und SMTP-Roundtrip
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def admin_client():
    from app.core.auth import get_admin_session_user
    from app.main import app
    from app.models.models import User

    admin = User(id=999001, username="f5test_http_admin", role="admin", is_active=True, hashed_password="x",
                 preferred_language="de")
    app.dependency_overrides[get_admin_session_user] = lambda: admin
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_admin_session_user, None)


def test_app_info_clear_base_url_db(fresh_db, admin_client):
    import asyncio

    c = admin_client
    assert c.put("/api/v1/settings/app-info", json={"app_name": "F5", "app_base_url": "https://a.example"}).status_code == 200
    assert c.get("/api/v1/settings/admin-info").json()["app_base_url"] == "https://a.example"
    assert c.put("/api/v1/settings/app-info", json={"app_name": "F5", "app_base_url": ""}).status_code == 200
    assert c.get("/api/v1/settings/admin-info").json()["app_base_url"] is None
    details = asyncio.run(_val(fresh_db, "SELECT details FROM audit_logs WHERE action = 'APP_INFO_UPDATE' "
                                         "AND user_id = 999001 ORDER BY id DESC LIMIT 1"))
    details = json.loads(details) if isinstance(details, str) else details
    assert details == {"changed": ["app_base_url"], "logo_files_removed": 0}


def test_smtp_roundtrip_db(fresh_db, admin_client):
    import asyncio

    c = admin_client
    body = {"host": "mail.example.com", "port": 587, "username": "relay", "password": "smtp-db-pw",
            "from_email": "dns@example.com", "from_name": "DNS", "encryption": "starttls", "enabled": True}
    assert c.put("/api/v1/settings/smtp", json=body).status_code == 200
    raw = asyncio.run(_val(fresh_db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'"))
    assert raw.startswith(PREFIX)
    got = c.get("/api/v1/settings/smtp").json()
    assert got["password_set"] is True and got["password"] == "••••••••" and got["password_unreadable"] is False

    assert c.put("/api/v1/settings/smtp", json={**body, "password": "••••••••", "from_name": "X"}).status_code == 200
    assert asyncio.run(_val(fresh_db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'")) == raw

    r = c.put("/api/v1/settings/smtp", json={**body, "password": None, "host": "evil.example.net"})
    assert r.status_code == 400 and r.json()["code"] == "secret_reentry_required"
    assert asyncio.run(_val(fresh_db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_host'")) == "mail.example.com"
