"""F5 §9.1 test_secrets_sqlite: Verschluesselung mit den ECHTEN Modellen (TypeDecorators an den Spalten,
before_flush-Listener, Startmigration gegen die Modell-Tabellen).

Synchrone SQLite-In-Memory-DB (aiosqlite ist nicht im Testimage); async-Pfade (init_secrets) ueber einen
kleinen AsyncEngine-Adapter wie in test_secrets_keyload.py.
"""
import asyncio
import contextlib
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core import secrets as secret_store
from app.core.database import Base
from app.core.secrets import (
    PREFIX,
    SECRET_COLUMNS,
    EncryptedString,
    EncryptedText,
    KeyConfig,
    SecretBox,
    SecretsStartupError,
    init_secrets,
    init_secrets_sync,
    is_unreadable,
    read_key_file,
)
from app.models.models import ServerConfig, SystemSetting, User, Webhook

KEY_A = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="   # = conftest.TEST_SECRET_KEY
KEY_B = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="   # = conftest.SECOND_TEST_KEY
KEY_C = Fernet.generate_key().decode()
KA, KB, KC = KEY_A.encode(), KEY_B.encode(), KEY_C.encode()

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
skip_as_root = pytest.mark.skipif(IS_ROOT, reason="chmod-Sperren greifen fuer root nicht")

TABLES = [User.__table__, ServerConfig.__table__, Webhook.__table__, SystemSetting.__table__]


@pytest.fixture(autouse=True)
def _encrypted_with_key_a():
    """Standard: aktiver Schluessel A (wie nach init_secrets mit SECRET_ENCRYPTION_KEY=A)."""
    secret_store.configure_for_tests(KEY_A)
    yield
    secret_store.reset_for_tests()


def _box(*keys: bytes) -> SecretBox:
    return SecretBox(keys[0], keys[1:])


def _engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=TABLES)
    return eng


def _seed_raw(eng, enc=lambda v: v):
    """Bestand wie unter 2.4.1 (Raw-SQL, an den Modell-Tabellen vorbei), optional verschluesselt."""
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, hashed_password, role, totp_enabled, totp_secret, totp_pending_secret) "
            "VALUES (1, 'admin', 'h', 'admin', 1, :t1, NULL), (2, 'bob', 'h', 'user', 0, NULL, :t2), "
            "(3, 'carol', 'h', 'user', 0, '', '')"),
            {"t1": enc("JBSWY3DPEHPK3PXP"), "t2": enc("KRSXG5CTMVRXEZLU")})
        conn.execute(text(
            "INSERT INTO server_configs (id, name, url, api_key) VALUES (1, 'ns1', 'http://ns1:8081', :k1), "
            "(2, 'ns2', 'http://ns2:8081', :k2)"), {"k1": enc("pdns-key-1"), "k2": enc("pdns-key-2")})
        conn.execute(text(
            "INSERT INTO webhooks (id, user_id, name, url, secret, events, is_active, created_at) VALUES "
            "(1, 2, 'slack', :u1, :s1, '[\"*\"]', 1, '2026-01-01 00:00:00'), "
            "(2, 99, 'verwaist', :u2, :s2, '[]', 1, '2026-01-01 00:00:00')"),
            {"u1": enc("https://hooks.slack.com/services/T000/B000/XXXX"), "s1": enc("whsec-1"),
             "u2": enc("https://example.com/hook"), "s2": enc("whsec-2")})
        conn.execute(text(
            "INSERT INTO system_settings (id, `key`, `value`) VALUES (1, 'smtp_password', :p), "
            "(2, 'captcha_secret_key', :c), (3, 'smtp_host', 'mail.example.com')"),
            {"p": enc("smtp-pw"), "c": enc("cap-secret")})


def _dump(eng) -> dict:
    with eng.connect() as conn:
        return {
            "users": conn.execute(text("SELECT id, totp_secret, totp_pending_secret FROM users ORDER BY id")).all(),
            "servers": conn.execute(text("SELECT id, api_key FROM server_configs ORDER BY id")).all(),
            "webhooks": conn.execute(text("SELECT id, url, secret FROM webhooks ORDER BY id")).all(),
            "settings": conn.execute(text("SELECT `key`, `value` FROM system_settings ORDER BY id")).all(),
        }


def _secret_raw_values(dump: dict) -> list:
    vals = []
    for _id, t1, t2 in dump["users"]:
        vals += [t1, t2]
    vals += [k for _id, k in dump["servers"]]
    for _id, u, s in dump["webhooks"]:
        vals += [u, s]
    vals += [v for k, v in dump["settings"] if k in secret_store.SECRET_SETTING_KEYS]
    return [v for v in vals if v]


def _run(eng, cfg):
    with eng.begin() as conn:
        return init_secrets_sync(conn, cfg)


# --- Modelle passen zur Spaltenliste von core/secrets.py ------------------------------------------

def test_model_columns_match_secret_columns():
    for table, column in SECRET_COLUMNS:
        col_type = Base.metadata.tables[table].c[column].type
        assert isinstance(col_type, (EncryptedText, EncryptedString)), f"{table}.{column}"
        assert col_type.label == f"{table}.{column}"
    assert Base.metadata.tables["users"].c["totp_secret"].type.impl.length == 512
    assert not Base.metadata.tables["server_configs"].c["api_key"].nullable
    assert not Base.metadata.tables["webhooks"].c["url"].nullable


# --- 1. ORM-Roundtrip --------------------------------------------------------------------------------

def test_orm_roundtrip_encrypts_on_disk():
    eng = _engine()
    with Session(eng) as s:
        s.add(User(id=1, username="admin", hashed_password="h", role="admin", totp_enabled=True,
                   totp_secret="JBSWY3DPEHPK3PXP"))
        s.add(ServerConfig(id=1, name="ns1", url="http://ns1:8081", api_key="pdns-key"))
        s.add(Webhook(id=1, user_id=1, name="w", url="https://hooks.example.com/T/B/X", secret="whsec", events=["*"]))
        s.commit()
    dump = _dump(eng)
    raw = [dump["users"][0][1], dump["servers"][0][1], dump["webhooks"][0][1], dump["webhooks"][0][2]]
    assert all(v.startswith(PREFIX) for v in raw)
    assert "pdns-key" not in repr(dump) and "hooks.example.com" not in repr(dump)
    with Session(eng) as s:
        assert s.get(User, 1).totp_secret == "JBSWY3DPEHPK3PXP"
        assert s.get(ServerConfig, 1).api_key == "pdns-key"
        hook = s.get(Webhook, 1)
        assert (hook.url, hook.secret) == ("https://hooks.example.com/T/B/X", "whsec")
        # neue Spalten mit Python-Defaults
        assert (hook.scope, hook.consecutive_failures) == ("own", 0)
        user = s.get(User, 1)
        assert (user.must_change_password, user.auth_source) == (False, "local")


# --- 2. Unlesbarer Wert wird bei fremdem Update nicht ueberschrieben ------------------------------

def test_unreadable_value_not_overwritten_on_unrelated_update():
    eng = _engine()
    _seed_raw(eng, enc=_box(KA).encrypt)
    with eng.begin() as conn:
        conn.execute(text("UPDATE server_configs SET api_key = 'enc:v1:garbage' WHERE id = 1"))
    with Session(eng) as s:
        cfg = s.get(ServerConfig, 1)
        assert is_unreadable(cfg.api_key)
        cfg.display_name = "Neu"
        cfg.api_key = ""   # == Platzhalter -> keine Aenderung fuer SQLAlchemy
        s.commit()
    with eng.connect() as conn:
        row = conn.execute(text("SELECT display_name, api_key FROM server_configs WHERE id = 1")).one()
    assert tuple(row) == ("Neu", "enc:v1:garbage")


# --- 3. before_flush-Listener ------------------------------------------------------------------------

def test_before_flush_encrypts_only_secret_settings():
    eng = _engine()
    with Session(eng) as s:
        s.add_all([SystemSetting(key="smtp_password", value="pw"), SystemSetting(key="app_name", value="Panel"),
                   SystemSetting(key="metrics_token", value=""), SystemSetting(key="ldap_bind_password", value=None)])
        s.commit()
        row = s.execute(select(SystemSetting).where(SystemSetting.key == "smtp_password")).scalar_one()
        row.value = "neu"          # auch beim Aendern (session.dirty)
        s.commit()
    with eng.connect() as conn:
        vals = dict(conn.execute(text("SELECT `key`, `value` FROM system_settings")).all())
    assert vals["smtp_password"].startswith(PREFIX) and _box(KA).decrypt(vals["smtp_password"]) == "neu"
    assert vals["app_name"] == "Panel" and vals["metrics_token"] == "" and vals["ldap_bind_password"] is None


def test_before_flush_does_not_double_encrypt():
    eng = _engine()
    already = _box(KA).encrypt("pw")
    with Session(eng) as s:
        s.add(SystemSetting(key="captcha_secret_key", value=already))
        s.commit()
    with eng.connect() as conn:
        assert conn.execute(text("SELECT `value` FROM system_settings")).scalar_one() == already


# --- 4.-10. Startmigration gegen die Modell-Tabellen -------------------------------------------------

def test_init_secrets_sync_migrates_and_is_idempotent(tmp_path):
    secret_store.reset_for_tests()
    eng = _engine()
    _seed_raw(eng)
    cfg = KeyConfig("", (), tmp_path / ".secret_key", False)
    report, box = _run(eng, cfg)
    assert report.mode == "encrypted" and report.key_generated and report.generated_reason == "first_start"
    assert report.migrated == {
        "users.totp_secret": 1, "users.totp_pending_secret": 1, "server_configs.api_key": 2,
        "webhooks.secret": 2, "webhooks.url": 2,
        "system_settings.smtp_password": 1, "system_settings.captcha_secret_key": 1,
    }
    dump = _dump(eng)
    assert all(v.startswith(PREFIX) for v in _secret_raw_values(dump)) and len(_secret_raw_values(dump)) == 10
    assert dump["users"][2] == (3, "", "")
    assert ("smtp_host", "mail.example.com") in dump["settings"]
    report2, _ = _run(eng, cfg)
    assert report2.migrated == {} and report2.rotated == {} and not report2.key_generated
    assert _dump(eng) == dump   # byte-gleich
    # ORM liest danach Klartext (Box des Laufs aktivieren)
    secret_store.configure_for_tests(read_key_file(tmp_path / ".secret_key").key.decode())
    with Session(eng) as s:
        assert s.get(Webhook, 1).url == "https://hooks.slack.com/services/T000/B000/XXXX"
        assert s.get(User, 2).totp_pending_secret == "KRSXG5CTMVRXEZLU"


def test_init_secrets_sync_key_missing_aborts(tmp_path):
    eng = _engine()
    _seed_raw(eng, enc=_box(KA).encrypt)
    before = _dump(eng)
    key_file = tmp_path / ".secret_key"
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig("", (), key_file, False))
    assert ei.value.code == "KEY_MISSING"
    assert not key_file.exists() and _dump(eng) == before


def test_init_secrets_sync_key_mismatch_aborts(tmp_path):
    eng = _engine()
    _seed_raw(eng, enc=_box(KA).encrypt)
    before = _dump(eng)
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig(KEY_B, (), tmp_path / ".secret_key", False))
    assert ei.value.code == "KEY_MISMATCH"
    assert KEY_A not in "\n".join(ei.value.log_lines()) and KEY_B not in "\n".join(ei.value.log_lines())
    assert _dump(eng) == before


def test_init_secrets_sync_file_fallback_rotates(tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text(KEY_A + "\n")
    key_file.chmod(0o600)
    eng = _engine()
    _seed_raw(eng, enc=_box(KA).encrypt)
    report, _ = _run(eng, KeyConfig(KEY_B, (), key_file, False))
    assert report.key_source == "env" and "key_file_obsolete" in report.static_issues
    assert sum(report.rotated.values()) == 10 and report.migrated == {}
    only_b = _box(KB)
    assert all(only_b.classify(v) == "encrypted" for v in _secret_raw_values(_dump(eng)))


def test_init_secrets_sync_partial_unreadable_continues(tmp_path):
    eng = _engine()
    _seed_raw(eng, enc=_box(KA).encrypt)
    foreign = _box(KC).encrypt("fremd")
    with eng.begin() as conn:
        conn.execute(text("UPDATE webhooks SET url = :v WHERE id = 1"), {"v": foreign})
    report, _ = _run(eng, KeyConfig(KEY_A, (), tmp_path / ".secret_key", False))
    assert report.mode == "encrypted"
    assert [(u.field, u.kind, u.row_id, u.name, u.owner) for u in report.unreadable] == [
        ("webhooks.url", "webhook", 1, "slack", "bob")]
    assert _dump(eng)["webhooks"][0][1] == foreign
    # ORM: unlesbare URL kommt als Platzhalter, das Lesen wirft nicht
    with Session(eng) as s:
        assert is_unreadable(s.get(Webhook, 1).url)


@skip_as_root
def test_plaintext_fallback_when_dir_unwritable(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        eng = _engine()
        _seed_raw(eng)
        before = _dump(eng)
        report, box = _run(eng, KeyConfig("", (), d / ".secret_key", False))
        assert box is None and report.mode == "plaintext_fallback"
        assert _dump(eng) == before
    finally:
        os.chmod(d, 0o700)


def test_invalid_default_file_replaced_when_no_ciphertext(tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("kaputt")
    eng = _engine()
    _seed_raw(eng)
    report, box = _run(eng, KeyConfig("", (), key_file, False))
    assert report.key_generated and report.generated_reason == "previous_file_invalid"
    assert len([p for p in tmp_path.iterdir() if p.name.startswith(".secret_key.invalid-")]) == 1
    assert box.fingerprint == secret_store.key_fingerprint(read_key_file(key_file).key)


# --- init_secrets (async) mit den Modell-Tabellen, danach ORM ----------------------------------------

class _FakeAsyncConn:
    def __init__(self, conn):
        self._conn = conn

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._conn, *args, **kwargs)


class _FakeAsyncEngine:
    def __init__(self, eng):
        self.eng = eng

    @contextlib.asynccontextmanager
    async def begin(self):
        with self.eng.begin() as conn:
            yield _FakeAsyncConn(conn)


def test_init_secrets_then_orm_reads_and_writes(tmp_path, monkeypatch):
    import app.services.audit as audit_mod

    audits = []

    async def _rec(action, *_a, **_k):
        audits.append(action)

    monkeypatch.setattr(audit_mod, "write_audit_detached", _rec)
    secret_store.reset_for_tests()
    eng = _engine()
    _seed_raw(eng)
    report = asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig(KEY_A, (), tmp_path / ".k", False)))
    assert report.mode == "encrypted" and secret_store.current_mode() == "encrypted"
    assert audits == ["SECRETS_MIGRATE"]
    with Session(eng) as s:
        cfg = s.get(ServerConfig, 2)
        assert cfg.api_key == "pdns-key-2"
        cfg.api_key = "rotiert"
        s.add(SystemSetting(key="oidc_client_secret", value="oidc"))
        s.commit()
    with eng.connect() as conn:
        raw_key = conn.execute(text("SELECT api_key FROM server_configs WHERE id = 2")).scalar_one()
        raw_oidc = conn.execute(text("SELECT `value` FROM system_settings WHERE `key` = 'oidc_client_secret'")).scalar_one()
    assert _box(KA).decrypt(raw_key) == "rotiert" and _box(KA).decrypt(raw_oidc) == "oidc"
