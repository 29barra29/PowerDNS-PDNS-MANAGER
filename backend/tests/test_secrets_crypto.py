"""F5 §9.1 test_secrets_crypto: SecretBox, Lese-/Schreibpfad, TypeDecorators, system_settings-Helfer.

Laeuft ohne MariaDB: TypeDecorators und Settings-Helfer werden gegen SQLite-Tabellen getestet, die
dieser Test selbst anlegt (die Modelle mit EncryptedText kommen erst in Welle 0b).
"""
import asyncio
import logging

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Integer, String, create_engine, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core import secrets as secret_store
from app.core.secrets import (
    PREFIX,
    UNREADABLE,
    EncryptedString,
    EncryptedText,
    KeyConfig,
    SecretBox,
    SecretsUnavailableError,
    UnreadableSecret,
)

KEY_A = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="   # urlsafe-b64("test-key-for-pdns-manager-32byte")
KEY_B = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="   # zweiter Testschluessel (F5 §9)


@pytest.fixture(autouse=True)
def _reset_secret_state():
    secret_store.reset_for_tests()
    yield
    secret_store.reset_for_tests()


def _key_config(monkeypatch, cfg: KeyConfig) -> None:
    """Lazy-Load ohne init_secrets auf eine feste Konfiguration umbiegen (Env/Datei des Hosts egal)."""
    monkeypatch.setattr(KeyConfig, "from_settings", classmethod(lambda cls, s=None: cfg))


def _box(key: str, *old: str) -> SecretBox:
    return SecretBox(key.encode(), [k.encode() for k in old])


# --- F5 §9.1 Nr. 1-11 -------------------------------------------------------------------------

def test_roundtrip_and_prefix():
    box = _box(KEY_A)
    c1 = box.encrypt("geheim-äöü")
    c2 = box.encrypt("geheim-äöü")
    assert c1.startswith(PREFIX) and c2.startswith(PREFIX)
    assert c1 != c2
    assert box.decrypt(c1) == "geheim-äöü"
    assert box.classify(c1) == "encrypted"


def test_empty_and_none_passthrough():
    secret_store.configure_for_tests(KEY_A)
    assert secret_store.encrypt_value("") == ""
    assert secret_store.encrypt_value(None) is None
    assert secret_store.decrypt_value(None, label="x") is None
    assert secret_store.decrypt_value("", label="x") == ""
    assert _box(KEY_A).encrypt("") == ""


def test_legacy_plaintext_passthrough():
    secret_store.configure_for_tests(KEY_A)
    assert secret_store.decrypt_value("abc", label="x") == "abc"
    assert _box(KEY_A).decrypt("abc") == "abc"
    assert _box(KEY_A).classify("abc") == "plaintext"


def test_wrong_key_returns_placeholder_without_raising(caplog):
    stored = _box(KEY_A).encrypt("klartext-wert")
    secret_store.configure_for_tests(KEY_B)
    with caplog.at_level(logging.ERROR, logger="app.core.secrets"):
        value = secret_store.decrypt_value(stored, label="server_configs.api_key")
        assert secret_store.is_unreadable(value)
        assert value == ""
        assert not bool(value)
        log = caplog.text
        assert "server_configs.api_key" in log
        assert stored not in log and stored[len(PREFIX):len(PREFIX) + 20] not in log
        assert "klartext-wert" not in log
        n_records = len(caplog.records)
        assert n_records == 1
        # zweiter Aufruf: gedrosselt, keine zweite Logzeile
        assert secret_store.is_unreadable(secret_store.decrypt_value(stored, label="server_configs.api_key"))
        assert len(caplog.records) == n_records
    assert secret_store.runtime_unreadable_counts() == {"server_configs.api_key": 2}


def test_corrupt_token():
    secret_store.configure_for_tests(KEY_A)
    for raw in ("enc:v1:garbage", "enc:v1:", "enc:v1:äöü", PREFIX + Fernet(KEY_A.encode()).encrypt(b"\xff\xfe").decode()):
        assert secret_store.is_unreadable(secret_store.decrypt_value(raw, label="x")), raw
    assert _box(KEY_A).classify("enc:v1:garbage") == "unreadable"
    assert _box(KEY_A).classify("enc:v1:äöü") == "unreadable"


def test_multifernet_rotation():
    token_a = _box(KEY_A).encrypt("wert")
    box = _box(KEY_B, KEY_A)
    assert box.decrypt(token_a) == "wert"
    assert box.classify(token_a) == "encrypted_old"
    assert box.decrypt_only_count == 1
    rotated = box.rotate(token_a)
    assert rotated.startswith(PREFIX)
    assert _box(KEY_B).decrypt(rotated) == "wert"
    assert _box(KEY_B).classify(rotated) == "encrypted"
    assert _box(KEY_A).classify(rotated) == "unreadable"


def test_encrypt_always_encrypts_prefixed_input():
    secret_store.configure_for_tests(KEY_A)
    stored = secret_store.encrypt_value("enc:v1:xyz")
    assert stored.startswith(PREFIX) and stored != "enc:v1:xyz"
    assert secret_store.decrypt_value(stored, label="x") == "enc:v1:xyz"


def test_encrypt_unreadable_placeholder_raises():
    secret_store.configure_for_tests(KEY_A)
    with pytest.raises(SecretsUnavailableError):
        secret_store.encrypt_value(UNREADABLE)
    # auch im Fallback-Modus und ohne Schluessel
    secret_store.configure_for_tests(mode="plaintext_fallback")
    with pytest.raises(SecretsUnavailableError):
        secret_store.encrypt_value(UNREADABLE)


def test_uninitialized_write_without_key_raises(monkeypatch, tmp_path):
    _key_config(monkeypatch, KeyConfig("", (), tmp_path / ".secret_key", False))
    with pytest.raises(SecretsUnavailableError) as ei:
        secret_store.encrypt_value("x")
    assert not isinstance(ei.value, ValueError)
    assert not (tmp_path / ".secret_key").exists()   # Lazy-Load erzeugt nie
    assert list(tmp_path.iterdir()) == []
    # Lesen wirft trotzdem nie
    assert secret_store.is_unreadable(secret_store.decrypt_value(_box(KEY_A).encrypt("v"), label="x"))
    assert secret_store.decrypt_value("klartext", label="x") == "klartext"


def test_fingerprint_stable_and_short():
    fp = secret_store.key_fingerprint(KEY_A)
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
    assert fp == secret_store.key_fingerprint(KEY_A.encode())
    assert fp != secret_store.key_fingerprint(KEY_B)
    assert fp not in KEY_A and KEY_A[:12] != fp
    # gleiches Schluesselmaterial in Standard-Base64 -> gleicher Fingerprint
    std = KEY_A.replace("-", "+").replace("_", "/")
    assert secret_store.key_fingerprint(std) == fp
    assert _box(KEY_A).fingerprint == fp


def test_typedecorator_bind_and_result():
    secret_store.configure_for_tests(KEY_A)
    dialect = mysql.dialect()
    t = EncryptedText(label="x")
    stored = t.process_bind_param("abc", dialect)
    assert stored.startswith(PREFIX)
    assert t.process_result_value(stored, dialect) == "abc"
    assert t.process_bind_param(None, dialect) is None
    assert t.process_result_value(None, dialect) is None
    assert t.process_result_value("legacy", dialect) == "legacy"
    assert str(EncryptedString(512, label="y").compile(dialect=dialect)) == "VARCHAR(512)"
    assert str(EncryptedText(label="x").compile(dialect=dialect)) == "TEXT"
    assert EncryptedString(512, label="y").label == "y"
    with pytest.raises(TypeError):
        EncryptedText()


def test_typedecorator_cache_key_includes_label_without_warning(recwarn):
    a, b = EncryptedText(label="a"), EncryptedText(label="b")
    assert a._static_cache_key != b._static_cache_key
    assert EncryptedString(512, label="a")._static_cache_key == EncryptedString(512, label="a")._static_cache_key
    assert not [w for w in recwarn if "cache_ok" in str(w.message)]


# --- Ergaenzungen: Platzhalter, Modi, Lazy-Load -----------------------------------------------

def test_unreadable_placeholder_behaves_like_empty_string():
    assert UNREADABLE == "" and not UNREADABLE and isinstance(UNREADABLE, str)
    assert secret_store.is_unreadable(UnreadableSecret("egal"))
    assert UnreadableSecret("egal") == ""
    assert not secret_store.is_unreadable("")
    assert repr(UNREADABLE) == "<UnreadableSecret>"
    assert secret_store.is_encrypted("enc:v1:x") and not secret_store.is_encrypted("x")
    assert not secret_store.is_encrypted(None)


def test_plaintext_fallback_mode(caplog):
    secret_store.configure_for_tests(mode="plaintext_fallback")
    with caplog.at_level(logging.WARNING, logger="app.core.secrets"):
        assert secret_store.encrypt_value("klartext") == "klartext"
        assert secret_store.encrypt_value("klartext2") == "klartext2"
    assert len([r for r in caplog.records if "Fallback" in r.getMessage()]) == 1   # gedrosselt
    assert "klartext" not in caplog.text
    assert secret_store.decrypt_value("klartext", label="x") == "klartext"
    assert secret_store.is_unreadable(secret_store.decrypt_value(_box(KEY_A).encrypt("v"), label="x"))


def test_lazy_load_from_env_includes_file_and_previous(monkeypatch, tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text(KEY_A + "\n")
    key_file.chmod(0o600)
    key_c = Fernet.generate_key().decode()
    _key_config(monkeypatch, KeyConfig(KEY_B, (key_c,), key_file, False))
    old_from_file = _box(KEY_A).encrypt("datei")
    old_from_prev = _box(key_c).encrypt("vorher")
    assert secret_store.decrypt_value(old_from_file, label="x") == "datei"
    assert secret_store.decrypt_value(old_from_prev, label="x") == "vorher"
    new = secret_store.encrypt_value("neu")
    assert _box(KEY_B).classify(new) == "encrypted"   # Schreiben immer mit dem Primaerschluessel
    assert secret_store._STATE.mode == "encrypted"


def test_lazy_load_from_file_only(monkeypatch, tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text(KEY_A)
    _key_config(monkeypatch, KeyConfig("", (), key_file, False))
    assert _box(KEY_A).decrypt(secret_store.encrypt_value("v")) == "v"


def test_lazy_load_invalid_env_key_is_unavailable(monkeypatch, tmp_path):
    _key_config(monkeypatch, KeyConfig("kaputt", (), tmp_path / ".secret_key", False))
    with pytest.raises(SecretsUnavailableError):
        secret_store.encrypt_value("x")
    assert secret_store.is_unreadable(secret_store.decrypt_value(_box(KEY_A).encrypt("v"), label="x"))


def test_encrypt_rejects_non_string():
    secret_store.configure_for_tests(KEY_A)
    with pytest.raises(TypeError):
        secret_store.encrypt_value(123)  # type: ignore[arg-type]


def test_secret_box_dedupes_keys_and_rejects_invalid():
    box = SecretBox(KEY_A.encode(), [KEY_A.encode(), KEY_A.replace("-", "+").encode(), KEY_B.encode()])
    assert box.decrypt_only_count == 1
    assert box.decrypt_only_fingerprints == (secret_store.key_fingerprint(KEY_B),)
    with pytest.raises(ValueError):
        SecretBox(b"kein-schluessel")


def test_ciphertext_sizes_fit_columns():
    """F5-Prototyp: 32 Z. -> 147, 500 Z. -> 767 Zeichen (TOTP passt in VARCHAR(512), api_key braucht TEXT)."""
    box = _box(KEY_A)
    assert len(box.encrypt("A" * 32)) == 147
    assert len(box.encrypt("A" * 64)) == 191
    assert len(box.encrypt("x" * 500)) == 767


def test_configure_for_tests_and_reset():
    secret_store.configure_for_tests(KEY_A, [KEY_B])
    assert secret_store._STATE.box.decrypt_only_count == 1
    assert secret_store.current_mode() == "encrypted"
    assert secret_store.startup_report().fingerprint == secret_store.key_fingerprint(KEY_A)
    secret_store.configure_for_tests(mode="plaintext_fallback")
    assert secret_store.current_mode() == "plaintext_fallback"
    secret_store.reset_for_tests()
    assert secret_store._STATE.mode == "uninitialized" and secret_store._STATE.box is None
    assert secret_store.current_mode() == "uninitialized" and secret_store.startup_report() is None
    with pytest.raises(ValueError):
        secret_store.configure_for_tests(mode="unbekannt")


# --- TypeDecorators gegen SQLite (eigene Tabellen) --------------------------------------------

class _Base(DeclarativeBase):
    pass


class _Srv(_Base):
    __tablename__ = "t_srv"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    api_key: Mapped[str] = mapped_column(EncryptedText(label="t_srv.api_key"), nullable=False)
    totp: Mapped[str | None] = mapped_column(EncryptedString(512, label="t_srv.totp"), nullable=True)


@pytest.fixture
def sqlite_engine():
    eng = create_engine("sqlite://")
    _Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _raw(eng, column: str, row_id: int = 1):
    with eng.connect() as conn:
        return conn.execute(text(f"SELECT {column} FROM t_srv WHERE id = :id"), {"id": row_id}).scalar_one()


def test_orm_roundtrip_encrypts_on_disk(sqlite_engine):
    secret_store.configure_for_tests(KEY_A)
    with Session(sqlite_engine) as s:
        s.add(_Srv(id=1, name="ns1", api_key="pdns-api-key", totp="JBSWY3DPEHPK3PXP"))
        s.commit()
    assert _raw(sqlite_engine, "api_key").startswith(PREFIX)
    assert _raw(sqlite_engine, "totp").startswith(PREFIX)
    with Session(sqlite_engine) as s:
        obj = s.get(_Srv, 1)
        assert obj.api_key == "pdns-api-key"
        assert obj.totp == "JBSWY3DPEHPK3PXP"


def test_unreadable_value_not_overwritten_on_unrelated_update(sqlite_engine):
    secret_store.configure_for_tests(KEY_A)
    with Session(sqlite_engine) as s:
        s.add(_Srv(id=1, name="ns1", api_key="x"))
        s.commit()
    with sqlite_engine.begin() as conn:
        conn.execute(text("UPDATE t_srv SET api_key = 'enc:v1:garbage' WHERE id = 1"))
    with Session(sqlite_engine) as s:
        obj = s.get(_Srv, 1)
        assert secret_store.is_unreadable(obj.api_key)
        obj.name = "ns1-neu"
        obj.api_key = ""          # gleich dem Platzhalter -> kein UPDATE der Spalte
        s.commit()
    assert _raw(sqlite_engine, "api_key") == "enc:v1:garbage"
    assert _raw(sqlite_engine, "name") == "ns1-neu"


def test_writing_placeholder_from_other_row_is_refused(sqlite_engine):
    """Kopiert jemand den Platzhalter in eine andere Zeile, wird nicht still "" geschrieben."""
    secret_store.configure_for_tests(KEY_A)
    with Session(sqlite_engine) as s:
        s.add(_Srv(id=2, name="ns2", api_key="ok"))
        s.commit()
        obj = s.get(_Srv, 2)
        obj.api_key = UNREADABLE
        with pytest.raises(StatementError) as ei:
            s.commit()
        assert isinstance(ei.value.orig, SecretsUnavailableError)
        s.rollback()
    assert _raw(sqlite_engine, "api_key", 2).startswith(PREFIX)


# --- services/system_settings.py --------------------------------------------------------------

class _AsyncSessionAdapter:
    """Minimaler AsyncSession-Ersatz ueber einer synchronen SQLite-Session (aiosqlite fehlt im Image)."""

    def __init__(self, session: Session):
        self.sync_session = session

    async def execute(self, *args, **kwargs):
        return self.sync_session.execute(*args, **kwargs)

    def add(self, obj):
        self.sync_session.add(obj)

    async def flush(self):
        self.sync_session.flush()

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self.sync_session, *args, **kwargs)


@pytest.fixture
def settings_db():
    from app.models.models import SystemSetting

    eng = create_engine("sqlite://")
    SystemSetting.__table__.create(eng)
    with Session(eng) as s:
        yield eng, _AsyncSessionAdapter(s), s
    eng.dispose()


def _raw_setting(eng, key):
    with eng.connect() as conn:
        return conn.execute(text("SELECT `value` FROM system_settings WHERE `key` = :k"), {"k": key}).scalar_one_or_none()


def test_secret_setting_keys_match_plan():
    from app.services.system_settings import is_secret_setting

    assert secret_store.SECRET_SETTING_KEYS == {
        "smtp_password", "captcha_secret_key", "oidc_client_secret", "ldap_bind_password", "metrics_token",
    }
    assert is_secret_setting("metrics_token") and not is_secret_setting("smtp_host")
    assert ("webhooks", "url") in secret_store.SECRET_COLUMNS


def test_system_settings_secret_encrypted_plain_stays_plain(settings_db):
    from app.services import system_settings as ss

    eng, db, s = settings_db
    secret_store.configure_for_tests(KEY_A)
    asyncio.run(ss.set_setting(db, "smtp_password", "pw"))
    asyncio.run(ss.set_setting(db, "app_name", "enc:v1:sieht-nur-so-aus"))
    s.commit()
    assert _raw_setting(eng, "smtp_password").startswith(PREFIX)
    assert _raw_setting(eng, "app_name") == "enc:v1:sieht-nur-so-aus"
    vals = asyncio.run(ss.get_settings(db, ["smtp_password", "app_name", "fehlt"]))
    assert vals == {"smtp_password": "pw", "app_name": "enc:v1:sieht-nur-so-aus"}


def test_system_settings_upsert_bulk_bool_and_delete(settings_db):
    from app.services import system_settings as ss

    eng, db, s = settings_db
    secret_store.configure_for_tests(KEY_A)
    asyncio.run(ss.set_settings(db, {"dyndns_enabled": False, "propagation_ipv6": True, "smtp_port": 587,
                                     "metrics_token": "tok", "smtp_host": None}))
    asyncio.run(ss.set_setting(db, "smtp_port", "465"))   # Update vorhandener Zeile
    s.commit()
    assert _raw_setting(eng, "dyndns_enabled") == "false"
    assert _raw_setting(eng, "propagation_ipv6") == "true"
    assert _raw_setting(eng, "smtp_port") == "465"
    assert _raw_setting(eng, "metrics_token").startswith(PREFIX)
    assert _raw_setting(eng, "smtp_host") is None
    assert asyncio.run(ss.get_bool_setting(db, "dyndns_enabled", True)) is False
    assert asyncio.run(ss.get_bool_setting(db, "propagation_ipv6", False)) is True
    assert asyncio.run(ss.get_bool_setting(db, "fehlt", True)) is True
    asyncio.run(ss.set_setting(db, "leer", "  "))
    assert asyncio.run(ss.get_bool_setting(db, "leer", True)) is True
    asyncio.run(ss.set_setting(db, "gross", "TRUE"))
    assert asyncio.run(ss.get_bool_setting(db, "gross", False)) is True
    assert asyncio.run(ss.get_setting(db, "smtp_host", "default")) == "default"   # NULL -> default
    assert asyncio.run(ss.get_setting(db, "fehlt", "d")) == "d"
    assert asyncio.run(ss.get_setting(db, "metrics_token")) == "tok"
    asyncio.run(ss.delete_setting(db, "metrics_token"))
    asyncio.run(ss.delete_setting(db, "gibt-es-nicht"))
    s.commit()
    assert _raw_setting(eng, "metrics_token") is None
    with pytest.raises(TypeError):
        asyncio.run(ss.set_setting(db, "x", object()))


def test_system_settings_unreadable_and_placeholder_write(settings_db):
    from app.services import system_settings as ss

    eng, db, s = settings_db
    secret_store.configure_for_tests(KEY_A)
    asyncio.run(ss.set_setting(db, "smtp_password", "pw"))
    s.commit()
    before = _raw_setting(eng, "smtp_password")
    secret_store.configure_for_tests(KEY_B)   # anderer Schluessel -> unlesbar
    value = asyncio.run(ss.get_setting(db, "smtp_password", "default"))
    assert secret_store.is_unreadable(value)   # unlesbar ist nicht "fehlt"
    with pytest.raises(SecretsUnavailableError):
        asyncio.run(ss.set_setting(db, "smtp_password", value))
    s.rollback()
    assert _raw_setting(eng, "smtp_password") == before
    # leer schreiben (bewusst loeschen) bleibt erlaubt
    asyncio.run(ss.set_setting(db, "smtp_password", ""))
    s.commit()
    assert _raw_setting(eng, "smtp_password") == ""


def test_system_settings_secret_write_without_key_raises(settings_db, monkeypatch, tmp_path):
    from app.services import system_settings as ss

    eng, db, s = settings_db
    _key_config(monkeypatch, KeyConfig("", (), tmp_path / ".secret_key", False))
    with pytest.raises(SecretsUnavailableError):
        asyncio.run(ss.set_setting(db, "ldap_bind_password", "pw"))
    asyncio.run(ss.set_setting(db, "app_name", "ok"))   # Nicht-Secrets gehen weiter
    s.commit()
    assert _raw_setting(eng, "ldap_bind_password") is None
    assert _raw_setting(eng, "app_name") == "ok"
