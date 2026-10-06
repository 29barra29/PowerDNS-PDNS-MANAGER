"""Schema-Migration beim Start (Bauplan A.4/A.5, Tests laut W0-INT-BE1).

Teil 1 (ohne DB, laeuft immer): Kernliste vollstaendig und in der verbindlichen Reihenfolge, Helfer
(MODIFY nur bei Bedarf, Existenzpruefung fuer Spalten/Indizes, ignorierte "existiert schon"-Fehler),
``run_data_migration_once`` (SQLite), ``verify_mapped_columns`` (Fake-Verbindung), Slots, ``DbWrite``.

Teil 2 (``requires_db``, MariaDB): ``init_db`` zweimal gegen eine frische DB und gegen das 2.4.1-Schema
(Spalten, Indizes, Marker, F14-Datenmigration, Backfill ``zone_name``, Ergebnis identisch zur
Neuinstallation, Startmigration der Geheimnisse danach), Abbruch bei fehlender Spalte [D15],
Slot-Statements und -Datenmigrationen, Index-Fallback.

CI ruft diese Datei als eigenen pytest-Aufruf auf (sie loescht alle Tabellen der Testdatenbank).
"""
import asyncio
import contextlib
import logging
import re
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core import database
from app.core.database import (
    DATA_MIGRATIONS,
    MIGRATION_ERRORS,
    SCHEMA_STATEMENTS,
    Base,
    DbRead,
    DbWrite,
    ModifyIfNeeded,
    SchemaStartupError,
    get_db,
    run_data_migration_once,
    verify_mapped_columns,
)
from dbutil import load_schema_241, prepare_fresh, requires_db, reset_schema, schema_241_statements


@pytest.fixture(autouse=True)
def _clean_migration_errors():
    MIGRATION_ERRORS.clear()
    yield
    MIGRATION_ERRORS.clear()


def _stmt_text(entry) -> str:
    return entry.sql if isinstance(entry, ModifyIfNeeded) else entry


# =============================================================================================
# Teil 1: ohne Datenbank
# =============================================================================================

def _schema_241_columns() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for stmt in schema_241_statements():
        table = re.match(r"CREATE TABLE `(\w+)`", stmt).group(1)
        out[table] = set(re.findall(r"^\s+`(\w+)` ", stmt, re.MULTILINE))
    return out


def _schema_241_indexes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for stmt in schema_241_statements():
        table = re.match(r"CREATE TABLE `(\w+)`", stmt).group(1)
        out[table] = set(re.findall(r"KEY `(\w+)` \(", stmt))
    return out


def test_schema_241_fixture_is_complete():
    cols = _schema_241_columns()
    assert set(cols) == {"acme_tokens", "audit_logs", "panel_tokens", "server_configs", "system_settings",
                         "user_zone_access", "users", "webauthn_credentials", "webhooks", "zone_templates"}
    assert "totp_secret" in cols["users"] and "zone_name" not in cols["audit_logs"]


def test_every_new_column_and_index_of_existing_tables_has_a_statement():
    """Jede gemappte Spalte/jeder Index einer 2.4.1-Tabelle, der dort fehlt, braucht ein Statement –
    sonst bricht ein Upgrade an verify_mapped_columns ab."""
    import app.models  # noqa: F401

    stmts = [_stmt_text(e) for e in SCHEMA_STATEMENTS]
    cols_241, idx_241 = _schema_241_columns(), _schema_241_indexes()
    for table, have in cols_241.items():
        for col in Base.metadata.tables[table].columns:
            if col.name in have:
                continue
            pat = re.compile(rf"^ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col.name} ", re.IGNORECASE)
            assert any(pat.match(s) for s in stmts), f"kein ADD COLUMN fuer {table}.{col.name}"
        named = {i.name for i in Base.metadata.tables[table].indexes}
        named |= {c.name for c in Base.metadata.tables[table].constraints if c.name}   # unbenannte: 2.4.1-Bestand
        for name in sorted(n for n in named if n):
            if name in idx_241[table]:
                continue
            pat = re.compile(rf"^CREATE (UNIQUE )?INDEX IF NOT EXISTS {name} ON {table} ", re.IGNORECASE)
            assert any(pat.match(s) for s in stmts), f"kein CREATE INDEX fuer {table}.{name}"


def test_schema_statement_order_matches_plan():
    stmts = [_stmt_text(e) for e in SCHEMA_STATEMENTS]

    def pos(fragment: str) -> int:
        hits = [i for i, s in enumerate(stmts) if fragment in s]
        assert len(hits) == 1, f"{fragment}: {hits}"
        return hits[0]

    # (1) 14 Bestandseintraege, totp_* jetzt VARCHAR(512)
    assert all(s.startswith("ALTER TABLE") and "ADD COLUMN IF NOT EXISTS" in s for s in stmts[:14])
    assert "totp_secret VARCHAR(512)" in stmts[11] and "totp_pending_secret VARCHAR(512)" in stmts[12]
    order = [
        pos("ADD COLUMN IF NOT EXISTS webauthn_user_handle"),          # (1) Ende
        pos("ADD COLUMN IF NOT EXISTS must_change_password"), pos("ADD COLUMN IF NOT EXISTS auth_source"),
        pos("ADD COLUMN IF NOT EXISTS external_issuer"), pos("ADD COLUMN IF NOT EXISTS external_id"),  # (2)
        pos("users ADD COLUMN IF NOT EXISTS sessions_revoked_at"),                                      # (2b) L3
        pos("MODIFY COLUMN totp_secret"), pos("MODIFY COLUMN totp_pending_secret"),                       # (3)
        pos("MODIFY COLUMN api_key"),                                                                    # (4)
        pos("MODIFY COLUMN secret"), pos("MODIFY COLUMN url"),                                           # (5/5b)
        pos("webhooks ADD COLUMN IF NOT EXISTS scope"), pos("consecutive_failures"),                     # (6)
        pos("audit_logs ADD COLUMN IF NOT EXISTS zone_name"), pos("client_ip"),                          # (7)
        pos("scope_zones"), pos("panel_tokens ADD COLUMN IF NOT EXISTS revoked_at"),                     # (8)
        pos("ix_audit_logs_timestamp"), pos("ix_audit_logs_revert_of_id"),                               # (9)
        pos("uq_users_external"),                                                                        # (10)
    ]
    assert order == sorted(order)
    assert order[-1] == len(stmts) - 1
    assert "TEXT NOT NULL" in stmts[pos("MODIFY COLUMN secret")]   # TEXT, nicht VARCHAR(512) (A.3)
    assert stmts[pos("sessions_revoked_at")] == "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_revoked_at DATETIME NULL"
    assert [n for n, _ in DATA_MIGRATIONS] == ["f14_panel_token_scope_v1"]


def test_modify_predicates():
    nt, n512 = database._needs_text, database._needs_varchar512
    assert nt(("varchar", 500)) and nt(("varchar", 1024)) and not nt(("text", 65535)) and not nt(("mediumtext", None))
    assert n512(("varchar", 64)) and not n512(("varchar", 512)) and not n512(("varchar", 1024))
    assert not n512(("text", 65535)) and n512(("char", 600))


class _FakeDbError(Exception):
    def __init__(self, errno, msg):
        super().__init__(errno, msg)
        self.orig = SimpleNamespace(args=(errno, msg))


class _FakeConn:
    """AsyncConnection-Ersatz: liefert information_schema-Antworten, zeichnet DDL auf."""

    def __init__(self, columns=None, indexes=(), fail=None):
        self.dialect = SimpleNamespace(name="mariadb")
        self.columns = columns or {}          # (table, column) -> (data_type, length)
        self.indexes = set(indexes)           # (table, name)
        self.fail = fail or {}                # Statement-Teilstring -> Exception
        self.executed: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "information_schema.COLUMNS" in sql and params:
            info = self.columns.get((params["t"], params["c"]))
            return SimpleNamespace(first=lambda: info)
        if "information_schema.STATISTICS" in sql:
            hit = (params["t"], params["n"]) in self.indexes
            return SimpleNamespace(first=lambda: (1,) if hit else None)
        self.executed.append(sql)
        for frag, exc in self.fail.items():
            if frag in sql:
                raise exc
        return SimpleNamespace(first=lambda: None)


def test_run_schema_entry_add_column_checks_existence():
    conn = _FakeConn(columns={("users", "phone"): ("varchar", 50)})
    asyncio.run(database._run_schema_entry(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)"))
    asyncio.run(database._run_schema_entry(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(100)"))
    # vorhandene Spalte: nichts; fehlende: ohne IF NOT EXISTS (laeuft auch auf MySQL)
    assert conn.executed == ["ALTER TABLE users ADD COLUMN city VARCHAR(100)"]


def test_run_schema_entry_index_fallback():
    conn = _FakeConn(indexes={("audit_logs", "ix_audit_logs_user_id")})
    asyncio.run(database._run_schema_entry(conn, "CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs (user_id)"))
    asyncio.run(database._run_schema_entry(
        conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_external ON users (auth_source, external_issuer, external_id)"))
    assert conn.executed == ["CREATE UNIQUE INDEX uq_users_external ON users (auth_source, external_issuer, external_id)"]


def test_modify_only_when_needed():
    conn = _FakeConn(columns={("server_configs", "api_key"): ("text", 65535), ("webhooks", "url"): ("varchar", 1024)})
    for entry in SCHEMA_STATEMENTS:
        if isinstance(entry, ModifyIfNeeded) and entry.table in ("server_configs", "webhooks"):
            asyncio.run(database._run_schema_entry(conn, entry))
    # api_key schon TEXT -> kein MODIFY; webhooks.secret fehlt -> nichts; url VARCHAR -> MODIFY
    assert conn.executed == ["ALTER TABLE webhooks MODIFY COLUMN url TEXT NOT NULL"]


def test_exec_idempotent_ignores_duplicates_and_collects_errors(caplog):
    conn = _FakeConn(fail={
        "dup_col": _FakeDbError(1060, "Duplicate column name"),
        "dup_key": _FakeDbError(1061, "Duplicate key name"),
        "no_drop": _FakeDbError(1091, "Can't DROP"),
        "broken": _FakeDbError(1142, "ALTER command denied"),
    })
    with caplog.at_level(logging.ERROR, logger="app.core.database"):
        for s in ("ALTER dup_col", "ALTER dup_key", "ALTER no_drop", "ALTER broken"):
            asyncio.run(database._exec_idempotent(conn, s))
    assert [stmt for stmt, _ in MIGRATION_ERRORS] == ["ALTER broken"]
    assert "ALTER command denied" in MIGRATION_ERRORS[0][1]
    assert "Migration fehlgeschlagen: ALTER broken" in caplog.text


class _AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.s = session

    async def execute(self, *args, **kwargs):
        return self.s.execute(*args, **kwargs)

    async def commit(self):
        self.s.commit()

    async def rollback(self):
        self.s.rollback()


@pytest.fixture
def sqlite_session():
    from app.models.models import SystemSetting

    eng = create_engine("sqlite://")
    SystemSetting.__table__.create(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))
        conn.execute(text("INSERT INTO t (id, v) VALUES (1, 0)"))
    with Session(eng) as s:
        yield eng, _AsyncSessionAdapter(s)
    eng.dispose()


def test_run_data_migration_once_sets_marker_and_runs_once(sqlite_session):
    eng, db = sqlite_session
    stmts = ["UPDATE t SET v = v + 1"]
    assert asyncio.run(run_data_migration_once(db, "be1_demo_v1", stmts)) is True
    assert asyncio.run(run_data_migration_once(db, "be1_demo_v1", stmts)) is False
    with eng.connect() as conn:
        assert conn.execute(text("SELECT v FROM t")).scalar_one() == 1
        marker = conn.execute(text("SELECT `value` FROM system_settings WHERE `key` = 'migration_be1_demo_v1'")).scalar_one()
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", marker)
    assert MIGRATION_ERRORS == []


def test_run_data_migration_failure_rolls_back_without_marker(sqlite_session, caplog):
    eng, db = sqlite_session
    with caplog.at_level(logging.ERROR, logger="app.core.database"):
        ok = asyncio.run(run_data_migration_once(db, "be1_bad_v1", ["UPDATE t SET v = 5", "UPDATE gibt_es_nicht SET x = 1"]))
    assert ok is False
    assert [s for s, _ in MIGRATION_ERRORS] == ["data:be1_bad_v1"]
    assert "Datenmigration be1_bad_v1 fehlgeschlagen" in caplog.text
    with eng.connect() as conn:
        assert conn.execute(text("SELECT v FROM t")).scalar_one() == 0           # zurueckgerollt
        assert conn.execute(text("SELECT COUNT(*) FROM system_settings")).scalar_one() == 0   # kein Marker
    # naechster Start mit korrigierter Liste laeuft
    assert asyncio.run(run_data_migration_once(db, "be1_bad_v1", ["UPDATE t SET v = 5"])) is True


class _VerifyConn:
    def __init__(self, rows, dialect="mariadb"):
        self.dialect = SimpleNamespace(name=dialect)
        self.rows = rows

    async def execute(self, _stmt, _params=None):
        return SimpleNamespace(all=lambda: self.rows)


def _all_mapped_rows(skip=()):
    import app.models  # noqa: F401

    return [(t.name, c.name) for t in Base.metadata.sorted_tables for c in t.columns if (t.name, c.name) not in skip]


def test_verify_mapped_columns_ok_and_missing():
    asyncio.run(verify_mapped_columns(_VerifyConn(_all_mapped_rows())))
    # Tabelle fehlt ganz -> keine Pruefung (create_all ist dafuer zustaendig)
    rows = [r for r in _all_mapped_rows() if r[0] != "dyndns_tokens"]
    asyncio.run(verify_mapped_columns(_VerifyConn(rows)))
    MIGRATION_ERRORS.append(("ALTER TABLE audit_logs ADD COLUMN actor_username VARCHAR(100) NULL", "denied"))
    with pytest.raises(SchemaStartupError) as ei:
        asyncio.run(verify_mapped_columns(_VerifyConn(_all_mapped_rows(skip={("audit_logs", "actor_username"),
                                                                             ("users", "auth_source")}))))
    exc = ei.value
    assert exc.code == "SCHEMA_INCOMPLETE"
    joined = "\n".join(exc.log_lines())
    assert "Start abgebrochen" in exc.log_lines()[0]
    assert "audit_logs.actor_username" in joined and "users.auth_source" in joined and "denied" in joined
    # andere Dialekte: keine Pruefung
    asyncio.run(verify_mapped_columns(_VerifyConn([], dialect="sqlite")))


def test_iter_migration_slots_sorted_and_validated(tmp_path, monkeypatch):
    pkg = tmp_path / "be1_slots_demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "b_second.py").write_text("SCHEMA_STATEMENTS = ['B']\n")
    (pkg / "a_first.py").write_text(textwrap.dedent("""
        SCHEMA_STATEMENTS = ['A1', 'A2']
        DATA_MIGRATIONS = [('a_first_v1', ['UPDATE x SET y = 1'])]
    """))
    (pkg / "_private.py").write_text("raise RuntimeError('darf nicht geladen werden')\n")
    bad = tmp_path / "be1_slots_bad"
    bad.mkdir()
    (bad / "__init__.py").write_text("")
    (bad / "x.py").write_text("SCHEMA_STATEMENTS = 'kein list'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        mods = database._iter_migration_slots("be1_slots_demo")
        assert [m.__name__ for m in mods] == ["be1_slots_demo.a_first", "be1_slots_demo.b_second"]
        assert mods[0].DATA_MIGRATIONS == [("a_first_v1", ["UPDATE x SET y = 1"])]
        with pytest.raises(TypeError):
            database._iter_migration_slots("be1_slots_bad")
    finally:
        for name in [n for n in sys.modules if n.startswith(("be1_slots_demo", "be1_slots_bad"))]:
            sys.modules.pop(name, None)


def test_repo_slots_and_ext_models_are_valid():
    """Alle eingecheckten Slots (core/migrations, models/ext) sind ladbar und gueltig."""
    import app.models

    assert isinstance(database._iter_migration_slots(), list)
    assert isinstance(app.models.EXT_MODEL_MODULES, list)
    names = set(Base.metadata.tables)
    assert {"webhook_deliveries", "dyndns_tokens"} <= names


def test_init_db_on_sqlite_only_creates_tables(monkeypatch):
    """Nicht-MySQL-Dialekt: nur create_all, keine ALTER-Statements (keine MIGRATION_ERRORS)."""

    class _Eng:
        dialect = SimpleNamespace(name="sqlite")

        def __init__(self):
            self.sync = create_engine("sqlite://")

        @contextlib.asynccontextmanager
        async def begin(self):
            with self.sync.begin() as conn:
                yield SimpleNamespace(dialect=conn.dialect, run_sync=lambda fn: _call(fn, conn))

        def connect(self):  # darf nicht benutzt werden
            raise AssertionError("keine Migrationen auf SQLite")

    async def _call(fn, conn):
        return fn(conn)

    eng = _Eng()
    monkeypatch.setattr(database, "engine", eng)
    asyncio.run(database.init_db())
    from sqlalchemy import inspect as sa_inspect

    assert {"audit_logs", "webhook_deliveries", "dyndns_tokens"} <= set(sa_inspect(eng.sync).get_table_names())
    assert MIGRATION_ERRORS == []


def test_db_write_commits_before_response_db_read_after():
    """DbWrite (scope="function") committet vor dem Senden der Antwort, DbRead danach [D2]."""
    events: list[str] = []

    async def fake_db():
        events.append("enter")
        yield SimpleNamespace()
        events.append("commit")

    class _MarkResponseStart:
        """Reine ASGI-Middleware: markiert den Beginn der Antwort (http.response.start)."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            async def _send(message):
                if message["type"] == "http.response.start":
                    events.append("response")
                await send(message)

            await self.app(scope, receive, _send)

    app = FastAPI()
    app.add_middleware(_MarkResponseStart)

    @app.post("/w")
    async def write(db: DbWrite):
        return {"ok": True}

    @app.get("/r")
    async def read(db: DbRead):
        return {"ok": True}

    app.dependency_overrides[get_db] = fake_db
    with TestClient(app) as client:
        assert client.post("/w").status_code == 200
        write_events = list(events)
        events.clear()
        assert client.get("/r").status_code == 200
        read_events = list(events)
    assert write_events == ["enter", "commit", "response"]
    assert read_events == ["enter", "response", "commit"]


def test_db_aliases_scope():
    from typing import get_args

    write_dep = get_args(DbWrite)[1]
    read_dep = get_args(DbRead)[1]
    assert write_dep.dependency is get_db and write_dep.scope == "function"
    assert read_dep.dependency is get_db and read_dep.scope in (None, "request")


# =============================================================================================
# Teil 2: MariaDB
# =============================================================================================

TOTP = "JBSWY3DPEHPK3PXP"

SECRET_COLUMN_TYPES = [
    ("server_configs", "api_key", "text"), ("webhooks", "secret", "text"), ("webhooks", "url", "text"),
    ("users", "totp_secret", "varchar(512)"), ("users", "totp_pending_secret", "varchar(512)"),
]
NEW_INDEXES = [
    ("audit_logs", "ix_audit_logs_timestamp"), ("audit_logs", "ix_audit_logs_zone_ts"),
    ("audit_logs", "ix_audit_logs_user_id"), ("audit_logs", "ix_audit_logs_revert_of_id"),
    ("users", "uq_users_external"), ("webhook_deliveries", "ix_wd_due"), ("webhook_deliveries", "ix_wd_created"),
    ("dyndns_tokens", "ix_dyndns_tokens_token_hash"),
]


def _engine():
    return database.engine


async def _q(sql, params=None):
    async with _engine().connect() as conn:
        return (await conn.execute(text(sql), params or {})).all()


async def _exec(*stmts):
    async with _engine().connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        for s in stmts:
            await conn.execute(text(s))


async def _column_types():
    rows = await _q("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE()")
    return {(t, c): str(ct).lower() for t, c, ct in rows}


async def _indexes():
    rows = await _q("SELECT TABLE_NAME, INDEX_NAME, MIN(NON_UNIQUE) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() GROUP BY TABLE_NAME, INDEX_NAME")
    return {(t, n): int(nu) == 0 for t, n, nu in rows}


async def _markers():
    rows = await _q("SELECT `key`, `value` FROM system_settings WHERE `key` IN "
                    "('migration_f14_panel_token_scope_v1', 'audit_zone_backfill_v1')")
    return dict(rows)


async def _schema_snapshot():
    cols = await _q("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLLATION_NAME "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")
    idx = await _q("SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
                   "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                   "GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE")
    return {tuple(r) for r in cols}, {tuple(r) for r in idx}


def _assert_schema_30(types, indexes):
    for table, col, expected in SECRET_COLUMN_TYPES:
        assert types[(table, col)] == expected, (table, col, types[(table, col)])
    for col in ("zone_name", "revert_of_id", "actor_username", "client_ip"):
        assert ("audit_logs", col) in types
    for col in ("scope_zones", "permission", "expires_at", "allow_admin", "revoked_at"):
        assert ("panel_tokens", col) in types
    for col in ("must_change_password", "auth_source", "external_issuer", "external_id"):
        assert ("users", col) in types
    assert types[("users", "sessions_revoked_at")] == "datetime"   # L3 (WS-W2-NACHARBEIT)
    for col in ("scope", "updated_at", "last_success_at", "last_failure_at", "consecutive_failures"):
        assert ("webhooks", col) in types
    assert types[("webhook_deliveries", "body")] == "mediumtext"
    assert ("dyndns_tokens", "stale_servers") in types
    for key in NEW_INDEXES:
        assert key in indexes, key
    assert indexes[("users", "uq_users_external")] is True


async def _reset_and_init():
    await prepare_fresh(_engine())


@requires_db
def test_db_241_fixture_provides_241_schema(db_241):
    """Erster DB-Test des Moduls (einziger Nutzer der Modul-Fixture db_241)."""
    types = asyncio.run(_column_types())
    assert types[("server_configs", "api_key")] == "varchar(500)"
    assert types[("users", "totp_secret")] == "varchar(64)"
    assert ("audit_logs", "zone_name") not in types and ("panel_tokens", "revoked_at") not in types
    assert not any(t == "webhook_deliveries" for t, _ in types)
    assert asyncio.run(_q("SELECT COUNT(*) FROM users"))[0][0] == 0


@requires_db
def test_fresh_install_twice():
    asyncio.run(_reset_and_init())
    assert MIGRATION_ERRORS == []
    types, idx = asyncio.run(_column_types()), asyncio.run(_indexes())
    _assert_schema_30(types, idx)
    markers = asyncio.run(_markers())
    assert set(markers) == {"migration_f14_panel_token_scope_v1", "audit_zone_backfill_v1"}
    snap = asyncio.run(_schema_snapshot())
    asyncio.run(database.init_db())
    assert MIGRATION_ERRORS == []
    assert asyncio.run(_markers()) == markers
    assert asyncio.run(_schema_snapshot()) == snap


async def _seed_241():
    """Bestand wie unter 2.4.1 (Raw-SQL gegen das 2.4.1-Schema)."""
    await _exec(
        "INSERT INTO users (id, username, email, hashed_password, role, is_active, created_at, totp_enabled, "
        f"totp_secret) VALUES (1, 'admin', 'a@example.test', 'h', 'admin', 1, '2025-01-01 00:00:00', 1, '{TOTP}'), "
        "(2, 'alice', 'b@example.test', 'h', 'user', 1, '2025-01-01 00:00:00', 0, NULL)",
        "INSERT INTO server_configs (id, name, url, api_key, is_active, allow_writes, sort_order) "
        "VALUES (1, 'ns1', 'http://ns1:8081', 'pdns-api-key-1', 1, 1, 0)",
        "INSERT INTO webhooks (id, user_id, name, url, secret, events, is_active, created_at) VALUES "
        "(1, 2, 'slack', 'https://hooks.slack.example/services/T0/B0/XYZ', 'whsec-1', '[\"*\"]', 1, '2025-01-01 00:00:00')",
        "INSERT INTO panel_tokens (id, user_id, name, token_prefix, token_hash, created_at, last_used_at, is_active) VALUES "
        "(1, 1, 'admin-aktiv', 'p1', 'h1', '2025-01-01 00:00:00', NULL, 1), "
        "(2, 2, 'alice-aktiv', 'p2', 'h2', '2025-01-02 00:00:00', NULL, 1), "
        "(3, 1, 'admin-geloescht', 'p3', 'h3', '2025-01-03 00:00:00', '2025-02-03 10:00:00', 0), "
        "(4, 2, 'alice-geloescht', 'p4', 'h4', '2025-01-04 00:00:00', NULL, 0)",
        "INSERT INTO system_settings (id, `key`, `value`) VALUES (1, 'smtp_password', 'smtp-pw'), "
        "(2, 'smtp_host', 'mail.example.test')",
        # Audit v1 (2.4.1-Formate) -> erwartete zone_name siehe EXPECTED_ZONES
        "INSERT INTO audit_logs (id, `timestamp`, action, resource_type, resource_name, server_name, details, status, user_id) VALUES "
        "(1, '2025-03-01 00:00:00', 'CREATE', 'zone', 'Alpha.Example.', 'ns1', '{\"kind\": \"Native\"}', 'success', 1), "
        "(2, '2025-03-01 00:00:01', 'CREATE', 'record', 'www.beta.example.', 'ns1', '{\"zone\": \"Beta.Example\", \"type\": \"A\"}', 'success', 2), "
        "(3, '2025-03-01 00:00:02', 'BULK_UPDATE', 'record', 'gamma.example', 'ns1', '{\"created\": 2, \"zone\": \"falsch.example.\"}', 'success', 1), "
        "(4, '2025-03-01 00:00:03', 'KEY_ACTIVATE', 'dnssec_key', 'delta.example.', 'ns1', '{\"key_id\": 3}', 'success', 1), "
        "(5, '2025-03-01 00:00:04', 'ACME_PRESENT', 'acme', '_acme-challenge.www.eps.example.', NULL, '{\"zone\": \"eps.example.\", \"token_id\": 1}', 'success', 1), "
        "(6, '2025-03-01 00:00:05', 'LOGIN', 'user', 'alice', NULL, '{\"ip\": \"192.0.2.1\", \"zone\": \"nicht.example.\"}', 'success', 2), "
        "(7, '2025-03-01 00:00:06', 'DELETE', 'record', 'x.zeta.example.', 'ns1', '{\"zone\": 5}', 'success', 1), "
        "(8, '2025-03-01 00:00:07', 'UPDATE', 'record', 'x.eta.example.', 'ns1', '{\"zone\": \"   \"}', 'success', 1), "
        "(9, '2025-03-01 00:00:08', 'CREATE', 'record', 'bad.theta.example.', 'ns1', '{\"zone\": \"theta.example.\"}', 'error', 1), "
        "(10, '2025-03-01 00:00:09', 'SMTP_UPDATE', 'settings', 'smtp', NULL, NULL, 'success', 1), "
        "(11, '2025-03-01 00:00:10', 'DELETE', 'zone', '  ', 'ns1', NULL, 'success', 1)",
    )


EXPECTED_ZONES = {1: "alpha.example.", 2: "beta.example.", 3: "gamma.example.", 4: "delta.example.",
                  5: "eps.example.", 6: None, 7: None, 8: None, 9: "theta.example.", 10: None, 11: None}


@requires_db
def test_upgrade_from_241_twice_then_secrets(tmp_path, monkeypatch):
    from app.core import secrets as secret_store

    async def prepare():
        await reset_schema(_engine())
        await load_schema_241(_engine())
        await _seed_241()

    asyncio.run(prepare())
    assert asyncio.run(_column_types())[("server_configs", "api_key")] == "varchar(500)"

    asyncio.run(database.init_db())
    assert MIGRATION_ERRORS == []
    _assert_schema_30(asyncio.run(_column_types()), asyncio.run(_indexes()))

    # Werte nach MODIFY unveraendert
    assert asyncio.run(_q("SELECT api_key FROM server_configs"))[0][0] == "pdns-api-key-1"
    assert asyncio.run(_q("SELECT totp_secret FROM users WHERE id = 1"))[0][0] == TOTP
    # Defaults neuer Spalten fuer Bestandszeilen
    assert [tuple(r) for r in asyncio.run(_q("SELECT must_change_password, auth_source, external_id FROM users ORDER BY id"))] == [
        (0, "local", None), (0, "local", None)]
    assert [r[0] for r in asyncio.run(_q("SELECT sessions_revoked_at FROM users ORDER BY id"))] == [None, None]
    assert tuple(asyncio.run(_q("SELECT scope, consecutive_failures FROM webhooks"))[0]) == ("own", 0)
    # F14-Datenmigration
    tokens = {r[0]: tuple(r[1:]) for r in asyncio.run(_q(
        "SELECT id, allow_admin, revoked_at, permission, scope_zones, expires_at FROM panel_tokens"))}
    from datetime import datetime

    assert tokens[1] == (1, None, "manage", None, None)                                   # Admin behaelt Admin-Rechte
    assert tokens[2] == (0, None, "manage", None, None)
    assert tokens[3] == (0, datetime(2025, 2, 3, 10, 0, 0), "manage", None, None)          # = last_used_at
    assert tokens[4] == (0, datetime(2025, 1, 4, 0, 0, 0), "manage", None, None)           # = created_at
    # Backfill zone_name
    zones = {r[0]: r[1] for r in asyncio.run(_q("SELECT id, zone_name FROM audit_logs"))}
    assert zones == EXPECTED_ZONES
    markers = asyncio.run(_markers())
    assert set(markers) == {"migration_f14_panel_token_scope_v1", "audit_zone_backfill_v1"}

    # zweiter Start: kein MODIFY, keine Fehler, Marker schuetzen manuelle Aenderungen
    asyncio.run(_exec("UPDATE panel_tokens SET allow_admin = 0 WHERE id = 1",
                      "UPDATE audit_logs SET zone_name = NULL WHERE id = 2"))
    executed: list[str] = []
    orig = database._exec_idempotent

    async def spy(conn, stmt):
        executed.append(stmt)
        await orig(conn, stmt)

    monkeypatch.setattr(database, "_exec_idempotent", spy)
    snap = asyncio.run(_schema_snapshot())
    asyncio.run(database.init_db())
    assert MIGRATION_ERRORS == []
    assert [s for s in executed if "MODIFY" in s] == []
    assert executed == []          # alle Spalten/Indizes vorhanden -> kein DDL
    assert asyncio.run(_schema_snapshot()) == snap
    assert asyncio.run(_markers()) == markers
    assert asyncio.run(_q("SELECT allow_admin FROM panel_tokens WHERE id = 1"))[0][0] == 0
    assert asyncio.run(_q("SELECT zone_name FROM audit_logs WHERE id = 2"))[0][0] is None

    # Startmigration der Geheimnisse (A.5 Nr. 4) laeuft auf dem migrierten Schema
    async def secrets_and_orm():
        from app.models.models import ServerConfig, Webhook

        cfg = secret_store.KeyConfig(secret_store.generate_key(), (), tmp_path / ".secret_key", False)
        report = await secret_store.init_secrets(_engine(), cfg)
        raw = await _q("SELECT (SELECT api_key FROM server_configs), (SELECT url FROM webhooks), "
                       "(SELECT secret FROM webhooks), (SELECT totp_secret FROM users WHERE id = 1), "
                       "(SELECT `value` FROM system_settings WHERE `key` = 'smtp_password')")
        async with database.async_session() as s:
            cfg_row = await s.get(ServerConfig, 1)
            hook = await s.get(Webhook, 1)
            plain = (cfg_row.api_key, hook.url, hook.secret)
        return report, raw[0], plain

    report, raw, plain = asyncio.run(secrets_and_orm())
    assert report.mode == "encrypted" and report.unreadable == []
    assert report.migrated == {"server_configs.api_key": 1, "webhooks.secret": 1, "webhooks.url": 1,
                               "users.totp_secret": 1, "system_settings.smtp_password": 1}
    assert all(str(v).startswith("enc:v1:") for v in raw)
    assert plain == ("pdns-api-key-1", "https://hooks.slack.example/services/T0/B0/XYZ", "whsec-1")
    assert asyncio.run(_q("SELECT COUNT(*) FROM audit_logs WHERE action = 'SECRETS_MIGRATE'"))[0][0] == 1


@requires_db
def test_upgraded_schema_equals_fresh_install():
    asyncio.run(_reset_and_init())
    fresh = asyncio.run(_schema_snapshot())

    async def upgrade():
        await reset_schema(_engine())
        await load_schema_241(_engine())
        await database.init_db()

    asyncio.run(upgrade())
    assert MIGRATION_ERRORS == []
    upgraded = asyncio.run(_schema_snapshot())
    fresh_cols, fresh_idx = fresh
    up_cols, up_idx = upgraded
    assert upgraded == fresh, {
        "spalten_nur_neu": sorted(fresh_cols - up_cols), "spalten_nur_upgrade": sorted(up_cols - fresh_cols),
        "indizes_nur_neu": sorted(fresh_idx - up_idx), "indizes_nur_upgrade": sorted(up_idx - fresh_idx),
    }


@requires_db
def test_missing_column_aborts_start(monkeypatch):
    asyncio.run(_reset_and_init())
    asyncio.run(_exec("ALTER TABLE audit_logs DROP COLUMN actor_username"))

    async def verify():
        async with _engine().connect() as conn:
            await verify_mapped_columns(conn)

    with pytest.raises(SchemaStartupError) as ei:
        asyncio.run(verify())
    assert ei.value.code == "SCHEMA_INCOMPLETE"
    assert "audit_logs.actor_username" in "\n".join(ei.value.lines)

    # init_db ohne das reparierende Statement (simuliert ein fehlgeschlagenes ALTER) -> Startabbruch
    reduced = [e for e in SCHEMA_STATEMENTS if "actor_username" not in _stmt_text(e)]
    reduced.append("ALTER TABLE gibt_es_nicht ADD COLUMN IF NOT EXISTS x INT")
    monkeypatch.setattr(database, "SCHEMA_STATEMENTS", reduced)
    with pytest.raises(SchemaStartupError) as ei:
        asyncio.run(database.init_db())
    joined = "\n".join(ei.value.lines)
    assert "audit_logs.actor_username" in joined and "gibt_es_nicht" in joined

    # mit vollstaendiger Liste repariert der naechste Start das Schema
    monkeypatch.setattr(database, "SCHEMA_STATEMENTS", SCHEMA_STATEMENTS)
    asyncio.run(database.init_db())
    assert MIGRATION_ERRORS == []
    assert ("audit_logs", "actor_username") in asyncio.run(_column_types())


@requires_db
def test_failed_statement_is_collected_without_abort(monkeypatch, caplog):
    asyncio.run(_reset_and_init())
    monkeypatch.setattr(database, "SCHEMA_STATEMENTS",
                        [*SCHEMA_STATEMENTS, "CREATE INDEX IF NOT EXISTS ix_bad ON audit_logs (gibt_es_nicht)"])
    with caplog.at_level(logging.ERROR, logger="app.core.database"):
        asyncio.run(database.init_db())
    assert [s for s, _ in MIGRATION_ERRORS] == ["CREATE INDEX ix_bad ON audit_logs (gibt_es_nicht)"]
    assert "Migration fehlgeschlagen" in caplog.text and "1 Fehler" in caplog.text


@requires_db
def test_index_fallback_recreates_missing_index():
    asyncio.run(_reset_and_init())
    asyncio.run(_exec("DROP INDEX ix_audit_logs_zone_ts ON audit_logs"))

    async def exists():
        async with _engine().connect() as conn:
            return await database._index_exists(conn, "audit_logs", "ix_audit_logs_zone_ts")

    assert asyncio.run(exists()) is False
    asyncio.run(database.init_db())
    assert asyncio.run(exists()) is True and MIGRATION_ERRORS == []


@requires_db
def test_slot_statements_and_data_migrations(monkeypatch):
    asyncio.run(_reset_and_init())
    slot = SimpleNamespace(
        __name__="app.core.migrations.be1_test",
        SCHEMA_STATEMENTS=[
            "CREATE TABLE IF NOT EXISTS be1_slot_test (id INT PRIMARY KEY, v INT NOT NULL DEFAULT 0)",
            "ALTER TABLE be1_slot_test ADD COLUMN IF NOT EXISTS note VARCHAR(20) NULL",
            "CREATE INDEX IF NOT EXISTS ix_be1_slot_v ON be1_slot_test (v)",
        ],
        DATA_MIGRATIONS=[("be1_slot_test_v1", ["INSERT INTO be1_slot_test (id, v) VALUES (1, 1)"])],
    )
    monkeypatch.setattr(database, "_iter_migration_slots", lambda: [slot])
    try:
        asyncio.run(database.init_db())
        asyncio.run(database.init_db())     # idempotent: Statements und Datenmigration nur einmal wirksam
        assert MIGRATION_ERRORS == []
        assert [tuple(r) for r in asyncio.run(_q("SELECT id, v, note FROM be1_slot_test"))] == [(1, 1, None)]
        assert ("be1_slot_test", "ix_be1_slot_v") in asyncio.run(_indexes())
        assert asyncio.run(_q("SELECT COUNT(*) FROM system_settings WHERE `key` = 'migration_be1_slot_test_v1'"))[0][0] == 1
    finally:
        asyncio.run(_exec("DROP TABLE IF EXISTS be1_slot_test"))


@requires_db
def test_backfill_failure_keeps_marker_unset_and_retries(monkeypatch):
    from app.services import audit as audit_mod

    async def prepare():
        await reset_schema(_engine())
        await load_schema_241(_engine())
        await _seed_241()

    asyncio.run(prepare())
    monkeypatch.setattr(audit_mod, "_BACKFILL_FROM_RESOURCE", "UPDATE gibt_es_nicht SET x = 1 WHERE :lo < :hi")
    asyncio.run(database.init_db())
    assert [s for s, _ in MIGRATION_ERRORS] == ["backfill:audit_zone_backfill_v1"]
    assert "audit_zone_backfill_v1" not in asyncio.run(_markers())
    monkeypatch.undo()
    asyncio.run(database.init_db())
    assert MIGRATION_ERRORS == []
    zones = {r[0]: r[1] for r in asyncio.run(_q("SELECT id, zone_name FROM audit_logs"))}
    assert zones == EXPECTED_ZONES
    assert "audit_zone_backfill_v1" in asyncio.run(_markers())
