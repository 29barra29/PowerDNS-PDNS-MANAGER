"""Audit v2 (Bauplan B.7): write_audit mit SAVEPOINT [D2], Kontextfelder, csv_safe [S13],
public_details [S12], Aufbewahrung.

Ohne DB: synchrone SQLite-Session hinter einem kleinen AsyncSession-Adapter (aiosqlite ist nicht im
Testimage); SQLite bekommt das dokumentierte SAVEPOINT-Rezept (BEGIN selbst senden). Mit MariaDB
(``requires_db``): dieselben Kernfaelle ueber den echten aiomysql-Pfad plus Bereinigung.
"""
import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.request_context import (
    TokenScope,
    actor_username_ctx,
    auth_via_ctx,
    client_ip_ctx,
    current_token_scope,
)
from app.models.models import AuditLog, SystemSetting
from app.services import audit as audit_mod
from app.services.audit import (
    ZONE_SCOPED_RESOURCE_TYPES,
    csv_safe,
    parse_retention_days,
    public_details,
    public_error_message,
    write_audit,
    write_audit_detached,
)
from dbutil import requires_db


# ---------------------------------------------------------------------------------------------
# SQLite-Unterbau
# ---------------------------------------------------------------------------------------------

def _sqlite_engine():
    """SQLite mit funktionierenden SAVEPOINTs (pysqlite-Rezept aus der SQLAlchemy-Doku)."""
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    AuditLog.__table__.create(eng)
    SystemSetting.__table__.create(eng)
    return eng


class _AsyncSessionAdapter:
    """Minimaler AsyncSession-Ersatz ueber einer synchronen Session (inkl. begin_nested)."""

    def __init__(self, session: Session):
        self.sync_session = session

    def add(self, obj):
        self.sync_session.add(obj)

    async def flush(self):
        self.sync_session.flush()

    async def execute(self, *args, **kwargs):
        return self.sync_session.execute(*args, **kwargs)

    async def commit(self):
        self.sync_session.commit()

    async def rollback(self):
        self.sync_session.rollback()

    @contextlib.asynccontextmanager
    async def begin_nested(self):
        with self.sync_session.begin_nested():
            yield


@pytest.fixture
def sdb():
    eng = _sqlite_engine()
    # wie app.core.database.async_session: expire_on_commit=False
    with Session(eng, expire_on_commit=False) as s:
        yield eng, _AsyncSessionAdapter(s)
    eng.dispose()


def _rows(eng, sql):
    with eng.connect() as conn:
        return conn.execute(text(sql)).all()


@contextlib.contextmanager
def _ctx(var, value):
    token = var.set(value)
    try:
        yield
    finally:
        var.reset(token)


# ---------------------------------------------------------------------------------------------
# write_audit: SAVEPOINT [D2]
# ---------------------------------------------------------------------------------------------

def test_flush_error_in_savepoint_does_not_poison_session(sdb, caplog):
    eng, db = sdb

    async def run():
        db.add(SystemSetting(key="vorher", value="1"))
        # action=None verletzt NOT NULL -> Flush-Fehler innerhalb des SAVEPOINTs
        with caplog.at_level(logging.ERROR, logger="app.services.audit"):
            res = await write_audit(db, None, "record", "www.example.com.")
        assert res is None
        assert "konnte nicht geschrieben werden" in caplog.text
        # Session bleibt benutzbar: weitere Aenderungen und ein gueltiger Audit-Eintrag
        db.add(SystemSetting(key="nachher", value="2"))
        ok = await write_audit(db, "UPDATE", "record", "www.example.com.", details={"zone": "example.com"})
        assert ok is not None and ok.id is not None
        await db.commit()

    asyncio.run(run())
    assert sorted(r[0] for r in _rows(eng, "SELECT `key` FROM system_settings")) == ["nachher", "vorher"]
    assert _rows(eng, "SELECT action, zone_name FROM audit_logs") == [("UPDATE", "example.com.")]


def test_pending_error_of_caller_is_not_swallowed(sdb):
    """Fehler in Aenderungen des Aufrufers (vor dem Audit) gehoeren dem Aufrufer und werden geworfen."""
    _eng, db = sdb

    async def run():
        db.add(SystemSetting(key=None, value="x"))
        await write_audit(db, "UPDATE", "record", "a.example.com.")

    with pytest.raises(IntegrityError):
        asyncio.run(run())


def test_write_audit_returns_flushed_object_with_fields(sdb):
    eng, db = sdb
    long_name = "x" * 300

    async def run():
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        obj = await write_audit(
            db, "CREATE", "record", long_name, user_id=7, details={"zone": "Example.COM"},
            server_name="ns1", revert_of_id=11,
        )
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        return obj, before, after

    obj, before, after = asyncio.run(run())
    assert obj.id is not None
    assert before - timedelta(seconds=1) <= obj.timestamp <= after + timedelta(seconds=1)
    assert obj.timestamp.tzinfo is None
    row = _rows(eng, "SELECT resource_name, zone_name, revert_of_id, user_id, status FROM audit_logs")[0]
    assert row == ("x" * 255, "example.com.", 11, 7, "success")


@pytest.mark.parametrize(
    "resource_type, details, zone_name, expected",
    [
        ("record", {"zone": "Example.com"}, None, "example.com."),
        ("zone", {"zone": " sub.example.org. "}, None, "sub.example.org."),
        ("acme", {"zone": "acme.example"}, None, "acme.example."),
        ("dyndns", {"zone": "home.example."}, None, "home.example."),
        ("dnssec_key", None, "Signed.Example", "signed.example."),
        # nicht zonenbezogene Typen bekommen nie einen zone_name aus details (kein Leak in den Zonenverlauf)
        ("user", {"zone": "example.com."}, None, None),
        ("settings", {"zone": "example.com."}, None, None),
        # explizit uebergeben gilt immer
        ("user", None, "example.net", "example.net."),
        ("record", {"zone": 5}, None, None),
        ("record", {"zone": "   "}, None, None),
        ("record", "kein-dict", None, None),
    ],
)
def test_zone_name_rules(resource_type, details, zone_name, expected):
    obj = audit_mod._build(
        "X", resource_type, "r", user_id=None, details=details, status="success", error_message=None,
        server_name=None, zone_name=zone_name, revert_of_id=None,
    )
    assert obj.zone_name == expected


def test_zone_scoped_types_match_f7():
    assert ZONE_SCOPED_RESOURCE_TYPES == frozenset({"record", "zone", "dnssec_key", "acme", "dyndns"})


def test_actor_and_client_ip_from_request_context():
    with _ctx(actor_username_ctx, "alice" * 30), _ctx(client_ip_ctx, "2001:db8::1"):
        obj = audit_mod._build(
            "LOGIN", "user", "alice", user_id=1, details=None, status="success", error_message=None,
            server_name=None, zone_name=None, revert_of_id=None,
        )
    assert obj.actor_username == ("alice" * 30)[:100]
    assert obj.client_ip == "2001:db8::1"
    plain = AuditLog(action="X", resource_type="user")   # ohne Kontext
    assert plain.actor_username is None and plain.client_ip is None


def test_auditlog_constructor_adds_reserved_auth_key():
    scope = TokenScope(token_id=4, name="ci", token_prefix="dnsmgr_abcd", zones=None,
                       permission="manage", allow_admin=False)
    with _ctx(auth_via_ctx, "panel_token"), _ctx(current_token_scope, scope):
        a = AuditLog(action="CREATE", resource_type="record", details={"zone": "a."})
        b = AuditLog(action="CREATE", resource_type="record")
        c = AuditLog(action="CREATE", resource_type="record", details={"auth": {"via": "x"}})
    auth = {"via": "panel_token", "token_id": 4, "token_name": "ci", "token_prefix": "dnsmgr_abcd"}
    assert a.details == {"zone": "a.", "auth": auth}
    assert b.details == {"auth": auth}
    assert c.details == {"auth": {"via": "x"}}            # vorhandener Schluessel bleibt
    with _ctx(auth_via_ctx, "session"):
        d = AuditLog(action="CREATE", resource_type="record", details={"zone": "a."})
    assert d.details == {"zone": "a."}


def test_error_status_goes_detached(monkeypatch, sdb):
    eng, db = sdb
    calls = []

    async def _rec(*args, **kwargs):
        calls.append((args, kwargs))
        return 99

    monkeypatch.setattr(audit_mod, "write_audit_detached", _rec)
    res = asyncio.run(write_audit(db, "DELETE", "record", "a.example.", status="error",
                                  error_message="kaputt", zone_name="example.", revert_of_id=3))
    assert res is None
    assert calls == [(("DELETE", "record", "a.example."), {
        "user_id": None, "details": None, "status": "error", "error_message": "kaputt",
        "server_name": None, "zone_name": "example.", "revert_of_id": 3})]
    assert _rows(eng, "SELECT COUNT(*) FROM audit_logs")[0][0] == 0


class _SessionFactory:
    """Ersatz fuer app.core.database.async_session (async with factory() as s)."""

    def __init__(self, eng):
        self.eng = eng

    @contextlib.asynccontextmanager
    async def __call__(self):
        with Session(self.eng, expire_on_commit=False) as s:
            yield _AsyncSessionAdapter(s)


def test_write_audit_detached_returns_id_and_commits(monkeypatch, sdb):
    eng, _db = sdb
    import app.core.database as database

    monkeypatch.setattr(database, "async_session", _SessionFactory(eng))
    with _ctx(client_ip_ctx, "192.0.2.5"):
        new_id = asyncio.run(write_audit_detached(
            "LOGIN_FAILED", "user", "bob", details={"ip": "192.0.2.5"}, status="error", error_message="falsch"))
    assert isinstance(new_id, int)
    assert _rows(eng, "SELECT id, action, status, client_ip, zone_name FROM audit_logs") == [
        (new_id, "LOGIN_FAILED", "error", "192.0.2.5", None)]


def test_write_audit_detached_failure_returns_none(monkeypatch, caplog):
    import app.core.database as database

    class _Broken:
        def __call__(self):
            raise RuntimeError("DB weg")

    monkeypatch.setattr(database, "async_session", _Broken())
    with caplog.at_level(logging.ERROR, logger="app.services.audit"):
        assert asyncio.run(write_audit_detached("X", "user", "y")) is None
    assert "DB weg" in caplog.text


# ---------------------------------------------------------------------------------------------
# csv_safe [S13]
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
        ("+49 123", "'+49 123"),
        ("-1+2", "'-1+2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("www.example.com.", "www.example.com."),
        ("a=b", "a=b"),
        ("", ""),
        (None, ""),
        (42, "42"),
        (-3, "-3"),
        (True, "true"),
        ({"zone": "a.", "n": 1}, json.dumps({"zone": "a.", "n": 1}, ensure_ascii=False)),
        (["=x"], '["=x"]'),
        ("Grüße", "Grüße"),
    ],
)
def test_csv_safe(value, expected):
    assert csv_safe(value) == expected


def test_csv_safe_datetime_is_iso_utc():
    assert csv_safe(datetime(2026, 10, 5, 12, 0, 0)) == "2026-10-05T12:00:00+00:00"


# ---------------------------------------------------------------------------------------------
# public_details [S12]
# ---------------------------------------------------------------------------------------------

def _v2_details():
    return {
        "version": 2,
        "zone": "example.com.",
        "changes": [{"name": "www.example.com.", "type": "A", "before": None,
                     "after": {"ttl": 300, "records": [{"content": "192.0.2.1", "disabled": False}], "comments": []}}],
        "after_source": "reread",
        "fanout": {"ns1": "saved", "ns2": "error: Could not connect to 10.0.0.2: secret path",
                   "ns3": "error: unklar – bitte Zone neu laden", "ns4": "skipped (not loaded: api key unreadable)"},
        "primary_outcome": "ok",
        "client_ip": "198.51.100.7",
        "ip": "198.51.100.7",
        "ip_source": "x-forwarded-for",
        "token_id": 5,
        "token_name": "router",
        "token_prefix": "dnsmgr_ddns_ab",
        "ptr": [{"zone": "2.0.192.in-addr.arpa.", "status": "saved", "client_ip": "198.51.100.7"}],
        "auth": {"via": "panel_token", "token_id": 9, "token_name": "ci", "token_prefix": "dnsmgr_xy"},
        "internal_note": "nur Admins",
    }


def test_public_details_admin_sees_everything():
    det = _v2_details()
    assert public_details(det, admin=True) is det


def test_public_details_non_admin_allowlist():
    out = public_details(_v2_details(), admin=False)
    assert set(out) == {"version", "zone", "changes", "after_source", "fanout", "primary_outcome",
                        "token_name", "ptr", "auth"}
    assert out["ptr"] == [{"zone": "2.0.192.in-addr.arpa.", "status": "saved"}]
    assert out["auth"] == {"via": "panel_token", "token_name": "ci"}
    assert out["fanout"] == {"ns1": "saved", "ns2": "error: PowerDNS-Fehler",
                             "ns3": "error: unklar – bitte Zone neu laden",
                             "ns4": "skipped (not loaded: api key unreadable)"}
    dumped = json.dumps(out)
    for secret in ("198.51.100.7", "dnsmgr_ddns_ab", "dnsmgr_xy", "x-forwarded-for", "nur Admins", "10.0.0.2"):
        assert secret not in dumped


def test_public_details_legacy_v1_and_edge_cases():
    legacy = {"zone": "a.", "type": "A", "old": "1.2.3.4", "new": "1.2.3.5", "fanout": {"ns1": "saved"},
              "ip": "203.0.113.1"}
    assert public_details(legacy, admin=False) == {"zone": "a.", "type": "A", "old": "1.2.3.4", "new": "1.2.3.5",
                                                   "fanout": {"ns1": "saved"}}
    assert public_details(None, admin=False) is None
    assert public_details("text", admin=False) is None
    assert public_details({"auth": "kaputt"}, admin=False) == {}


def test_public_error_message():
    assert public_error_message(None) is None
    assert public_error_message("Rohtext mit Details") == "PowerDNS-Fehler"
    assert public_error_message("x", 422) == "PowerDNS-Fehler (HTTP 422)"


# ---------------------------------------------------------------------------------------------
# Aufbewahrung
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, days", [
    (None, 0), ("", 0), ("abc", 0), ("0", 0), ("6", 0), ("7", 7), (" 30 ", 30), ("3650", 3650), ("3651", 0), ("-7", 0),
])
def test_parse_retention_days(raw, days):
    assert parse_retention_days(raw) == days


# ---------------------------------------------------------------------------------------------
# MariaDB (aiomysql): SAVEPOINT und Bereinigung im echten Treiberpfad
# ---------------------------------------------------------------------------------------------

@requires_db
def test_savepoint_on_mariadb(fresh_db):
    from app.core.database import async_session

    async def run():
        async with async_session() as db:
            await db.execute(text("DELETE FROM system_settings WHERE `key` LIKE 'be1test_%'"))
            db.add(SystemSetting(key="be1test_vorher", value="1"))
            assert await write_audit(db, None, "record", "x.example.") is None
            db.add(SystemSetting(key="be1test_nachher", value="2"))
            ok = await write_audit(db, "BE1TEST", "record", "x.example.", details={"zone": "example."})
            assert ok is not None and ok.id
            await db.commit()
        async with async_session() as db:
            keys = sorted((await db.execute(
                select(SystemSetting.key).where(SystemSetting.key.like("be1test_%")))).scalars())
            audits = (await db.execute(
                select(AuditLog.action, AuditLog.zone_name).where(AuditLog.action == "BE1TEST"))).all()
            await db.execute(text("DELETE FROM system_settings WHERE `key` LIKE 'be1test_%'"))
            await db.execute(text("DELETE FROM audit_logs WHERE action = 'BE1TEST'"))
            await db.commit()
        return keys, audits

    keys, audits = asyncio.run(run())
    assert keys == ["be1test_nachher", "be1test_vorher"]
    assert [tuple(a) for a in audits] == [("BE1TEST", "example.")]


@requires_db
def test_purge_expired_audit_logs_on_mariadb(fresh_db):
    from app.core.database import async_session
    from app.services.system_settings import get_settings, set_setting

    now = datetime(2026, 10, 5, 12, 0, 0)

    async def run():
        async with async_session() as db:
            await db.execute(text("DELETE FROM audit_logs"))
            for days in (40, 31, 29, 1):
                db.add(AuditLog(timestamp=now - timedelta(days=days), action="BE1OLD", resource_type="record",
                                status="success"))
            await set_setting(db, audit_mod.RETENTION_KEY, "30")
            await db.commit()
        deleted = await audit_mod.purge_expired_audit_logs(now=now)
        async with async_session() as db:
            left = sorted((await db.execute(
                select(AuditLog.timestamp).where(AuditLog.action == "BE1OLD"))).scalars())
            purge = (await db.execute(select(AuditLog).where(AuditLog.action == "AUDIT_PURGE"))).scalars().all()
            stats = await get_settings(db, [audit_mod.LAST_PURGE_AT_KEY, audit_mod.LAST_PURGE_DELETED_KEY])
            await set_setting(db, audit_mod.RETENTION_KEY, "0")
            await db.commit()
        none_deleted = await audit_mod.purge_expired_audit_logs(now=now)
        return deleted, left, purge, stats, none_deleted

    deleted, left, purge, stats, none_deleted = asyncio.run(run())
    assert deleted == 2
    assert left == [now - timedelta(days=29), now - timedelta(days=1)]
    assert len(purge) == 1 and purge[0].details["deleted"] == 2 and purge[0].details["retention_days"] == 30
    assert purge[0].resource_type == "audit_log" and purge[0].zone_name is None
    assert stats[audit_mod.LAST_PURGE_DELETED_KEY] == "2" and stats[audit_mod.LAST_PURGE_AT_KEY]
    assert none_deleted == 0     # Aufbewahrung 0 = unbegrenzt
