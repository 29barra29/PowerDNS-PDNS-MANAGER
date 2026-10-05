"""F5 §9.1 test_secrets_keyload: Schluesseldatei, Entscheidungsmatrix (§5.3), Startmigration (§5.4), Status.

Die Startmigration (``init_secrets_sync``) laeuft gegen SQLite-Tabellen, die dieser Test selbst anlegt
(Raw-SQL wie in MariaDB; die ORM-Modelle mit EncryptedText kommen erst in Welle 0b).
Rechte-Tests mit chmod werden als root uebersprungen; die Faelle sind zusaetzlich root-unabhaengig
ueber Verzeichnis-statt-Datei bzw. monkeypatch abgedeckt.
"""
import asyncio
import contextlib
import logging
import os
import stat
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core import secrets as secret_store
from app.core.secrets import (
    DEFAULT_KEY_FILE,
    DOCS_URL,
    PREFIX,
    FileKeyState,
    KeyConfig,
    SecretBox,
    SecretsStartupError,
    _decide,
    init_secrets,
    init_secrets_sync,
    read_key_file,
    write_new_key_file,
)

KEY_A = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="
KEY_B = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="
KEY_C = Fernet.generate_key().decode()
KA, KB, KC = KEY_A.encode(), KEY_B.encode(), KEY_C.encode()

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
skip_as_root = pytest.mark.skipif(IS_ROOT, reason="chmod-Sperren greifen fuer root nicht")


@pytest.fixture(autouse=True)
def _reset_secret_state():
    secret_store.reset_for_tests()
    yield
    secret_store.reset_for_tests()


def _box(*keys: bytes) -> SecretBox:
    return SecretBox(keys[0], keys[1:])


# --- KeyConfig ---------------------------------------------------------------------------------

def test_key_config_from_settings_object():
    s = SimpleNamespace(
        SECRET_ENCRYPTION_KEY=f"  {KEY_A} ",
        SECRET_ENCRYPTION_KEY_PREVIOUS=f"{KEY_B}, ,{KEY_A},{KEY_B},{KEY_C}",
        SECRET_ENCRYPTION_KEY_FILE="",
    )
    cfg = KeyConfig.from_settings(s)
    assert cfg.env_primary == KEY_A
    assert cfg.env_previous == (KEY_B, KEY_C)        # dedupliziert, leer + primary entfernt
    assert cfg.file_path == DEFAULT_KEY_FILE and not cfg.file_explicit
    assert DEFAULT_KEY_FILE.name == ".secret_key" and DEFAULT_KEY_FILE.parent.name == "data"

    cfg = KeyConfig.from_settings(SimpleNamespace(SECRET_ENCRYPTION_KEY="", SECRET_ENCRYPTION_KEY_PREVIOUS="",
                                                  SECRET_ENCRYPTION_KEY_FILE=" /run/secrets/k "))
    assert cfg.file_path == secret_store.Path("/run/secrets/k") and cfg.file_explicit


def test_key_config_falls_back_to_environment(monkeypatch):
    """Solange config.py die Felder nicht kennt (bis Welle 0b), wird die Umgebung gelesen."""
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", KEY_A)
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_PREVIOUS", KEY_B)
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_FILE", "/tmp/x.key")
    cfg = KeyConfig.from_settings(SimpleNamespace())
    assert (cfg.env_primary, cfg.env_previous, str(cfg.file_path), cfg.file_explicit) == (
        KEY_A, (KEY_B,), "/tmp/x.key", True)
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY")
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY_PREVIOUS")
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY_FILE")
    cfg = KeyConfig.from_settings(SimpleNamespace())
    assert (cfg.env_primary, cfg.env_previous, cfg.file_path, cfg.file_explicit) == ("", (), DEFAULT_KEY_FILE, False)


# --- Schluessel pruefen ------------------------------------------------------------------------

def test_env_key_valid():
    assert secret_store.parse_key(f" {KEY_A}\n", source_name="SECRET_ENCRYPTION_KEY") == KA
    # Standard-Base64 mit +/ wird kanonisiert
    std = KEY_C.replace("-", "+").replace("_", "/")
    assert secret_store.parse_key(std, source_name="X") == KC


@pytest.mark.parametrize("bad", ["geheimer-falscher-wert-1234", KEY_A[:-4], KEY_A + "AAAA", KEY_A[:20] + "!" + KEY_A[21:], ""])
def test_env_key_invalid_aborts_without_leaking_value(bad):
    with pytest.raises(SecretsStartupError) as ei:
        secret_store.parse_key(bad, source_name="SECRET_ENCRYPTION_KEY")
    exc = ei.value
    assert exc.code == "KEY_INVALID"
    joined = "\n".join(exc.log_lines()) + str(exc)
    assert "SECRET_ENCRYPTION_KEY" in joined
    assert f"gesetzt: {len(bad.strip())} Zeichen" in joined
    if bad:
        assert bad not in joined
    assert exc.log_lines()[0].startswith("==== PDNS Manager: Start abgebrochen")
    assert exc.log_lines()[-1] == f"Doku: {DOCS_URL}"


def test_previous_invalid_aborts():
    cfg = KeyConfig(KEY_A, ("kaputt",), DEFAULT_KEY_FILE, False)
    with pytest.raises(SecretsStartupError) as ei:
        _decide(cfg, FileKeyState("missing", None, None, True), 0)
    assert ei.value.code == "KEY_INVALID"
    assert "SECRET_ENCRYPTION_KEY_PREVIOUS (Eintrag 1)" in "\n".join(ei.value.lines)
    assert "kaputt" not in "\n".join(ei.value.lines)


# --- Schluesseldatei ----------------------------------------------------------------------------

def test_file_ok_and_permissions_fixed(tmp_path):
    p = tmp_path / ".secret_key"
    p.write_text(KEY_A + "\n")
    os.chmod(p, 0o644)
    st = read_key_file(p)
    assert st.status == "ok" and st.key == KA and st.mode_ok
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_file_permissions_not_fixable(tmp_path, monkeypatch):
    p = tmp_path / ".secret_key"
    p.write_text(KEY_A)
    os.chmod(p, 0o644)

    def _deny(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(secret_store.os, "chmod", _deny)
    st = read_key_file(p)
    assert st.status == "ok" and not st.mode_ok


@skip_as_root
def test_file_unreadable(tmp_path):
    p = tmp_path / ".secret_key"
    p.write_text(KEY_A)
    os.chmod(p, 0o000)
    try:
        st = read_key_file(p)
        assert st.status == "unreadable" and st.key is None and st.error
    finally:
        os.chmod(p, 0o600)


def test_file_unreadable_directory(tmp_path):
    p = tmp_path / ".secret_key"
    p.mkdir()
    st = read_key_file(p)
    assert st.status == "unreadable" and st.error


def test_file_missing(tmp_path):
    assert read_key_file(tmp_path / "nix").status == "missing"


@pytest.mark.parametrize("content", [b"kaputt", b"", b"   \n", b"\xff\xfe\x00", (KEY_A[:-2]).encode()])
def test_file_invalid(tmp_path, content):
    p = tmp_path / ".secret_key"
    p.write_bytes(content)
    st = read_key_file(p)
    assert st.status == "invalid" and st.key is None and st.error


def test_write_new_key_file_atomic_0600(tmp_path):
    p = tmp_path / "sub" / ".secret_key"
    # Rest eines abgebrochenen Laufs mit gleicher PID stoert nicht
    p.parent.mkdir()
    (p.parent / f".secret_key.tmp-{os.getpid()}").write_text("alt")
    key = write_new_key_file(p)
    assert p.exists()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    st = read_key_file(p)
    assert st.status == "ok" and st.key == key
    Fernet(key)
    assert [x.name for x in p.parent.iterdir()] == [".secret_key"]   # keine .tmp-*-Reste
    with pytest.raises(FileExistsError):
        write_new_key_file(p)                                          # nie ueberschreiben
    assert read_key_file(p).key == key


@skip_as_root
def test_write_new_key_file_unwritable_dir(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        with pytest.raises(OSError):
            write_new_key_file(d / ".secret_key")
        assert list(d.iterdir()) == []
    finally:
        os.chmod(d, 0o700)


def test_write_new_key_file_cleans_up_on_failed_replace(tmp_path, monkeypatch):
    p = tmp_path / ".secret_key"

    def _fail(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(secret_store.os, "replace", _fail)
    with pytest.raises(OSError):
        write_new_key_file(p)
    assert list(tmp_path.iterdir()) == []


# --- Entscheidungsmatrix (§5.3) ----------------------------------------------------------------

def _fs(status: str, key: bytes | None = None, mode_ok: bool = True) -> FileKeyState:
    return FileKeyState(status, key, None if status == "ok" else ("x" if status != "missing" else None), mode_ok)


P = DEFAULT_KEY_FILE
EXPL = secret_store.Path("/run/secrets/pdns_manager_secret_key")

# (id, env_primary, env_previous, explicit, file_state, real_encrypted, erwartet)
# erwartet: ("abort", code) | (action, source, decrypt_only, issues, fallback/generated_reason, previous_keys)
MATRIX = [
    ("0-env-ungueltig", "kaputt", (), False, _fs("ok", KA), 0, ("abort", "KEY_INVALID")),
    ("0-previous-ungueltig", KEY_A, ("kaputt",), False, _fs("missing"), 0, ("abort", "KEY_INVALID")),
    ("0-previous-ungueltig-ohne-primary", "", ("kaputt",), False, _fs("ok", KA), 0, ("abort", "KEY_INVALID")),
    ("1-env", KEY_A, (), False, _fs("missing"), 0, ("use", "env", (), (), None, 0)),
    ("1-env-mit-werten", KEY_A, (), False, _fs("missing"), 7, ("use", "env", (), (), None, 0)),
    ("1-env+previous", KEY_A, (KEY_B,), False, _fs("missing"), 3, ("use", "env", (KB,), (), None, 1)),
    ("1-env+gleiche-datei", KEY_A, (), False, _fs("ok", KA), 3, ("use", "env", (), (), None, 0)),
    ("1-env+andere-datei(nur-datei-fallback)", KEY_A, (), False, _fs("ok", KB), 3,
     ("use", "env", (KB,), ("key_file_obsolete",), None, 0)),
    ("1-env+andere-datei-0644", KEY_A, (), False, _fs("ok", KB, mode_ok=False), 0,
     ("use", "env", (KB,), ("key_file_obsolete", "key_file_permissions"), None, 0)),
    ("1-env+previous+datei", KEY_A, (KEY_B,), False, _fs("ok", KC), 1,
     ("use", "env", (KB, KC), ("key_file_obsolete",), None, 1)),
    ("1-env+datei-unlesbar", KEY_A, (), False, _fs("unreadable"), 5, ("use", "env", (), (), None, 0)),
    ("1-env+datei-ungueltig", KEY_A, (), False, _fs("invalid"), 5, ("use", "env", (), (), None, 0)),
    ("1-env+explizite-datei-fehlt", KEY_A, (), True, _fs("missing"), 0, ("use", "env", (), (), None, 0)),
    ("2-datei", "", (), False, _fs("ok", KA), 4, ("use", "file", (), (), None, 0)),
    ("2-datei-0644", "", (), False, _fs("ok", KA, mode_ok=False), 0,
     ("use", "file", (), ("key_file_permissions",), None, 0)),
    ("2-datei+previous", "", (KEY_B,), False, _fs("ok", KA), 4, ("use", "file", (KB,), (), None, 1)),
    ("2-datei+previous-gleich", "", (KEY_A,), False, _fs("ok", KA), 4, ("use", "file", (), (), None, 0)),
    ("2-explizite-datei-ok", "", (), True, _fs("ok", KA), 0, ("use", "file", (), (), None, 0)),
    ("3-nur-previous", "", (KEY_B,), False, _fs("missing"), 0, ("abort", "KEY_CONFIG")),
    ("3-nur-previous-datei-ungueltig", "", (KEY_B,), False, _fs("invalid"), 0, ("abort", "KEY_CONFIG")),
    ("4-explizite-datei-fehlt-ohne-werte", "", (), True, _fs("missing"), 0, ("abort", "KEY_FILE_MISSING")),
    ("4-explizite-datei-fehlt-mit-werten", "", (), True, _fs("missing"), 2, ("abort", "KEY_FILE_MISSING")),
    ("4-explizite-datei-ungueltig", "", (), True, _fs("invalid"), 0, ("abort", "KEY_FILE_INVALID")),
    ("4-explizite-datei-unlesbar-ohne-werte", "", (), True, _fs("unreadable"), 0, ("abort", "KEY_FILE_UNREADABLE")),
    ("5-unlesbar-mit-werten", "", (), False, _fs("unreadable"), 3, ("abort", "KEY_FILE_UNREADABLE")),
    ("5-unlesbar-ohne-werte", "", (), False, _fs("unreadable"), 0,
     ("fallback", None, (), (), "key_file_unreadable", 0)),
    ("6-ungueltig-mit-werten", "", (), False, _fs("invalid"), 3, ("abort", "KEY_FILE_INVALID")),
    ("6-ungueltig-ohne-werte", "", (), False, _fs("invalid"), 0,
     ("replace_invalid", "generated", (), (), "previous_file_invalid", 0)),
    ("7-fehlt-mit-werten", "", (), False, _fs("missing"), 1, ("abort", "KEY_MISSING")),
    ("7-fehlt-ohne-werte", "", (), False, _fs("missing"), 0, ("generate", "generated", (), (), "first_start", 0)),
]


@pytest.mark.parametrize(
    ("env_primary", "env_previous", "explicit", "file_state", "real_encrypted", "expected"),
    [m[1:] for m in MATRIX],
    ids=[m[0] for m in MATRIX],
)
def test_decision_matrix(env_primary, env_previous, explicit, file_state, real_encrypted, expected):
    cfg = KeyConfig(env_primary, env_previous, EXPL if explicit else P, explicit)
    if expected[0] == "abort":
        with pytest.raises(SecretsStartupError) as ei:
            _decide(cfg, file_state, real_encrypted)
        assert ei.value.code == expected[1]
        text_all = "\n".join(ei.value.log_lines())
        assert KEY_A not in text_all and KEY_B not in text_all and KEY_C not in text_all
        if real_encrypted and expected[1] in ("KEY_MISSING", "KEY_FILE_INVALID"):
            assert f"{real_encrypted} verschluesselte Werte" in text_all
        return
    action, source, decrypt_only, issues, reason, previous_keys = expected
    d = _decide(cfg, file_state, real_encrypted)
    assert d.action == action
    assert d.key_source == source
    assert d.decrypt_only == decrypt_only
    assert set(d.issues) == set(issues)
    assert d.previous_keys == previous_keys
    if action == "use":
        assert d.primary == (KA if env_primary == KEY_A else file_state.key)
    if action == "fallback":
        assert d.fallback_reason == reason and d.primary is None
    if action in ("generate", "replace_invalid"):
        assert d.generated_reason == reason and d.primary is None


def test_decision_matrix_covers_all_rows():
    rows = {m[0].split("-")[0] for m in MATRIX}
    assert rows == {"0", "1", "2", "3", "4", "5", "6", "7"}   # Zeile 8 (KEY_MISMATCH) siehe init_secrets_sync
    assert len(MATRIX) >= 12


def test_env_with_unusable_file_logs_warning(tmp_path, caplog):
    d = tmp_path / ".secret_key"
    d.mkdir()   # Verzeichnis statt Datei -> unlesbar
    cfg = KeyConfig(KEY_A, (), d, False)
    eng = _make_db()
    with caplog.at_level(logging.WARNING, logger="app.core.secrets"):
        with eng.begin() as conn:
            report, box = init_secrets_sync(conn, cfg)
    assert report.key_source == "env" and box is not None
    assert "wird ignoriert" in caplog.text


# --- Startmigration gegen SQLite --------------------------------------------------------------

DDL = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(100) NOT NULL, totp_enabled INTEGER DEFAULT 0, "
    "totp_secret VARCHAR(512), totp_pending_secret VARCHAR(512))",
    "CREATE TABLE server_configs (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, url VARCHAR(500) NOT NULL, "
    "api_key TEXT NOT NULL, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE webhooks (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, "
    "url TEXT NOT NULL, secret TEXT NOT NULL, is_active INTEGER DEFAULT 1)",
    "CREATE TABLE system_settings (id INTEGER PRIMARY KEY, `key` VARCHAR(100) NOT NULL UNIQUE, `value` TEXT)",
]

SECRET_SETTING_ROWS = {"smtp_password": "smtp-pw", "captcha_secret_key": "cap-secret", "metrics_token": "mt"}


def _make_db(seed: bool = False, encrypt_with: SecretBox | None = None):
    """SQLite-DB mit den Secret-Tabellen; seed=True legt Klartext-Bestand wie unter 2.4.1 an."""
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        if seed:
            enc = (lambda v: encrypt_with.encrypt(v)) if encrypt_with else (lambda v: v)
            conn.execute(text("INSERT INTO users (id, username, totp_enabled, totp_secret, totp_pending_secret) VALUES "
                              "(1, 'admin', 1, :t1, NULL), (2, 'bob', 0, NULL, :t2), (3, 'carol', 0, '', '')"),
                         {"t1": enc("JBSWY3DPEHPK3PXP"), "t2": enc("KRSXG5CTMVRXEZLU")})
            conn.execute(text("INSERT INTO server_configs (id, name, url, api_key) VALUES "
                              "(1, 'ns1', 'http://ns1:8081', :k1), (2, 'ns2', 'http://ns2:8081', :k2)"),
                         {"k1": enc("pdns-key-1"), "k2": enc("pdns-key-2")})
            conn.execute(text("INSERT INTO webhooks (id, user_id, name, url, secret) VALUES "
                              "(1, 2, 'slack', :u1, :s1), (2, 99, 'verwaist', :u2, :s2)"),
                         {"u1": enc("https://hooks.slack.com/services/T000/B000/XXXX"), "s1": enc("whsec-1"),
                          "u2": enc("https://example.com/hook"), "s2": enc("whsec-2")})
            for i, (k, v) in enumerate(SECRET_SETTING_ROWS.items(), start=1):
                conn.execute(text("INSERT INTO system_settings (id, `key`, `value`) VALUES (:i, :k, :v)"),
                             {"i": i, "k": k, "v": enc(v)})
            conn.execute(text("INSERT INTO system_settings (id, `key`, `value`) VALUES "
                              "(10, 'smtp_host', 'mail.example.com'), (11, 'ldap_bind_password', NULL), "
                              "(12, 'oidc_client_secret', '')"))
    return eng


def _dump(eng) -> dict:
    with eng.connect() as conn:
        return {
            "users": conn.execute(text("SELECT id, totp_secret, totp_pending_secret FROM users ORDER BY id")).all(),
            "servers": conn.execute(text("SELECT id, api_key FROM server_configs ORDER BY id")).all(),
            "webhooks": conn.execute(text("SELECT id, url, secret FROM webhooks ORDER BY id")).all(),
            "settings": conn.execute(text("SELECT `key`, `value` FROM system_settings ORDER BY id")).all(),
        }


def _all_secret_raw(dump: dict) -> list:
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


def test_init_secrets_sync_migrates_and_is_idempotent(tmp_path):
    eng = _make_db(seed=True)
    key_file = tmp_path / ".secret_key"
    cfg = KeyConfig("", (), key_file, False)
    report, box = _run(eng, cfg)
    assert report.mode == "encrypted" and box is not None
    assert report.key_generated and report.generated_reason == "first_start" and report.key_source == "generated"
    assert report.migrated == {
        "users.totp_secret": 1, "users.totp_pending_secret": 1,
        "server_configs.api_key": 2, "webhooks.secret": 2, "webhooks.url": 2,
        "system_settings.smtp_password": 1, "system_settings.captcha_secret_key": 1,
        "system_settings.metrics_token": 1,
    }
    assert report.rotated == {} and report.unreadable == []
    assert report.fingerprint == box.fingerprint == secret_store.key_fingerprint(read_key_file(key_file).key)
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    dump1 = _dump(eng)
    assert all(v.startswith(PREFIX) for v in _all_secret_raw(dump1))
    assert len(_all_secret_raw(dump1)) == 11
    # leere/NULL-Werte und Nicht-Secrets bleiben unangetastet
    assert dump1["users"][2] == (3, "", "")
    assert ("smtp_host", "mail.example.com") in dump1["settings"]
    assert ("ldap_bind_password", None) in dump1["settings"] and ("oidc_client_secret", "") in dump1["settings"]
    # entschluesselbar zu den Originalwerten
    assert box.decrypt(dump1["webhooks"][0][1]) == "https://hooks.slack.com/services/T000/B000/XXXX"
    assert box.decrypt(dump1["servers"][1][1]) == "pdns-key-2"

    report2, box2 = _run(eng, cfg)
    assert report2.migrated == {} and report2.rotated == {} and not report2.key_generated
    assert report2.key_source == "file" and box2.fingerprint == box.fingerprint
    assert _dump(eng) == dump1   # byte-gleich


def test_init_secrets_sync_key_missing_aborts(tmp_path):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    before = _dump(eng)
    key_file = tmp_path / ".secret_key"
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig("", (), key_file, False))
    assert ei.value.code == "KEY_MISSING"
    assert "11 verschluesselte Werte" in "\n".join(ei.value.lines)
    assert "reset-unreadable" in "\n".join(ei.value.lines)
    assert not key_file.exists() and list(tmp_path.iterdir()) == []
    assert _dump(eng) == before


def test_init_secrets_sync_key_mismatch_aborts(tmp_path):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    before = _dump(eng)
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig(KEY_B, (), tmp_path / ".secret_key", False))
    exc = ei.value
    assert exc.code == "KEY_MISMATCH"
    joined = "\n".join(exc.log_lines())
    assert secret_store.key_fingerprint(KEY_B) in joined and "Quelle env" in joined
    assert KEY_A not in joined and KEY_B not in joined
    assert _dump(eng) == before
    assert not (tmp_path / ".secret_key").exists()


def test_init_secrets_sync_file_fallback_rotates(tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text(KEY_A + "\n")
    key_file.chmod(0o600)
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    report, box = _run(eng, KeyConfig(KEY_B, (), key_file, False))
    assert report.key_source == "env" and report.decrypt_only_keys == 1
    assert "key_file_obsolete" in report.static_issues
    assert sum(report.rotated.values()) == 11 and report.migrated == {}
    only_b = _box(KB)
    assert all(only_b.classify(v) == "encrypted" for v in _all_secret_raw(_dump(eng)))
    # zweiter Lauf: nichts mehr umzuschluesseln
    report2, _ = _run(eng, KeyConfig(KEY_B, (), key_file, False))
    assert report2.rotated == {}


def test_init_secrets_sync_previous_env_rotates(tmp_path):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    report, _ = _run(eng, KeyConfig(KEY_B, (KEY_A,), tmp_path / ".secret_key", False))
    assert report.rotated["webhooks.url"] == 2 and report.previous_keys == 1
    assert not (tmp_path / ".secret_key").exists()   # Env-Schluessel -> keine Datei


def test_init_secrets_sync_partial_unreadable_continues(tmp_path):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    foreign = _box(KC).encrypt("fremd")
    with eng.begin() as conn:
        conn.execute(text("UPDATE webhooks SET secret = :v WHERE id = 1"), {"v": foreign})
        conn.execute(text("UPDATE server_configs SET api_key = 'neu-im-klartext' WHERE id = 2"))
    report, box = _run(eng, KeyConfig(KEY_A, (), tmp_path / ".secret_key", False))
    assert report.mode == "encrypted"
    assert [(u.field, u.kind, u.row_id, u.name, u.owner) for u in report.unreadable] == [
        ("webhooks.secret", "webhook", 1, "slack", "bob")]
    assert report.migrated == {"server_configs.api_key": 1}
    dump = _dump(eng)
    assert dump["webhooks"][0][2] == foreign            # unlesbarer Wert bleibt erhalten
    assert box.decrypt(dump["servers"][1][1]) == "neu-im-klartext"


def test_init_secrets_sync_owner_none_for_orphan_webhook(tmp_path):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    with eng.begin() as conn:
        conn.execute(text("UPDATE webhooks SET url = 'enc:v1:kaputt' WHERE id = 2"))
    report, _ = _run(eng, KeyConfig(KEY_A, (), tmp_path / ".secret_key", False))
    assert [(u.field, u.name, u.owner) for u in report.unreadable] == [("webhooks.url", "verwaist", None)]


def test_plaintext_fallback_when_key_cannot_be_written(tmp_path, monkeypatch):
    eng = _make_db(seed=True)
    before = _dump(eng)

    def _fail(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(secret_store, "write_new_key_file", _fail)
    report, box = _run(eng, KeyConfig("", (), tmp_path / ".secret_key", False))
    assert box is None
    assert report.mode == "plaintext_fallback" and report.fallback_reason == "key_file_unwritable"
    assert report.key_source is None and not report.key_generated
    assert _dump(eng) == before


@skip_as_root
def test_plaintext_fallback_when_dir_unwritable(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        eng = _make_db(seed=True)
        before = _dump(eng)
        report, box = _run(eng, KeyConfig("", (), d / ".secret_key", False))
        assert box is None and report.mode == "plaintext_fallback"
        assert report.fallback_reason == "key_file_unwritable"
        assert _dump(eng) == before
    finally:
        os.chmod(d, 0o700)


def test_plaintext_fallback_when_default_file_unreadable_and_no_ciphertext(tmp_path):
    key_path = tmp_path / ".secret_key"
    key_path.mkdir()   # unlesbar (auch als root)
    eng = _make_db(seed=True)
    before = _dump(eng)
    report, box = _run(eng, KeyConfig("", (), key_path, False))
    assert box is None and report.fallback_reason == "key_file_unreadable"
    assert _dump(eng) == before


def test_unreadable_default_file_with_ciphertext_aborts(tmp_path):
    key_path = tmp_path / ".secret_key"
    key_path.mkdir()
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig("", (), key_path, False))
    assert ei.value.code == "KEY_FILE_UNREADABLE"
    assert "chown -R 1001:1001 /app/data" in "\n".join(ei.value.lines)


def test_invalid_default_file_replaced_when_no_ciphertext(tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("kaputt")
    eng = _make_db(seed=True)
    report, box = _run(eng, KeyConfig("", (), key_file, False))
    assert report.key_generated and report.generated_reason == "previous_file_invalid"
    assert report.key_source == "generated" and sum(report.migrated.values()) == 11
    moved = [p for p in tmp_path.iterdir() if p.name.startswith(".secret_key.invalid-")]
    assert len(moved) == 1 and moved[0].read_text() == "kaputt"
    assert read_key_file(key_file).key is not None
    assert box.fingerprint == secret_store.key_fingerprint(read_key_file(key_file).key)


def test_invalid_default_file_with_ciphertext_aborts_and_keeps_file(tmp_path):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("kaputt")
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig("", (), key_file, False))
    assert ei.value.code == "KEY_FILE_INVALID"
    assert key_file.read_text() == "kaputt" and len(list(tmp_path.iterdir())) == 1


def test_invalid_default_file_not_replaceable_falls_back(tmp_path, monkeypatch):
    key_file = tmp_path / ".secret_key"
    key_file.write_text("kaputt")
    eng = _make_db(seed=True)

    def _fail(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(secret_store, "_move_invalid_key_file", _fail)
    report, box = _run(eng, KeyConfig("", (), key_file, False))
    assert box is None and report.fallback_reason == "key_file_invalid"


def test_explicit_key_file_missing_aborts_without_values(tmp_path):
    eng = _make_db(seed=False)
    key_file = tmp_path / "docker-secret"
    with pytest.raises(SecretsStartupError) as ei:
        _run(eng, KeyConfig("", (), key_file, True))
    assert ei.value.code == "KEY_FILE_MISSING"
    assert not key_file.exists()


def test_empty_database_generates_key_without_migration(tmp_path):
    eng = _make_db(seed=False)
    report, box = _run(eng, KeyConfig("", (), tmp_path / ".secret_key", False))
    assert report.key_generated and report.migrated == {} and box is not None


class _FakeConn:
    """Verbindung mit MariaDB-Dialektnamen, liefert vorgegebene information_schema-Zeilen."""

    def __init__(self, rows):
        self.dialect = SimpleNamespace(name="mariadb")
        self._rows = rows
        self.sql = []

    def execute(self, stmt, *_a, **_k):
        self.sql.append(str(stmt))
        return SimpleNamespace(all=lambda: self._rows)


def test_column_width_check_on_mariadb():
    ok_rows = [("server_configs", "api_key", "text", 65535), ("webhooks", "secret", "mediumtext", None),
               ("webhooks", "url", "text", 65535), ("users", "totp_secret", "varchar", 512),
               ("users", "totp_pending_secret", "text", 65535)]
    conn = _FakeConn(ok_rows)
    secret_store._check_column_widths(conn)
    assert "information_schema.COLUMNS" in conn.sql[0] and "('webhooks','url')" in conn.sql[0]

    narrow = [("server_configs", "api_key", "varchar", 500), ("webhooks", "secret", "text", 65535),
              ("users", "totp_secret", "varchar", 64), ("users", "totp_pending_secret", "varchar", 512)]
    with pytest.raises(SecretsStartupError) as ei:
        secret_store._check_column_widths(_FakeConn(narrow))
    lines = "\n".join(ei.value.lines)
    assert ei.value.code == "SCHEMA_TOO_NARROW"
    assert "server_configs.api_key: Ist VARCHAR(500), Soll TEXT" in lines
    assert "users.totp_secret: Ist VARCHAR(64), Soll VARCHAR(512) oder TEXT" in lines
    assert "webhooks.url: Ist fehlt" in lines
    assert "webhooks.secret" not in lines and "totp_pending_secret" not in lines


# --- init_secrets (async) ---------------------------------------------------------------------

class _FakeAsyncConn:
    def __init__(self, conn):
        self._conn = conn

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._conn, *args, **kwargs)


class _FakeAsyncEngine:
    """AsyncEngine-Ersatz ueber SQLite (aiosqlite ist nicht im Testimage); begin() = eine Transaktion."""

    def __init__(self, eng):
        self.eng = eng

    @contextlib.asynccontextmanager
    async def begin(self):
        with self.eng.begin() as conn:
            yield _FakeAsyncConn(conn)


@pytest.fixture
def audit_calls(monkeypatch):
    import app.services.audit as audit_mod

    calls = []

    async def _rec(action, resource_type, resource_name=None, **kwargs):
        calls.append({"action": action, "resource_type": resource_type, "resource_name": resource_name, **kwargs})

    monkeypatch.setattr(audit_mod, "write_audit_detached", _rec)
    return calls


def test_init_secrets_activates_state_and_audits(tmp_path, audit_calls, caplog):
    eng = _make_db(seed=True)
    cfg = KeyConfig("", (), tmp_path / ".secret_key", False)
    with caplog.at_level(logging.INFO, logger="app.core.secrets"):
        report = asyncio.run(init_secrets(_FakeAsyncEngine(eng), cfg))
    assert secret_store._STATE.mode == "encrypted" and secret_store._STATE.report is report
    assert "Geheimnis-Verschluesselung aktiv" in caplog.text
    assert "pdns-key-1" not in caplog.text and "whsec-1" not in caplog.text
    stored = secret_store.encrypt_value("neu")
    assert secret_store.decrypt_value(stored, label="x") == "neu"
    assert [c["action"] for c in audit_calls] == ["SECRETS_KEY_GENERATED", "SECRETS_MIGRATE"]
    for c in audit_calls:
        assert c["status"] == "success" and c["user_id"] is None
        assert (c["resource_type"], c["resource_name"]) == ("system", "secrets")
    gen, mig = audit_calls
    assert gen["details"]["reason"] == "first_start" and gen["details"]["key_fingerprint"] == report.fingerprint
    assert mig["details"]["migrated"]["webhooks.url"] == 2
    assert "pdns-key" not in repr(audit_calls)

    audit_calls.clear()
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), cfg))
    assert audit_calls == []   # nichts zu tun -> kein Audit


def test_init_secrets_rotation_audit(tmp_path, audit_calls):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig(KEY_B, (KEY_A,), tmp_path / ".k", False)))
    assert [c["action"] for c in audit_calls] == ["SECRETS_ROTATE"]
    assert audit_calls[0]["details"]["decrypt_only_keys"] == 1
    assert sum(audit_calls[0]["details"]["rotated"].values()) == 11


def test_init_secrets_audit_failure_does_not_abort(tmp_path, monkeypatch):
    import app.services.audit as audit_mod

    async def _boom(*_a, **_k):
        raise RuntimeError("DB weg")

    monkeypatch.setattr(audit_mod, "write_audit_detached", _boom)
    eng = _make_db(seed=True)
    report = asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), tmp_path / ".k", False)))
    assert report.mode == "encrypted"


def test_init_secrets_startup_error_passes_through(tmp_path, audit_calls):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    with pytest.raises(SecretsStartupError) as ei:
        asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), tmp_path / ".k", False)))
    assert ei.value.code == "KEY_MISSING"
    assert secret_store._STATE.mode == "uninitialized" and audit_calls == []


def test_init_secrets_db_error_is_migration_failed(tmp_path, audit_calls):
    eng = create_engine("sqlite://")   # keine Tabellen -> OperationalError beim Scan
    with pytest.raises(SecretsStartupError) as ei:
        asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), tmp_path / ".k", False)))
    assert ei.value.code == "MIGRATION_FAILED"
    assert ei.value.lines[0] == "Datenbankfehler: OperationalError"
    assert ei.value.__cause__ is not None
    assert not (tmp_path / ".k").exists()


def test_init_secrets_fallback_logs_error_without_audit(tmp_path, monkeypatch, audit_calls, caplog):
    key_path = tmp_path / ".secret_key"
    key_path.mkdir()
    eng = _make_db(seed=True)
    with caplog.at_level(logging.ERROR, logger="app.core.secrets"):
        report = asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), key_path, False)))
    assert report.mode == "plaintext_fallback" and secret_store._STATE.mode == "plaintext_fallback"
    assert "chown -R 1001:1001 /app/data" in caplog.text
    assert audit_calls == []
    assert secret_store.encrypt_value("x") == "x"


# --- Status (collect_status) ------------------------------------------------------------------

class _FakeAsyncSession:
    def __init__(self, session):
        self._s = session

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._s, *args, **kwargs)


def _status(eng):
    with Session(eng) as s:
        return asyncio.run(secret_store.collect_status(_FakeAsyncSession(s)))


def test_collect_status_after_startup(tmp_path, audit_calls):
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    with eng.begin() as conn:
        conn.execute(text("UPDATE webhooks SET secret = :v WHERE id = 1"), {"v": _box(KC).encrypt("fremd")})
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig(KEY_A, (), tmp_path / ".k", False)))
    with eng.begin() as conn:   # nach dem Start im Klartext geschrieben (z. B. Fremdwerkzeug)
        conn.execute(text("UPDATE server_configs SET api_key = 'klartext' WHERE id = 1"))
    st = _status(eng)
    assert st["mode"] == "encrypted" and st["key_source"] == "env"
    assert st["key_fingerprint"] == secret_store.key_fingerprint(KEY_A)
    assert [c["id"] for c in st["columns"]] == [
        "server_configs.api_key", "webhooks.secret", "webhooks.url", "users.totp_secret",
        "users.totp_pending_secret", "system_settings.smtp_password", "system_settings.captcha_secret_key",
        "system_settings.oidc_client_secret", "system_settings.ldap_bind_password", "system_settings.metrics_token",
    ]
    cols = {c["id"]: c for c in st["columns"]}
    assert cols["server_configs.api_key"] == {"id": "server_configs.api_key", "encrypted": 1, "encrypted_old": 0,
                                              "plaintext": 1, "unreadable": 0, "empty": 0}
    assert cols["webhooks.secret"]["unreadable"] == 1 and cols["webhooks.secret"]["encrypted"] == 1
    assert cols["users.totp_secret"]["empty"] == 2
    assert cols["system_settings.oidc_client_secret"]["empty"] == 1
    assert st["unreadable"] == [{"kind": "webhook", "field": "webhooks.secret", "id": 1, "name": "slack", "owner": "bob"}]
    assert set(st["issues"]) == {"unreadable_values", "plaintext_values"}
    assert st["health"] == "error"
    assert st["last_startup"]["migrated"] == 0 and st["last_startup"]["at"].endswith("+00:00")
    assert st["key_file_exists"] is False
    blob = repr(st)
    assert "klartext" not in blob and "pdns-key" not in blob and PREFIX not in blob


def test_collect_status_setting_unreadable_item(tmp_path, audit_calls):
    eng = _make_db(seed=True)
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig(KEY_A, (), tmp_path / ".k", False)))
    with eng.begin() as conn:
        conn.execute(text("UPDATE system_settings SET `value` = 'enc:v1:kaputt' WHERE `key` = 'smtp_password'"))
    st = _status(eng)
    assert st["unreadable"] == [{"kind": "setting", "field": "system_settings.smtp_password", "id": None,
                                 "name": "smtp_password", "owner": None}]


def test_collect_status_generated_key_and_previous_unused(tmp_path, audit_calls):
    eng = _make_db(seed=True)
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), tmp_path / ".secret_key", False)))
    st = _status(eng)
    assert st["key_source"] == "generated" and st["key_file_exists"] is True
    assert st["issues"] == ["key_only_in_volume"] and st["health"] == "ok"
    assert st["last_startup"]["key_generated"] is True and st["last_startup"]["migrated"] == 11

    secret_store.reset_for_tests()
    eng2 = _make_db(seed=True, encrypt_with=_box(KA))
    asyncio.run(init_secrets(_FakeAsyncEngine(eng2), KeyConfig(KEY_B, (KEY_A,), tmp_path / ".k2", False)))
    st2 = _status(eng2)
    assert "previous_keys_unused" in st2["issues"] and st2["health"] == "ok"
    assert st2["decrypt_only_keys"] == 1


def test_collect_status_previous_still_needed():
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    secret_store.configure_for_tests(KEY_B, [KEY_A])   # ohne Startmigration: Werte noch mit altem Schluessel
    st = _status(eng)
    assert "previous_keys_still_needed" in st["issues"]
    assert sum(c["encrypted_old"] for c in st["columns"]) == 11


def test_collect_status_plaintext_fallback(tmp_path, audit_calls):
    key_path = tmp_path / ".secret_key"
    key_path.mkdir()
    eng = _make_db(seed=True)
    asyncio.run(init_secrets(_FakeAsyncEngine(eng), KeyConfig("", (), key_path, False)))
    st = _status(eng)
    assert st["mode"] == "plaintext_fallback" and st["fallback_reason"] == "key_file_unreadable"
    assert st["issues"] == ["plaintext_fallback"] and st["health"] == "error"
    assert sum(c["plaintext"] for c in st["columns"]) == 11
    assert st["key_fingerprint"] is None


def test_collect_status_without_init_uses_lazy_key(tmp_path, monkeypatch):
    key_file = tmp_path / ".secret_key"
    key_file.write_text(KEY_A)
    key_file.chmod(0o644)
    monkeypatch.setattr(KeyConfig, "from_settings", classmethod(lambda cls, s=None: KeyConfig("", (), key_file, False)))
    eng = _make_db(seed=True, encrypt_with=_box(KA))
    st = _status(eng)
    assert st["mode"] == "encrypted" and st["key_source"] == "file" and st["last_startup"] is None
    assert sum(c["encrypted"] for c in st["columns"]) == 11
    assert "key_only_in_volume" in st["issues"]
