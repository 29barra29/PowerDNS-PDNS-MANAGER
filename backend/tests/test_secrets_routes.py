"""F5 §9.1 test_secrets_routes: Settings-Endpunkte mit verschluesselten Geheimnissen (ohne MariaDB).

Die Router laufen in ``app.main.app`` (inkl. der globalen Exception-Handler) gegen eine SQLite-Datenbank
im Speicher: ``get_db`` liefert eine duenne Async-Huelle um eine synchrone Session (aiosqlite ist nicht im
Image), ``get_admin_session_user`` liefert einen Admin. So laufen echte SQL-Abfragen, die TypeDecorators
und der ``before_flush``-Listener – nur die Fehler-Audits (eigene Session) werden abgefangen.

``SqliteSettingsDb`` und ``settings_client`` nutzen auch ``test_settings_f8_backend.py``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import secrets as secret_store
from app.core.secrets import PREFIX, UNREADABLE, SecretBox, parse_key
from app.core.secret_mask import SECRET_MASK

KEY_A = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="   # = conftest TEST_SECRET_KEY
KEY_B = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="   # fremder Schluessel (-> unlesbar)
FOREIGN = SecretBox(parse_key(KEY_B, source_name="test"))
ADMIN_ID = 1


# ---------------------------------------------------------------------------------------------
# Infrastruktur: SQLite-Datenbank + Async-Huelle + Test-Client
# ---------------------------------------------------------------------------------------------


class AsyncSessionShim:
    """Minimale AsyncSession ueber einer synchronen SQLAlchemy-Session (gleiche Methoden wie im Code genutzt)."""

    def __init__(self, session: Session):
        self.sync_session = session

    async def execute(self, *args, **kwargs):
        return self.sync_session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self.sync_session.scalar(*args, **kwargs)

    def add(self, obj):
        self.sync_session.add(obj)

    async def delete(self, obj):
        self.sync_session.delete(obj)

    async def flush(self):
        self.sync_session.flush()

    async def commit(self):
        self.sync_session.commit()

    async def rollback(self):
        self.sync_session.rollback()

    async def refresh(self, obj):
        self.sync_session.refresh(obj)

    async def close(self):
        return None

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self.sync_session, *args, **kwargs)

    @asynccontextmanager
    async def _nested(self):
        # SAVEPOINTs mit pysqlite sind unzuverlaessig; fuer die Tests reicht ein No-op.
        yield self

    def begin_nested(self):
        return self._nested()


class SqliteSettingsDb:
    """SQLite im Speicher mit den Tabellen, die der Settings-Router braucht."""

    def __init__(self):
        from app.models.models import AuditLog, PanelToken, ServerConfig, SystemSetting, User, Webhook

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (User, ServerConfig, SystemSetting, AuditLog, Webhook, PanelToken):
            model.__table__.create(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, username, hashed_password, role, is_active, totp_enabled, "
                              "must_change_password, auth_source) VALUES (1, 'admin', 'x', 'admin', 1, 0, 0, 'local')"))

    def raw(self, sql: str, params: dict | None = None):
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()

    def raw_value(self, sql: str, params: dict | None = None):
        rows = self.raw(sql, params)
        return rows[0][0] if rows else None

    def exec(self, sql: str, params: dict | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(sql), params or {})

    def setting(self, key: str):
        return self.raw_value("SELECT `value` FROM system_settings WHERE `key` = :k", {"k": key})

    def put_setting(self, key: str, value) -> None:
        self.exec("INSERT INTO system_settings (`key`, `value`) VALUES (:k, :v)", {"k": key, "v": value})

    def audits(self, action: str | None = None) -> list:
        sql = "SELECT action, resource_type, resource_name, status, details, user_id FROM audit_logs"
        params = {}
        if action:
            sql += " WHERE action = :a"
            params["a"] = action
        import json

        out = []
        for row in self.raw(sql + " ORDER BY id", params):
            d = row[4]
            out.append(SimpleNamespace(action=row[0], resource_type=row[1], resource_name=row[2], status=row[3],
                                       details=json.loads(d) if isinstance(d, str) else d, user_id=row[5]))
        return out

    def dependency(self):
        engine = self.engine

        async def _get_db():
            with Session(engine, expire_on_commit=False) as s:
                shim = AsyncSessionShim(s)
                try:
                    yield shim
                    s.commit()
                except Exception:
                    s.rollback()
                    raise

        return _get_db


@pytest.fixture
def sdb():
    secret_store.configure_for_tests(KEY_A)
    db = SqliteSettingsDb()
    yield db
    db.engine.dispose()


@pytest.fixture
def detached_audits(monkeypatch):
    """Fehler-Audits (eigene Session in write_audit_detached) abfangen statt in die MariaDB zu schreiben."""
    from app.services import audit

    calls: list[dict] = []

    async def _fake(action, resource_type, resource_name=None, **kw):
        calls.append({"action": action, "resource_type": resource_type, "resource_name": resource_name, **kw})
        return len(calls)

    monkeypatch.setattr(audit, "write_audit_detached", _fake)
    return calls


@pytest.fixture
def pdns_state():
    """Globalen pdns_manager-Zustand sichern und nach dem Test wiederherstellen."""
    from app.services.pdns_client import pdns_manager

    clients, unloaded = dict(pdns_manager.clients), dict(pdns_manager.unloaded)
    yield pdns_manager
    pdns_manager.clients.clear()
    pdns_manager.clients.update(clients)
    pdns_manager.unloaded.clear()
    pdns_manager.unloaded.update(unloaded)


@pytest.fixture
def settings_client(sdb, detached_audits):
    from app.core.auth import get_admin_session_user
    from app.core.database import get_db
    from app.main import app
    from app.models.models import User

    admin = User(id=ADMIN_ID, username="admin", role="admin", is_active=True, hashed_password="x",
                 preferred_language="de")
    app.dependency_overrides[get_db] = sdb.dependency()
    app.dependency_overrides[get_admin_session_user] = lambda: admin
    client = TestClient(app, raise_server_exceptions=False)
    client.sdb = sdb
    client.detached = detached_audits
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_admin_session_user, None)


def _enc(value: str) -> str:
    return secret_store.encrypt_value(value)


def _add_server(sdb: SqliteSettingsDb, sid: int, name: str, api_key_raw: str, *, active: bool = True) -> None:
    sdb.exec("INSERT INTO server_configs (id, name, display_name, url, api_key, is_active, allow_writes, sort_order) "
             "VALUES (:i, :n, :n, :u, :k, :a, 1, :i)",
             {"i": sid, "n": name, "u": f"http://{name}:8081", "k": api_key_raw, "a": 1 if active else 0})


# ---------------------------------------------------------------------------------------------
# 1. Session-Pflicht (Routen-Walk)
# ---------------------------------------------------------------------------------------------


def _route_walk(routes):
    for r in routes:
        yield r
        inner = getattr(r, "routes", None)
        if inner is None:
            inner = getattr(getattr(r, "original_router", None), "routes", None)
        yield from _route_walk(inner or [])


def test_secrets_status_requires_browser_session():
    from app.main import app

    want = {("/settings/secrets/status", "GET"), ("/settings/servers/{server_id}/api-key", "GET")}
    seen = set()
    for r in _route_walk(app.routes):
        path = (getattr(r, "path", None) or "").removeprefix("/api/v1")
        for m in getattr(r, "methods", None) or []:
            if (path, m) in want:
                names = [getattr(d.call, "__name__", "") for d in r.dependant.dependencies]
                assert "get_admin_session_user" in names, (path, m, names)
                seen.add((path, m))
    assert seen == want


def test_settings_secrets_router_module_contract():
    from app.routers import settings_secrets

    assert settings_secrets.ROUTER_ORDER == 101
    assert settings_secrets.router.prefix == "/settings"
    assert "webhooks.url" in settings_secrets.SECRET_FIELD_IDS
    assert "system_settings.metrics_token" in settings_secrets.SECRET_FIELD_IDS


# ---------------------------------------------------------------------------------------------
# 2. GET /settings/secrets/status
# ---------------------------------------------------------------------------------------------


def test_secrets_status_endpoint_counts_without_values(settings_client):
    sdb = settings_client.sdb
    _add_server(sdb, 1, "ns1", _enc("pdns-key-geheim-1"))
    _add_server(sdb, 2, "ns-fremd", FOREIGN.encrypt("pdns-key-fremd"))
    _add_server(sdb, 3, "ns-klar", "pdns-key-klartext")
    sdb.exec("INSERT INTO webhooks (id, user_id, name, url, secret, events, is_active, created_at, scope, "
             "consecutive_failures) VALUES (1, 1, 'slack', :u, :s, '[]', 1, CURRENT_TIMESTAMP, 'own', 0)",
             {"u": FOREIGN.encrypt("https://hooks.slack.com/services/T/B/X"), "s": _enc("whsec-1")})
    sdb.put_setting("smtp_password", _enc("smtp-geheim"))
    sdb.put_setting("metrics_token", FOREIGN.encrypt("metrics-geheim"))

    r = settings_client.get("/api/v1/settings/secrets/status")
    assert r.status_code == 200, r.text
    data = r.json()
    for secret in ("pdns-key-geheim-1", "pdns-key-fremd", "pdns-key-klartext", "hooks.slack.com", "whsec-1",
                   "smtp-geheim", "metrics-geheim", PREFIX):
        assert secret not in r.text, secret
    cols = {c["id"]: c for c in data["columns"]}
    assert cols["server_configs.api_key"] == {"id": "server_configs.api_key", "encrypted": 1, "encrypted_old": 0,
                                              "plaintext": 1, "unreadable": 1, "empty": 0}
    assert cols["webhooks.url"]["unreadable"] == 1 and cols["webhooks.secret"]["encrypted"] == 1
    assert cols["system_settings.metrics_token"]["unreadable"] == 1
    assert cols["system_settings.ldap_bind_password"] == {"id": "system_settings.ldap_bind_password", "encrypted": 0,
                                                          "encrypted_old": 0, "plaintext": 0, "unreadable": 0,
                                                          "empty": 0}
    items = {(i["kind"], i["field"], i["name"]) for i in data["unreadable"]}
    assert ("server", "server_configs.api_key", "ns-fremd") in items
    assert ("webhook", "webhooks.url", "slack") in items
    assert ("setting", "system_settings.metrics_token", "metrics_token") in items
    owner = next(i for i in data["unreadable"] if i["field"] == "webhooks.url")
    assert owner["owner"] == "admin" and owner["id"] == 1
    assert data["health"] == "error" and "unreadable_values" in data["issues"] and "plaintext_values" in data["issues"]
    assert data["mode"] == "encrypted" and data["key_fingerprint"] == secret_store.key_fingerprint(KEY_A)


def test_secrets_status_schema_accepts_all_field_ids():
    from app.routers.settings_secrets import SECRET_FIELD_IDS, SecretsStatusOut

    payload = secret_store.build_status([])
    out = SecretsStatusOut.model_validate(payload)
    assert [c.id for c in out.columns] == [c["id"] for c in payload["columns"]]
    assert set(SECRET_FIELD_IDS) == {c["id"] for c in payload["columns"]}


# ---------------------------------------------------------------------------------------------
# 3. Server: api_key_status, Reveal, Live-Client
# ---------------------------------------------------------------------------------------------


def test_reveal_unreadable_returns_409(settings_client):
    _add_server(settings_client.sdb, 7, "ns-fremd", FOREIGN.encrypt("pdns-key"))
    r = settings_client.get("/api/v1/settings/servers/7/api-key")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Der API-Key dieses Servers kann nicht entschlüsselt werden")
    assert "pdns-key" not in r.text
    calls = settings_client.detached
    assert len(calls) == 1 and calls[0]["action"] == "REVEAL_API_KEY" and calls[0]["status"] == "error"
    assert calls[0]["error_message"] == "API-Key nicht entschluesselbar"
    assert settings_client.sdb.audits("REVEAL_API_KEY") == []  # kein Erfolgs-Audit


def test_reveal_empty_key_returns_409_without_audit(settings_client):
    _add_server(settings_client.sdb, 8, "ns-leer", "")
    r = settings_client.get("/api/v1/settings/servers/8/api-key")
    assert r.status_code == 409 and r.json()["detail"] == "Für diesen Server ist kein API-Key gespeichert."
    assert settings_client.detached == [] and settings_client.sdb.audits() == []


def test_reveal_readable_key_is_audited(settings_client):
    _add_server(settings_client.sdb, 9, "ns1", _enc("pdns-key-9"))
    r = settings_client.get("/api/v1/settings/servers/9/api-key")
    assert r.status_code == 200 and r.json()["api_key"] == "pdns-key-9"
    audits = settings_client.sdb.audits("REVEAL_API_KEY")
    assert len(audits) == 1 and audits[0].status == "success" and audits[0].user_id == ADMIN_ID


def test_enrich_server_row_reports_unreadable():
    from app.routers.settings import _enrich_server_config_row

    cfg = SimpleNamespace(id=1, name="ns1", display_name="NS1", url="http://ns1:8081", api_key=UNREADABLE,
                          description=None, is_active=False, allow_writes=True, created_at=None, updated_at=None)
    row = asyncio.run(_enrich_server_config_row(cfg))
    assert row["api_key_status"] == "unreadable" and row["has_api_key"] is False and row["api_key"] == ""
    cfg.api_key = ""
    assert asyncio.run(_enrich_server_config_row(cfg))["api_key_status"] == "missing"
    cfg.api_key = "abcdefgh"
    row = asyncio.run(_enrich_server_config_row(cfg))
    assert row["api_key_status"] == "set" and row["api_key"] == "abcd…" and row["has_api_key"] is True


def test_list_servers_reports_api_key_status(settings_client):
    sdb = settings_client.sdb
    _add_server(sdb, 1, "ns-ok", _enc("pdns-key-ok"), active=False)
    _add_server(sdb, 2, "ns-fremd", FOREIGN.encrypt("x"), active=False)
    _add_server(sdb, 3, "ns-leer", "", active=False)
    r = settings_client.get("/api/v1/settings/servers")
    assert r.status_code == 200
    status = {s["name"]: s["api_key_status"] for s in r.json()["servers"]}
    assert status == {"ns-ok": "set", "ns-fremd": "unreadable", "ns-leer": "missing"}


def test_update_server_with_unreadable_key_marks_unloaded(settings_client, pdns_state):
    _add_server(settings_client.sdb, 4, "f5-ns", FOREIGN.encrypt("alt"))
    pdns_state.add_server("f5-ns", "http://f5-ns:8081", "irgendwas")  # z. B. Env-Client gleichen Namens

    r = settings_client.put("/api/v1/settings/servers/4", json={"description": "neu"})
    assert r.status_code == 200, r.text
    assert "f5-ns" not in pdns_state.clients
    assert pdns_state.unloaded.get("f5-ns") == "api key unreadable"
    raw = settings_client.sdb.raw_value("SELECT api_key FROM server_configs WHERE id = 4")
    assert FOREIGN.decrypt(raw) == "alt"  # Chiffretext bleibt erhalten

    r = settings_client.put("/api/v1/settings/servers/4", json={"api_key": "neuer-key"})
    assert r.status_code == 200
    assert pdns_state.clients["f5-ns"].api_key == "neuer-key" and "f5-ns" not in pdns_state.unloaded
    raw = settings_client.sdb.raw_value("SELECT api_key FROM server_configs WHERE id = 4")
    assert raw.startswith(PREFIX) and secret_store.decrypt_value(raw, label="t") == "neuer-key"

    r = settings_client.put("/api/v1/settings/servers/4", json={"is_active": False})
    assert r.status_code == 200 and "f5-ns" not in pdns_state.clients and "f5-ns" not in pdns_state.unloaded


def test_sync_live_client_empty_key(pdns_state):
    from app.routers.settings import _sync_live_client

    _sync_live_client(SimpleNamespace(name="f5-leer", url="http://x", api_key="  ", is_active=True))
    assert pdns_state.unloaded.get("f5-leer") == "api key empty" and "f5-leer" not in pdns_state.clients


def test_server_schemas_limit_api_key_length():
    from pydantic import ValidationError

    from app.routers.settings import ServerConfigCreate, ServerConfigUpdate, TestConnectionRequest

    with pytest.raises(ValidationError):
        ServerConfigCreate(name="a", url="http://a", api_key="")
    with pytest.raises(ValidationError):
        ServerConfigCreate(name="a", url="http://a", api_key="k" * 501)
    with pytest.raises(ValidationError):
        ServerConfigUpdate(api_key="k" * 501)
    with pytest.raises(ValidationError):
        TestConnectionRequest(url="http://a", api_key="k" * 501)
    assert ServerConfigCreate(name="a", url="http://a", api_key="k" * 500).api_key == "k" * 500


def test_load_from_db_configs_skips_unreadable():
    from app.services.pdns_client import PowerDNSManager

    mgr = PowerDNSManager()
    mgr.clients.clear()
    mgr.add_server("ns1", "http://env-ns1:8081", "env-key")
    cfgs = [SimpleNamespace(name="ns1", url="http://ns1:8081", api_key=UNREADABLE, is_active=True),
            SimpleNamespace(name="ns2", url="http://ns2:8081", api_key="ok-key", is_active=True)]
    assert mgr.load_from_db_configs(cfgs) == ["ns1"]
    assert "ns1" not in mgr.clients and mgr.unloaded == {"ns1": "api key unreadable"}
    assert mgr.clients["ns2"].api_key == "ok-key"


# ---------------------------------------------------------------------------------------------
# 4. SMTP
# ---------------------------------------------------------------------------------------------


def _store_smtp(sdb: SqliteSettingsDb, password_raw: str | None, **over) -> None:
    values = {"smtp_host": "mail.example.com", "smtp_port": "587", "smtp_username": "relay",
              "smtp_from_email": "dns@example.com", "smtp_from_name": "DNS", "smtp_encryption": "starttls",
              "smtp_enabled": "true", **over}
    for k, v in values.items():
        sdb.put_setting(k, v)
    if password_raw is not None:
        sdb.put_setting("smtp_password", password_raw)


def _smtp_body(**over) -> dict:
    body = {"host": "mail.example.com", "port": 587, "username": "relay", "password": SECRET_MASK,
            "from_email": "dns@example.com", "from_name": "DNS", "encryption": "starttls", "enabled": True}
    body.update(over)
    return body


def test_smtp_get_masks_and_reports_unreadable(settings_client):
    sdb = settings_client.sdb
    _store_smtp(sdb, _enc("smtp-geheim"))
    data = settings_client.get("/api/v1/settings/smtp").json()
    assert data["password"] == SECRET_MASK and data["password_set"] is True and data["password_unreadable"] is False
    assert "smtp-geheim" not in str(data)

    sdb.exec("UPDATE system_settings SET `value` = :v WHERE `key` = 'smtp_password'", {"v": FOREIGN.encrypt("x")})
    data = settings_client.get("/api/v1/settings/smtp").json()
    assert data["password"] == "" and data["password_set"] is False and data["password_unreadable"] is True


def test_smtp_put_mask_keeps_password_without_rewrite(settings_client):
    sdb = settings_client.sdb
    _store_smtp(sdb, _enc("smtp-geheim"))
    before = sdb.setting("smtp_password")
    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(from_name="Neu"))
    assert r.status_code == 200, r.text
    assert sdb.setting("smtp_password") == before  # byte-gleich: weder gelesen noch neu geschrieben
    assert sdb.setting("smtp_from_name") == "Neu"
    audit = sdb.audits("SMTP_UPDATE")[-1]
    assert audit.details["password_changed"] == "kept" and "smtp-geheim" not in str(audit.details)

    # Ohne Passwort-Feld (None) ebenfalls behalten
    body = _smtp_body()
    body.pop("password")
    assert settings_client.put("/api/v1/settings/smtp", json=body).status_code == 200
    assert sdb.setting("smtp_password") == before


@pytest.mark.parametrize("field,value", [
    ("host", "evil.example.net"), ("port", 2525), ("username", "anderer"), ("encryption", "none"),
])
def test_smtp_put_retarget_requires_password(settings_client, field, value):
    sdb = settings_client.sdb
    _store_smtp(sdb, _enc("smtp-geheim"))
    before = sdb.setting("smtp_password")
    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(**{field: value}))
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "secret_reentry_required" and body["fields"] == [field]
    assert body["detail"].startswith("Zieladresse geändert")
    assert sdb.setting("smtp_password") == before and sdb.setting(f"smtp_{field}") != str(value)
    assert sdb.audits("SMTP_UPDATE") == []


def test_smtp_put_retarget_with_new_password_or_clear(settings_client):
    sdb = settings_client.sdb
    _store_smtp(sdb, _enc("smtp-geheim"))
    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(host="Mail.Example.COM."))
    assert r.status_code == 200  # nur Gross-/Kleinschreibung und Schlusspunkt: gleiches Ziel

    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(host="neu.example.net", password="neu-pw"))
    assert r.status_code == 200
    raw = sdb.setting("smtp_password")
    assert raw.startswith(PREFIX) and secret_store.decrypt_value(raw, label="t") == "neu-pw"
    assert sdb.audits("SMTP_UPDATE")[-1].details["password_changed"] == "set"

    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(host="dritter.example.net", password=""))
    assert r.status_code == 200 and sdb.setting("smtp_password") == ""
    assert sdb.audits("SMTP_UPDATE")[-1].details["password_changed"] == "cleared"


def test_smtp_put_unreadable_password_stays_untouched(settings_client):
    sdb = settings_client.sdb
    foreign = FOREIGN.encrypt("alt")
    _store_smtp(sdb, foreign)
    # Ein unlesbares Passwort kann nicht an ein neues Ziel gehen -> kein Retarget-Fehler, Chiffretext bleibt
    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(host="neu.example.net"))
    assert r.status_code == 200, r.text
    assert sdb.setting("smtp_password") == foreign


def test_smtp_test_endpoint_guard_and_audit(settings_client, monkeypatch):
    from app.services import email_service

    seen: list[dict] = []

    async def _fake_test(settings):
        seen.append(dict(settings))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(email_service, "test_smtp_connection", _fake_test)
    sdb = settings_client.sdb
    _store_smtp(sdb, _enc("smtp-geheim"))

    # Ohne Body: gespeicherte Werte inkl. Passwort
    r = settings_client.post("/api/v1/settings/smtp/test")
    assert r.status_code == 200 and r.json()["success"] is True
    assert seen[-1]["host"] == "mail.example.com" and seen[-1]["password"] == "smtp-geheim"
    audit = sdb.audits("SMTP_TEST")[-1]
    assert audit.details == {"kind": "connection", "host": "mail.example.com", "port": 587, "success": True,
                             "unsaved_values": False}

    # Ungespeicherter neuer Host mit gespeichertem Passwort -> 400, kein Verbindungsversuch
    n = len(seen)
    r = settings_client.post("/api/v1/settings/smtp/test", json={"host": "evil.example.net"})
    assert r.status_code == 400 and r.json()["code"] == "secret_reentry_required" and len(seen) == n
    r = settings_client.post("/api/v1/settings/smtp/test", json={"host": "evil.example.net", "password": SECRET_MASK})
    assert r.status_code == 400 and len(seen) == n

    # Mit neu eingegebenem Passwort ok, gespeicherte Werte unveraendert
    r = settings_client.post("/api/v1/settings/smtp/test", json={"host": "neu.example.net", "password": "pw2"})
    assert r.status_code == 200
    assert seen[-1]["host"] == "neu.example.net" and seen[-1]["password"] == "pw2"
    assert sdb.setting("smtp_host") == "mail.example.com"
    assert sdb.audits("SMTP_TEST")[-1].details["unsaved_values"] is True


def test_smtp_test_failure_is_error_audit(settings_client, monkeypatch):
    from app.services import email_service

    async def _fail(settings):
        return {"success": False, "error": "Verbindung fehlgeschlagen: x"}

    monkeypatch.setattr(email_service, "test_smtp_connection", _fail)
    _store_smtp(settings_client.sdb, _enc("pw"))
    r = settings_client.post("/api/v1/settings/smtp/test")
    assert r.status_code == 200 and r.json()["success"] is False
    call = settings_client.detached[-1]
    assert call["action"] == "SMTP_TEST" and call["status"] == "error" and call["details"]["success"] is False
    assert "pw" not in str(call["details"].values())


def test_smtp_test_email_with_unreadable_password(settings_client):
    _store_smtp(settings_client.sdb, FOREIGN.encrypt("alt"))
    r = settings_client.post("/api/v1/settings/smtp/test-email", json={"to_email": "admin@example.com"})
    assert r.status_code == 200
    assert r.json() == {"success": False, "error": "SMTP-Passwort kann nicht entschlüsselt werden – "
                                                   "bitte unter Einstellungen → SMTP neu eintragen."}
    call = settings_client.detached[-1]
    assert call["action"] == "SMTP_TEST" and call["details"]["kind"] == "email"


def test_smtp_unreadable_password_blocks_send():
    from app.services.email_service import SMTP_PASSWORD_UNREADABLE, _test_smtp_connection_sync, send_email

    settings = {"enabled": True, "host": "127.0.0.1", "port": 1, "username": "u", "password": UNREADABLE,
                "from_email": "a@example.com", "encryption": "none"}
    with pytest.raises(RuntimeError) as ei:
        send_email(settings, "b@example.com", "s", "<p>x</p>")
    assert str(ei.value) == SMTP_PASSWORD_UNREADABLE
    assert _test_smtp_connection_sync(settings) == {"success": False, "error": SMTP_PASSWORD_UNREADABLE}


def test_smtp_settings_password_optional_and_limited():
    from pydantic import ValidationError

    from app.routers.settings import SmtpSettings

    assert SmtpSettings().password is None
    with pytest.raises(ValidationError):
        SmtpSettings(password="p" * 501)


def test_smtp_put_without_key_returns_503(settings_client, monkeypatch, tmp_path):
    """Schreibzugriff ohne verfuegbaren Schluessel -> 503 ueber den globalen Handler, kein Klartext."""
    secret_store.reset_for_tests()
    cfg = secret_store.KeyConfig(env_primary="", env_previous=(), file_path=tmp_path / "fehlt", file_explicit=False)
    monkeypatch.setattr(secret_store.KeyConfig, "from_settings", classmethod(lambda cls, s=None: cfg))
    r = settings_client.put("/api/v1/settings/smtp", json=_smtp_body(password="klartext-pw"))
    assert r.status_code == 503
    assert r.json() == {"detail": "Verschlüsselung nicht verfügbar – Geheimnis wurde nicht gespeichert. "
                                  "Bitte Server-Log prüfen."}
    assert settings_client.sdb.setting("smtp_password") is None


# ---------------------------------------------------------------------------------------------
# 5. Captcha
# ---------------------------------------------------------------------------------------------


def _store_captcha(sdb, secret_raw, provider="turnstile", site_key="site-123"):
    sdb.put_setting("captcha_provider", provider)
    sdb.put_setting("captcha_site_key", site_key)
    if secret_raw is not None:
        sdb.put_setting("captcha_secret_key", secret_raw)


def test_captcha_get_reports_unreadable(settings_client):
    sdb = settings_client.sdb
    _store_captcha(sdb, FOREIGN.encrypt("cap-secret"))
    data = settings_client.get("/api/v1/settings/captcha").json()
    assert data == {"provider": "turnstile", "site_key": "site-123", "secret_key": "", "secret_key_set": False,
                    "secret_key_unreadable": True}


def test_captcha_put_encrypts_and_audits(settings_client):
    sdb = settings_client.sdb
    r = settings_client.put("/api/v1/settings/captcha",
                            json={"provider": "hcaptcha", "site_key": "s1", "secret_key": "cap-geheim"})
    assert r.status_code == 200
    raw = sdb.setting("captcha_secret_key")
    assert raw.startswith(PREFIX) and secret_store.decrypt_value(raw, label="t") == "cap-geheim"
    a = sdb.audits("CAPTCHA_UPDATE")[-1]
    assert (a.resource_type, a.resource_name, a.user_id) == ("settings", "captcha", ADMIN_ID)
    assert a.details == {"provider": "hcaptcha", "site_key_set": True, "secret_key_changed": "set"}

    r = settings_client.put("/api/v1/settings/captcha", json={"provider": "hcaptcha", "site_key": "s1",
                                                              "secret_key": SECRET_MASK})
    assert r.status_code == 200 and sdb.setting("captcha_secret_key") == raw
    assert sdb.audits("CAPTCHA_UPDATE")[-1].details["secret_key_changed"] == "kept"

    r = settings_client.put("/api/v1/settings/captcha", json={"provider": "none", "site_key": "", "secret_key": ""})
    assert r.status_code == 200 and sdb.setting("captcha_secret_key") == ""
    assert sdb.audits("CAPTCHA_UPDATE")[-1].details == {"provider": "none", "site_key_set": False,
                                                        "secret_key_changed": "cleared"}


def test_captcha_test_with_unreadable_secret(settings_client):
    _store_captcha(settings_client.sdb, FOREIGN.encrypt("cap-secret"))
    r = settings_client.post("/api/v1/settings/captcha/test", json={"token": "t"})
    assert r.json() == {"success": False,
                        "error": "Captcha-Secret kann nicht entschlüsselt werden – bitte neu eintragen."}


def test_captcha_unreadable_secret_is_fail_open(monkeypatch, caplog):
    from app.services import captcha

    async def _settings(db):
        return {"provider": "turnstile", "site_key": "s", "secret_key": "", "secret_unreadable": True}

    async def _no_verify(**kw):  # darf nicht aufgerufen werden
        raise AssertionError("Provider-Aufruf trotz unlesbarem Secret")

    monkeypatch.setattr(captcha, "get_captcha_settings", _settings)
    monkeypatch.setattr(captcha, "verify_captcha_token", _no_verify)
    monkeypatch.setattr(captcha, "_unreadable_log_state", {})
    with caplog.at_level(logging.ERROR, logger="app.services.captcha"):
        asyncio.run(captcha.verify_or_raise(None, token=None))
        asyncio.run(captcha.verify_or_raise(None, token=None))
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "Captcha-Secret kann nicht entschluesselt werden" in errors[0].getMessage()


def test_captcha_service_reads_and_writes_via_settings_helpers(sdb):
    from app.services import captcha

    with Session(sdb.engine) as s:
        db = AsyncSessionShim(s)
        asyncio.run(captcha.save_captcha_settings(db, provider="recaptcha", site_key=" site ", secret_key=" sec "))
        s.commit()
        assert sdb.setting("captcha_secret_key").startswith(PREFIX) and sdb.setting("captcha_site_key") == "site"
        got = asyncio.run(captcha.get_captcha_settings(db))
        assert got == {"provider": "recaptcha", "site_key": "site", "secret_key": "sec", "secret_unreadable": False}
        assert asyncio.run(captcha.is_captcha_required(db)) is True


# ---------------------------------------------------------------------------------------------
# 6. TOTP (Helfer in routers/auth.py, WS-F2F3 in Welle 1)
# ---------------------------------------------------------------------------------------------


@pytest.mark.wave_integration
def test_totp_secret_state():
    from app.routers.auth import _totp_secret_state

    assert _totp_secret_state(SimpleNamespace(totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")) == "ok"
    assert _totp_secret_state(SimpleNamespace(totp_enabled=True, totp_secret=None)) == "missing"
    assert _totp_secret_state(SimpleNamespace(totp_enabled=True, totp_secret=UNREADABLE)) == "unreadable"
