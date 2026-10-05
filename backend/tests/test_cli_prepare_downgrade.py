"""CLI ``python -m app.cli.secrets`` (F5 §5.15, Bauplan A.8 [D6]) gegen SQLite – ohne MariaDB.

Kernlogik (synchron, ``Connection``) direkt; die Kommandos ueber ``main()`` mit einer Async-Huelle um den
SQLite-Engine (``_get_engine`` gepatcht) und abgefangenem Audit (``_write_audit``).
Die Wiederholung der F14-Migration beim erneuten Upgrade prueft ``test_secrets_integration.py`` (MariaDB).
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.cli import secrets as cli
from app.core import secrets as secret_store
from app.core.secrets import PREFIX, SecretBox, parse_key

KEY_A = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="
KEY_B = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="
BOX_A = SecretBox(parse_key(KEY_A, source_name="t"))
BOX_B = SecretBox(parse_key(KEY_B, source_name="t"))
NOW = datetime(2026, 10, 6, 12, 0, 0)


# ---------------------------------------------------------------------------------------------
# Testdatenbank
# ---------------------------------------------------------------------------------------------


def _make_db():
    from app.models.models import AuditLog, PanelToken, ServerConfig, SystemSetting, User, Webhook

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, ServerConfig, SystemSetting, AuditLog, Webhook, PanelToken):
        model.__table__.create(eng)
    return eng


def _x(eng, sql, params=None):
    with eng.begin() as conn:
        conn.execute(text(sql), params or {})


def _q(eng, sql, params=None):
    with eng.connect() as conn:
        return conn.execute(text(sql), params or {}).all()


def _v(eng, sql, params=None):
    rows = _q(eng, sql, params)
    return rows[0][0] if rows else None


def _user(eng, uid, name, role="user", *, totp=None, pending=None, auth_source="local"):
    _x(eng, "INSERT INTO users (id, username, hashed_password, role, is_active, totp_enabled, totp_secret, "
            "totp_pending_secret, must_change_password, auth_source) VALUES (:i, :n, 'x', :r, 1, :te, :t, :p, 0, :a)",
       {"i": uid, "n": name, "r": role, "te": 1 if totp else 0, "t": totp, "p": pending, "a": auth_source})


def _server(eng, sid, name, raw):
    _x(eng, "INSERT INTO server_configs (id, name, url, api_key, is_active, allow_writes, sort_order) "
            "VALUES (:i, :n, 'http://x', :k, 1, 1, 0)", {"i": sid, "n": name, "k": raw})


def _webhook(eng, wid, uid, name, url_raw, secret_raw):
    _x(eng, "INSERT INTO webhooks (id, user_id, name, url, secret, events, is_active, created_at, scope, "
            "consecutive_failures) VALUES (:i, :u, :n, :url, :s, '[]', 1, CURRENT_TIMESTAMP, 'own', 0)",
       {"i": wid, "u": uid, "n": name, "url": url_raw, "s": secret_raw})


def _setting(eng, key, value):
    _x(eng, "INSERT INTO system_settings (`key`, `value`) VALUES (:k, :v)", {"k": key, "v": value})


def _token(eng, tid, uid, name, *, active=1, scope="null", permission="manage", expires=None, allow_admin=0,
           revoked=None):
    _x(eng, "INSERT INTO panel_tokens (id, user_id, name, token_prefix, token_hash, created_at, is_active, "
            "scope_zones, permission, expires_at, allow_admin, revoked_at) VALUES "
            "(:i, :u, :n, 'dnsmgr_', :h, CURRENT_TIMESTAMP, :a, :s, :p, :e, :aa, :r)",
       {"i": tid, "u": uid, "n": name, "h": f"hash-{tid}", "a": active, "s": scope, "p": permission,
        "e": expires, "aa": allow_admin, "r": revoked})


@pytest.fixture
def db():
    eng = _make_db()
    _user(eng, 1, "admin", "admin", totp=BOX_A.encrypt("JBSWY3DPEHPK3PXP"))
    _user(eng, 2, "alice", totp=BOX_B.encrypt("KRSXG5CTMVRXEZLU"), pending=BOX_B.encrypt("PENDING"))
    _user(eng, 3, "bob", pending=BOX_B.encrypt("NUR-PENDING"))
    _user(eng, 4, "sso-user", auth_source="oidc")
    _server(eng, 1, "ns1", BOX_A.encrypt("pdns-key-1"))
    _server(eng, 2, "ns-fremd", BOX_B.encrypt("pdns-key-2"))
    _server(eng, 3, "ns-klar", "pdns-key-3")
    _webhook(eng, 1, 2, "slack", BOX_A.encrypt("https://hooks.example/1"), BOX_A.encrypt("whsec-1"))
    _webhook(eng, 2, 2, "fremd", BOX_B.encrypt("https://hooks.example/2"), BOX_B.encrypt("whsec-2"))
    _setting(eng, "smtp_password", BOX_A.encrypt("smtp-pw"))
    _setting(eng, "captcha_secret_key", BOX_B.encrypt("cap-secret"))
    _setting(eng, "smtp_host", "mail.example.com")
    _setting(eng, cli.F14_MARKER, "2026-10-01T00:00:00+00:00")
    _setting(eng, cli.AUDIT_BACKFILL_MARKER, "2026-10-01T00:00:00+00:00")
    yield eng
    eng.dispose()


@pytest.fixture
def tokens_db():
    eng = _make_db()
    _user(eng, 1, "admin", "admin")
    _user(eng, 2, "alice")
    _token(eng, 1, 1, "admin-ok", allow_admin=1)
    _token(eng, 2, 1, "admin-ohne-freigabe", allow_admin=0)
    _token(eng, 3, 2, "alice-ok")
    _token(eng, 4, 2, "alice-zonen", scope='["alpha.example."]')
    _token(eng, 5, 2, "alice-lesen", permission="read")
    _token(eng, 6, 2, "alice-abgelaufen", expires=NOW - timedelta(days=1))
    _token(eng, 7, 2, "alice-laeuft-ab", expires=NOW + timedelta(days=30))
    _token(eng, 8, 2, "alice-pausiert", active=0, scope='["alpha.example."]')
    _token(eng, 9, 2, "alice-widerrufen-aber-aktiv", revoked=NOW - timedelta(days=2))
    _token(eng, 10, 2, "alice-sql-null", scope=None)
    yield eng
    eng.dispose()


class _AsyncConn:
    def __init__(self, conn):
        self._conn = conn

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._conn, *args, **kwargs)

    async def rollback(self):
        self._conn.rollback()


class _AsyncEngine:
    """Async-Huelle um einen synchronen Engine (begin/connect/dispose wie AsyncEngine)."""

    def __init__(self, eng):
        self.eng = eng
        self.disposed = 0

    @asynccontextmanager
    async def begin(self):
        with self.eng.begin() as conn:
            yield _AsyncConn(conn)

    @asynccontextmanager
    async def connect(self):
        with self.eng.connect() as conn:
            yield _AsyncConn(conn)

    async def dispose(self):
        self.disposed += 1


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    """Fuehrt ``cli.main(argv)`` gegen einen SQLite-Engine aus; Audits landen in ``audits``."""
    audits: list[tuple[str, dict]] = []

    async def _audit(action, details):
        audits.append((action, details))
        return len(audits)

    monkeypatch.setattr(cli, "_write_audit", _audit)
    cfg_holder = {"cfg": secret_store.KeyConfig(env_primary=KEY_A, env_previous=(), file_path=tmp_path / ".secret_key",
                                                file_explicit=False)}
    monkeypatch.setattr(secret_store.KeyConfig, "from_settings", classmethod(lambda cls, s=None: cfg_holder["cfg"]))

    def _run(eng, *argv):
        monkeypatch.setattr(cli, "_get_engine", lambda: _AsyncEngine(eng))
        return asyncio.run(cli.main(list(argv)))

    _run.audits = audits
    _run.cfg = cfg_holder
    return _run


# ---------------------------------------------------------------------------------------------
# Token-Haertung
# ---------------------------------------------------------------------------------------------


def test_plan_token_hardening_reasons(tokens_db):
    with tokens_db.connect() as conn:
        actions = cli.plan_token_hardening(conn, now=NOW)
    got = {a.name: a.reasons for a in actions}
    assert got == {
        "admin-ohne-freigabe": ("admin_without_allow_admin",),
        "alice-zonen": ("zone_scope",),
        "alice-lesen": ("read_only",),
        "alice-abgelaufen": ("expired",),
        "alice-laeuft-ab": ("expires",),
        "alice-widerrufen-aber-aktiv": ("revoked",),
    }
    assert {a.owner for a in actions} == {"admin", "alice"}


def test_apply_token_hardening_only_touches_planned(tokens_db):
    with tokens_db.begin() as conn:
        n = cli.apply_token_hardening(conn, cli.plan_token_hardening(conn, now=NOW))
    assert n == 6
    active = {r[0] for r in _q(tokens_db, "SELECT name FROM panel_tokens WHERE is_active = 1")}
    assert active == {"admin-ok", "alice-ok", "alice-sql-null"}
    # revoked_at wird nicht gesetzt (das macht die F14-Migration beim erneuten Upgrade)
    assert _v(tokens_db, "SELECT revoked_at FROM panel_tokens WHERE name = 'alice-abgelaufen'") is None


def test_token_hardening_without_30_columns_is_noop():
    eng = create_engine("sqlite://")
    _x(eng, "CREATE TABLE panel_tokens (id INTEGER PRIMARY KEY, user_id INT, name TEXT, is_active INT)")
    _x(eng, "CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, username TEXT)")
    with eng.connect() as conn:
        assert cli.plan_token_hardening(conn) == []
        assert cli.count_external_accounts(conn) == 0


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("null", None), ('["a."]', ["a."]), (b'["a."]', ["a."]), (["b."], ["b."]), ("kaputt", ["?"]),
    ('{"x": 1}', ["?"]),
])
def test_scope_list(raw, expected):
    assert cli._scope_list(raw) == expected


# ---------------------------------------------------------------------------------------------
# Entschluesseln / Marker / Plan
# ---------------------------------------------------------------------------------------------


def test_plan_and_apply_decrypt(db):
    with db.connect() as conn:
        plan = cli.plan_decrypt(secret_store.scan_values(conn), BOX_A)
    assert plan.counts == {"server_configs.api_key": 1, "webhooks.url": 1, "webhooks.secret": 1,
                           "users.totp_secret": 1, "system_settings.smtp_password": 1}
    unread = {(v.field, v.name) for v in plan.unreadable}
    assert unread == {("server_configs.api_key", "ns-fremd"), ("webhooks.url", "fremd"), ("webhooks.secret", "fremd"),
                      ("users.totp_secret", "alice"), ("users.totp_pending_secret", "alice"),
                      ("users.totp_pending_secret", "bob"), ("system_settings.captcha_secret_key", "captcha_secret_key")}
    with db.begin() as conn:
        done = cli.apply_decrypt(conn, plan, BOX_A)
    assert done == plan.counts
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 1") == "pdns-key-1"
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 3") == "pdns-key-3"
    assert _v(db, "SELECT url FROM webhooks WHERE id = 1") == "https://hooks.example/1"
    assert _v(db, "SELECT totp_secret FROM users WHERE id = 1") == "JBSWY3DPEHPK3PXP"
    assert _v(db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'") == "smtp-pw"
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 2").startswith(PREFIX)  # unlesbar bleibt


def test_classify_without_box():
    assert cli.classify(None, None) == "empty" and cli.classify(None, "") == "empty"
    assert cli.classify(None, "klar") == "plaintext"
    assert cli.classify(None, BOX_A.encrypt("x")) == "unreadable"
    assert cli.classify(BOX_A, BOX_A.encrypt("x")) == "encrypted"


def test_plan_prepare_downgrade_collects_everything(db):
    _token(db, 1, 2, "alice-zonen", scope='["a."]')
    with db.connect() as conn:
        plan = cli.plan_prepare_downgrade(conn, BOX_A, now=NOW)
    assert plan.markers == sorted([cli.AUDIT_BACKFILL_MARKER, cli.F14_MARKER])
    assert [a.name for a in plan.tokens] == ["alice-zonen"] and plan.token_reasons() == {"zone_scope": 1}
    assert plan.external_accounts == 1


# ---------------------------------------------------------------------------------------------
# Kommandos ueber main()
# ---------------------------------------------------------------------------------------------


def _all_secrets_plain(eng) -> bool:
    vals = [r[0] for r in _q(eng, "SELECT api_key FROM server_configs")]
    vals += [v for r in _q(eng, "SELECT url, secret FROM webhooks") for v in r]
    vals += [v for r in _q(eng, "SELECT totp_secret, totp_pending_secret FROM users") for v in r]
    vals += [r[0] for r in _q(eng, "SELECT `value` FROM system_settings WHERE `key` IN ('smtp_password', "
                                    "'captcha_secret_key')")]
    return not any(isinstance(v, str) and v.startswith(PREFIX) for v in vals)


def _readable_only_db():
    eng = _make_db()
    _user(eng, 1, "admin", "admin", totp=BOX_A.encrypt("JBSWY3DPEHPK3PXP"))
    _user(eng, 2, "alice")
    _server(eng, 1, "ns1", BOX_A.encrypt("pdns-key-1"))
    _webhook(eng, 1, 2, "slack", BOX_A.encrypt("https://hooks.example/1"), BOX_A.encrypt("whsec-1"))
    _setting(eng, "smtp_password", BOX_A.encrypt("smtp-pw"))
    _setting(eng, cli.F14_MARKER, "x")
    _token(eng, 1, 1, "admin-ok", allow_admin=1)
    _token(eng, 2, 2, "alice-abgelaufen", expires=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    return eng


def test_prepare_downgrade_dry_run_changes_nothing(run_cli, capsys):
    eng = _readable_only_db()
    before = _q(eng, "SELECT api_key FROM server_configs")
    assert run_cli(eng, "prepare-downgrade") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Trockenlauf" in out and "alice-abgelaufen" in out and "expired" in out
    assert _q(eng, "SELECT api_key FROM server_configs") == before
    assert _v(eng, "SELECT is_active FROM panel_tokens WHERE id = 2") == 1
    assert run_cli.audits == []
    assert "pdns-key-1" not in out and "smtp-pw" not in out


def test_prepare_downgrade_yes(run_cli, capsys):
    eng = _readable_only_db()
    assert run_cli(eng, "prepare-downgrade", "--yes") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert _all_secrets_plain(eng)
    assert _v(eng, "SELECT api_key FROM server_configs WHERE id = 1") == "pdns-key-1"
    assert _v(eng, "SELECT is_active FROM panel_tokens WHERE id = 2") == 0
    assert _v(eng, "SELECT is_active FROM panel_tokens WHERE id = 1") == 1
    assert _v(eng, "SELECT COUNT(*) FROM system_settings WHERE `key` = :k", {"k": cli.F14_MARKER}) == 0
    assert [a for a, _ in run_cli.audits] == ["DOWNGRADE_PREPARED"]
    details = run_cli.audits[0][1]
    assert details["decrypted"] == {"server_configs.api_key": 1, "webhooks.url": 1, "webhooks.secret": 1,
                                    "users.totp_secret": 1, "system_settings.smtp_password": 1}
    assert details["tokens_deactivated"] == 1 and details["token_reasons"] == {"expired": 1}
    assert details["markers_removed"] == [cli.F14_MARKER] and details["skipped_unreadable"] == 0
    assert "NICHT mehr mit 3.x starten" in out
    for secret in ("pdns-key-1", "smtp-pw", "whsec-1", "JBSWY3DPEHPK3PXP"):
        assert secret not in out and secret not in json.dumps(details)


def test_prepare_downgrade_unreadable_needs_force(run_cli, db, capsys):
    before = _q(db, "SELECT api_key FROM server_configs ORDER BY id")
    assert run_cli(db, "prepare-downgrade", "--yes") == cli.EXIT_UNREADABLE
    assert _q(db, "SELECT api_key FROM server_configs ORDER BY id") == before
    assert run_cli.audits == []
    assert "ns-fremd" in capsys.readouterr().out

    assert run_cli(db, "prepare-downgrade", "--yes", "--force") == cli.EXIT_OK
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 1") == "pdns-key-1"
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 2").startswith(PREFIX)
    details = run_cli.audits[-1][1]
    assert details["skipped_unreadable"] == 7 and details["external_accounts"] == 1
    assert sorted(details["markers_removed"]) == sorted([cli.F14_MARKER, cli.AUDIT_BACKFILL_MARKER])


def test_prepare_downgrade_without_key(run_cli, tmp_path, capsys):
    eng = _readable_only_db()
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=tmp_path / "fehlt",
                                                file_explicit=False)
    assert run_cli(eng, "prepare-downgrade", "--yes") == cli.EXIT_ERROR
    assert "Kein Schluessel verfuegbar" in capsys.readouterr().err
    assert _v(eng, "SELECT is_active FROM panel_tokens WHERE id = 2") == 1


def test_prepare_downgrade_key_from_file(run_cli, tmp_path):
    eng = _readable_only_db()
    key_file = tmp_path / "key"
    key_file.write_text(KEY_A + "\n")
    key_file.chmod(0o600)
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=key_file, file_explicit=False)
    assert run_cli(eng, "prepare-downgrade", "--yes") == cli.EXIT_OK
    assert _all_secrets_plain(eng)


def test_decrypt_all(run_cli, capsys):
    eng = _readable_only_db()
    assert run_cli(eng, "decrypt-all") == cli.EXIT_OK
    assert not _all_secrets_plain(eng)
    assert run_cli(eng, "decrypt-all", "--yes") == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "prepare-downgrade" in captured.err
    assert _all_secrets_plain(eng)
    assert _v(eng, "SELECT is_active FROM panel_tokens WHERE id = 2") == 1  # decrypt-all haertet keine Tokens
    assert _v(eng, "SELECT COUNT(*) FROM system_settings WHERE `key` = :k", {"k": cli.F14_MARKER}) == 1
    assert run_cli.audits[-1][0] == "SECRETS_DECRYPT_ALL"
    assert run_cli.audits[-1][1]["decrypted"]["server_configs.api_key"] == 1


def test_reset_unreadable_dry_run_and_yes(run_cli, db, capsys):
    before = _q(db, "SELECT api_key FROM server_configs ORDER BY id")
    assert run_cli(db, "reset-unreadable") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "ns-fremd" in out and "fremd" in out and "captcha_secret_key" in out and "alice" in out and "bob" in out
    assert _q(db, "SELECT api_key FROM server_configs ORDER BY id") == before and run_cli.audits == []

    assert run_cli(db, "reset-unreadable", "--yes") == cli.EXIT_OK
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 2") == ""
    # Klartext-Server wurde dabei mitverschluesselt, lesbarer bleibt lesbar
    assert BOX_A.decrypt(_v(db, "SELECT api_key FROM server_configs WHERE id = 3")) == "pdns-key-3"
    assert BOX_A.decrypt(_v(db, "SELECT api_key FROM server_configs WHERE id = 1")) == "pdns-key-1"
    url, secret, active = _q(db, "SELECT url, secret, is_active FROM webhooks WHERE id = 2")[0]
    assert url == "" and active == 0 and BOX_A.classify(secret) == "encrypted" and BOX_A.decrypt(secret) != "whsec-2"
    assert _q(db, "SELECT is_active FROM webhooks WHERE id = 1")[0][0] == 1
    assert _v(db, "SELECT `value` FROM system_settings WHERE `key` = 'captcha_secret_key'") == ""
    alice = _q(db, "SELECT totp_enabled, totp_secret, totp_pending_secret FROM users WHERE id = 2")[0]
    assert tuple(alice) == (0, None, None)
    bob = _q(db, "SELECT totp_enabled, totp_pending_secret FROM users WHERE id = 3")[0]
    assert tuple(bob) == (0, None)
    action, details = run_cli.audits[-1]
    assert action == "SECRETS_RESET_UNREADABLE"
    assert details["servers"] == ["ns-fremd"] and details["settings"] == ["captcha_secret_key"]
    assert details["users_2fa_reset"] == ["alice"] and details["key_generated"] is False
    assert details["webhooks"] == [{"id": 2, "name": "fremd", "owner": "alice", "fields": ["secret", "url"]}]
    assert details["key_fingerprint"] == secret_store.key_fingerprint(KEY_A)

    assert run_cli(db, "reset-unreadable", "--yes") == cli.EXIT_NOTHING_TO_DO


def test_reset_unreadable_generates_key_when_missing(run_cli, db, tmp_path):
    key_file = tmp_path / "data" / ".secret_key"
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=key_file, file_explicit=False)
    assert run_cli(db, "reset-unreadable", "--yes") == cli.EXIT_OK
    assert key_file.exists() and oct(key_file.stat().st_mode & 0o777) == "0o600"
    new_box = SecretBox(parse_key(key_file.read_text(), source_name="t"))
    # Ohne Schluessel ist alles unlesbar -> alles zurueckgesetzt, Rest mit dem neuen Schluessel verschluesselt
    assert _v(db, "SELECT api_key FROM server_configs WHERE id = 1") == ""
    assert new_box.decrypt(_v(db, "SELECT api_key FROM server_configs WHERE id = 3")) == "pdns-key-3"
    assert _v(db, "SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'") == ""
    details = run_cli.audits[-1][1]
    assert details["key_generated"] is True and details["key_fingerprint"] == new_box.fingerprint
    assert sorted(details["servers"]) == ["ns-fremd", "ns1"]


def test_reset_unreadable_refuses_explicit_missing_key_file(run_cli, db, tmp_path, capsys):
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=tmp_path / "secret",
                                                file_explicit=True)
    before = _q(db, "SELECT api_key FROM server_configs ORDER BY id")
    assert run_cli(db, "reset-unreadable", "--yes") == cli.EXIT_ERROR
    assert "SECRET_ENCRYPTION_KEY_FILE" in capsys.readouterr().err
    assert not (tmp_path / "secret").exists() and _q(db, "SELECT api_key FROM server_configs ORDER BY id") == before


def test_reset_unreadable_moves_invalid_default_file(run_cli, db, tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("kaputt")
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=key_file, file_explicit=False)
    assert run_cli(db, "reset-unreadable", "--yes") == cli.EXIT_OK
    assert secret_store.read_key_file(key_file).status == "ok"
    assert [p.name.startswith(".secret_key.invalid-") for p in tmp_path.iterdir()].count(True) == 1


def test_status_json_and_text(run_cli, db, capsys):
    assert run_cli(db, "status", "--json") == cli.EXIT_OK
    out = capsys.readouterr().out
    data = json.loads(out)
    cols = {c["id"]: c for c in data["columns"]}
    assert cols["server_configs.api_key"] == {"id": "server_configs.api_key", "encrypted": 1, "encrypted_old": 0,
                                              "plaintext": 1, "unreadable": 1, "empty": 0}
    assert data["key_source"] == "env" and data["key_fingerprint"] == secret_store.key_fingerprint(KEY_A)
    for secret in ("pdns-key", "smtp-pw", "whsec", PREFIX):
        assert secret not in out
    assert run_cli(db, "status") == cli.EXIT_OK
    text_out = capsys.readouterr().out
    assert "server_configs.api_key" in text_out and "ns-fremd" in text_out and PREFIX not in text_out


def test_key_info_and_generate_key(run_cli, capsys, tmp_path):
    assert asyncio.run(cli.main(["key-info"])) == cli.EXIT_OK
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
    assert lines["source"] == "env" and lines["fingerprint"] == secret_store.key_fingerprint(KEY_A)
    assert lines["key_file_explicit"] == "0" and lines["key_file_exists"] == "0"
    assert KEY_A not in str(lines)

    key_file = tmp_path / "k"
    key_file.write_text(KEY_B)
    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=key_file, file_explicit=True)
    assert asyncio.run(cli.main(["key-info"])) == cli.EXIT_OK
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
    assert lines["source"] == "file" and lines["key_file"] == str(key_file) and lines["key_file_explicit"] == "1"
    assert lines["fingerprint"] == secret_store.key_fingerprint(KEY_B)

    run_cli.cfg["cfg"] = secret_store.KeyConfig(env_primary="zu-kurz", env_previous=(), file_path=key_file,
                                                file_explicit=False)
    assert asyncio.run(cli.main(["key-info"])) == cli.EXIT_ERROR
    assert "source=invalid" in capsys.readouterr().out

    assert asyncio.run(cli.main(["generate-key"])) == cli.EXIT_OK
    key = capsys.readouterr().out.strip()
    assert len(key) == 44 and parse_key(key, source_name="t")


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
    args = cli.build_parser().parse_args(["decrypt-all", "--yes", "--force"])
    assert args.yes and args.force
