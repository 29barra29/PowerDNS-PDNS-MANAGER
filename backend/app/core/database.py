"""Datenbank-Engine, Sessions, DB-Dependencies und Schema-Migration beim Start (Bauplan A.4).

Ablauf von ``init_db`` (idempotent, laeuft bei jedem Start):

1. ``create_all`` (neue Tabellen inkl. Modell-Slots ``app/models/ext``),
2. ``SCHEMA_STATEMENTS`` (Kernliste, Reihenfolge verbindlich) und danach die Statements der
   Migrations-Slots ``app/core/migrations/*.py``,
3. ``verify_mapped_columns``: fehlt eine gemappte Spalte, bricht der Start mit
   ``SchemaStartupError`` ab (sonst wuerde z. B. jeder Audit-Eintrag scheitern) [D15],
4. ``DATA_MIGRATIONS`` (einmalig, Marker in ``system_settings``) plus Slot-Datenmigrationen,
5. ``backfill_audit_zone_names`` (F7 4.4).

Fehlgeschlagene Statements werden geloggt UND in ``MIGRATION_ERRORS`` gesammelt (fuer ``/health``
nur ueber Loopback und den Startlog). Schritt 2-5 laufen nur auf MySQL/MariaDB; auf anderen
Dialekten (SQLite in Unit-Tests) legt ``init_db`` nur die Tabellen an.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass
from types import ModuleType
from typing import Annotated, Callable, Iterable, Optional, Union

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)


if settings.DB_POOL_SIZE <= 0:
    # Kein Pool: Verbindungen leben nur innerhalb einer Session (Tests mit wechselnden Event-Loops).
    engine = create_async_engine(settings.DATABASE_URL, echo=False, poolclass=NullPool)
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=20,
    )

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency to get async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            # Rollback bei jedem Fehler (auch HTTPException): Handler, die erst mutieren und
            # dann validieren, duerfen keine Teilzustaende hinterlassen. Fehler-Audit-Eintraege
            # werden deshalb in einer EIGENEN Session geschrieben (write_audit_detached).
            await session.rollback()
            raise
        finally:
            await session.close()


# DB-Dependencies (verbindliche Aliase, Bauplan A.4 [D2]):
# - DbRead: Default-Scope "request" – der Commit laeuft erst nach dem Senden der Antwort (fuer Lesepfade egal).
# - DbWrite: scope="function" – Commit (Exit-Code von get_db) laeuft VOR dem Senden der Antwort; ein
#   Commit-Fehler fuehrt zu 500 statt zu 200 ohne Audit/Outbox. Pflicht fuer alle schreibenden Handler.
DbRead = Annotated[AsyncSession, Depends(get_db)]
DbWrite = Annotated[AsyncSession, Depends(get_db, scope="function")]


# ---------------------------------------------------------------------------
# Migrations-Fehler und Startabbruch
# ---------------------------------------------------------------------------

# (statement bzw. "data:<name>"/"backfill:<marker>", fehlertext) – gefuellt von init_db, geleert bei jedem Lauf.
MIGRATION_ERRORS: list[tuple[str, str]] = []

# MariaDB/MySQL: 1060 Duplicate column, 1061 Duplicate key name, 1091 Can't DROP (existiert nicht).
_IGNORED_ERRNOS = frozenset({1060, 1061, 1091})
_TEXT_TYPES = ("text", "mediumtext", "longtext")
_MYSQL_DIALECTS = ("mysql", "mariadb")


class SchemaStartupError(RuntimeError):
    """Startabbruch wegen unvollstaendigem Schema (analog ``SecretsStartupError``) [D15].

    Codes: SCHEMA_INCOMPLETE
    """

    def __init__(self, code: str, title: str, lines: list[str]):
        self.code = code
        self.title = title
        self.lines = list(lines)
        super().__init__(f"{title} ({code})")

    def log_lines(self) -> list[str]:
        return [
            f"==== PDNS Manager: Start abgebrochen – {self.title} ({self.code}) ====",
            *self.lines,
        ]


def _mysql_errno(exc: BaseException) -> Optional[int]:
    """Fehlernummer eines (pymysql/aiomysql-)Datenbankfehlers, sonst None."""
    orig = getattr(exc, "orig", None) or exc
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _error_text(exc: BaseException) -> str:
    """Kurzer Fehlertext ohne SQL-Wiederholung (das Statement steht separat in MIGRATION_ERRORS)."""
    orig = getattr(exc, "orig", None) or exc
    return f"{type(orig).__name__}: {orig}"[:500]


def _record_error(stmt: str, exc: BaseException) -> None:
    msg = _error_text(exc)
    logger.error("Migration fehlgeschlagen: %s -> %s", stmt, msg)
    MIGRATION_ERRORS.append((stmt, msg))


def _is_mysql(conn) -> bool:
    return conn.dialect.name in _MYSQL_DIALECTS


# ---------------------------------------------------------------------------
# Schema-Helfer (nur MySQL/MariaDB; conn = AsyncConnection im AUTOCOMMIT-Modus)
# ---------------------------------------------------------------------------


async def _exec_idempotent(conn, stmt: str) -> None:
    """Fuehrt ``stmt`` aus. "Existiert schon"-Fehler (1060/1061/1091) werden ignoriert, alle
    anderen geloggt und in ``MIGRATION_ERRORS`` gesammelt (kein Abbruch)."""
    try:
        await conn.execute(text(stmt))
    except Exception as exc:  # noqa: BLE001 - jeder Fehler wird gesammelt, der Start entscheidet spaeter
        if _mysql_errno(exc) in _IGNORED_ERRNOS:
            logger.debug("Migration uebersprungen (existiert bereits): %s", stmt)
            return
        _record_error(stmt, exc)


async def _column_type(conn, table: str, column: str) -> Optional[tuple[str, Optional[int]]]:
    """``(DATA_TYPE klein, CHARACTER_MAXIMUM_LENGTH)`` aus information_schema oder None (Spalte fehlt)."""
    row = (await conn.execute(
        text(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    )).first()
    if row is None:
        return None
    return (str(row[0] or "").lower(), int(row[1]) if row[1] is not None else None)


async def _modify_if_needed(
    conn, table: str, column: str, target_sql: str, *, needs: Callable[[tuple], bool]
) -> None:
    """``target_sql`` (MODIFY COLUMN) nur ausfuehren, wenn ``needs((data_type, max_len))`` wahr ist.

    Vermeidet einen Tabellen-Rebuild bei jedem Start. Fehlt die Spalte, passiert nichts (die
    Spaltenpruefung ``verify_mapped_columns`` meldet das).
    """
    try:
        info = await _column_type(conn, table, column)
    except Exception as exc:  # noqa: BLE001
        _record_error(target_sql, exc)
        return
    if info is None or not needs(info):
        return
    logger.info("Migration: %s.%s wird angepasst (%s)", table, column, target_sql)
    await _exec_idempotent(conn, target_sql)


async def _index_exists(conn, table: str, name: str) -> bool:
    row = (await conn.execute(
        text(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :n LIMIT 1"
        ),
        {"t": table, "n": name},
    )).first()
    return row is not None


_IF_NOT_EXISTS_RE = re.compile(r"\s+IF\s+NOT\s+EXISTS\b", re.IGNORECASE)


def _strip_if_not_exists(stmt: str) -> str:
    return _IF_NOT_EXISTS_RE.sub("", stmt, count=1)


async def _create_index_if_missing(conn, stmt: str, table: str, name: str) -> None:
    """Index anlegen, falls er fehlt. Fallback fuer DBs ohne ``CREATE INDEX IF NOT EXISTS`` (MySQL):
    Existenz per information_schema pruefen, dann das Statement ohne ``IF NOT EXISTS`` ausfuehren."""
    try:
        if await _index_exists(conn, table, name):
            return
    except Exception as exc:  # noqa: BLE001
        _record_error(stmt, exc)
        return
    await _exec_idempotent(conn, _strip_if_not_exists(stmt))


async def _column_exists(conn, table: str, column: str) -> bool:
    return (await _column_type(conn, table, column)) is not None


@dataclass(frozen=True)
class ModifyIfNeeded:
    """Eintrag der Kernliste: ``sql`` (MODIFY COLUMN) nur bei Bedarf (``needs``)."""

    table: str
    column: str
    sql: str
    needs: Callable[[tuple], bool]


SchemaEntry = Union[str, ModifyIfNeeded]

_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+`?(\w+)`?\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+`?(\w+)`?", re.IGNORECASE
)
_CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+`?(\w+)`?\s+ON\s+`?(\w+)`?", re.IGNORECASE
)


async def _run_schema_entry(conn, entry: SchemaEntry) -> None:
    """Ein Eintrag der Schema-Liste: MODIFY nur bei Bedarf, ADD COLUMN/CREATE INDEX mit
    Existenzpruefung (funktioniert auch ohne ``IF NOT EXISTS``-Unterstuetzung), sonst direkt."""
    if isinstance(entry, ModifyIfNeeded):
        await _modify_if_needed(conn, entry.table, entry.column, entry.sql, needs=entry.needs)
        return
    m = _ADD_COLUMN_RE.match(entry)
    if m:
        try:
            if await _column_exists(conn, m.group(1), m.group(2)):
                return
        except Exception as exc:  # noqa: BLE001
            _record_error(entry, exc)
            return
        await _exec_idempotent(conn, _strip_if_not_exists(entry))
        return
    m = _CREATE_INDEX_RE.match(entry)
    if m:
        await _create_index_if_missing(conn, entry, m.group(2), m.group(1))
        return
    await _exec_idempotent(conn, entry)


def _needs_text(info: tuple) -> bool:
    """api_key, webhooks.secret/url: Chiffretext braucht TEXT."""
    return info[0] not in _TEXT_TYPES


def _needs_varchar512(info: tuple) -> bool:
    """users.totp_*: VARCHAR(>=512) oder Text-Typ genuegt."""
    data_type, length = info
    if data_type in _TEXT_TYPES:
        return False
    return not (data_type == "varchar" and (length or 0) >= 512)


# Kernliste (Bauplan A.4, Reihenfolge verbindlich).
SCHEMA_STATEMENTS: list[SchemaEntry] = [
    # (1) Bestand aus 2.x (totp_* jetzt VARCHAR(512))
    "ALTER TABLE server_configs ADD COLUMN IF NOT EXISTS allow_writes TINYINT(1) DEFAULT 1",
    "ALTER TABLE user_zone_access ADD COLUMN IF NOT EXISTS permission VARCHAR(20) DEFAULT 'manage'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS company VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS street VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS postal_code VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth DATETIME",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled TINYINT(1) DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(512)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_pending_secret VARCHAR(512)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS webauthn_user_handle VARCHAR(64)",
    # (2) users: F3 + F10
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(16) NOT NULL DEFAULT 'local'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS external_issuer VARCHAR(255) "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS external_id VARCHAR(255) "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL",
    # (2b) users: Sitzungs-Widerruf (Schema-Nachtrag Welle 3, L3)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_revoked_at DATETIME NULL",
    # (3) users.totp_* verbreitern (F5)
    ModifyIfNeeded("users", "totp_secret",
                   "ALTER TABLE users MODIFY COLUMN totp_secret VARCHAR(512) NULL", _needs_varchar512),
    ModifyIfNeeded("users", "totp_pending_secret",
                   "ALTER TABLE users MODIFY COLUMN totp_pending_secret VARCHAR(512) NULL", _needs_varchar512),
    # (4) server_configs.api_key (F5)
    ModifyIfNeeded("server_configs", "api_key",
                   "ALTER TABLE server_configs MODIFY COLUMN api_key TEXT NOT NULL", _needs_text),
    # (5) webhooks.secret (F5: TEXT, nicht VARCHAR(512)) und (5b) webhooks.url [S10]
    ModifyIfNeeded("webhooks", "secret",
                   "ALTER TABLE webhooks MODIFY COLUMN secret TEXT NOT NULL", _needs_text),
    ModifyIfNeeded("webhooks", "url",
                   "ALTER TABLE webhooks MODIFY COLUMN url TEXT NOT NULL", _needs_text),
    # (6) webhooks: F6
    "ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS scope VARCHAR(16) NOT NULL DEFAULT 'own'",
    "ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS updated_at DATETIME NULL",
    "ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS last_success_at DATETIME NULL",
    "ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS last_failure_at DATETIME NULL",
    "ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS consecutive_failures INT NOT NULL DEFAULT 0",
    # (7) audit_logs: F7 + E-F7-1
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS zone_name VARCHAR(255) NULL",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS revert_of_id INT NULL",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_username VARCHAR(100) NULL",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS client_ip VARCHAR(64) NULL",
    # (8) panel_tokens: F14
    "ALTER TABLE panel_tokens ADD COLUMN IF NOT EXISTS scope_zones JSON NULL",
    "ALTER TABLE panel_tokens ADD COLUMN IF NOT EXISTS permission VARCHAR(10) NOT NULL DEFAULT 'manage'",
    "ALTER TABLE panel_tokens ADD COLUMN IF NOT EXISTS expires_at DATETIME NULL",
    "ALTER TABLE panel_tokens ADD COLUMN IF NOT EXISTS allow_admin TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE panel_tokens ADD COLUMN IF NOT EXISTS revoked_at DATETIME NULL",
    # (9) audit_logs-Indizes (F7)
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp ON audit_logs (`timestamp`)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_zone_ts ON audit_logs (zone_name, `timestamp`)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_revert_of_id ON audit_logs (revert_of_id)",
    # (10) eindeutige externe Identitaet (F10) – nach den Spalten
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_external ON users (auth_source, external_issuer, external_id)",
]

# Einmalige Datenmigrationen (Bauplan A.5): (name, statements); Marker migration_<name>.
F14_PANEL_TOKEN_MIGRATION: list[str] = [
    # In 2.4.1 bedeutete is_active=0 immer "geloescht" -> endgueltig widerrufen (nicht "pausiert").
    "UPDATE panel_tokens SET revoked_at = COALESCE(last_used_at, created_at, UTC_TIMESTAMP()) "
    "WHERE is_active = 0 AND revoked_at IS NULL",
    # Bestandstokens von Admins behalten ihre Admin-Rechte (Verhalten wie 2.4.1).
    "UPDATE panel_tokens pt JOIN users u ON u.id = pt.user_id SET pt.allow_admin = 1 "
    "WHERE u.role = 'admin' AND pt.revoked_at IS NULL",
]
DATA_MIGRATIONS: list[tuple[str, list[str]]] = [
    ("f14_panel_token_scope_v1", F14_PANEL_TOKEN_MIGRATION),
]


# ---------------------------------------------------------------------------
# Datenmigrationen, Spaltenpruefung, Slots
# ---------------------------------------------------------------------------


def _marker_key(name: str) -> str:
    return f"migration_{name}"


async def run_data_migration_once(session: AsyncSession, name: str, statements: list[str]) -> bool:
    """Fuehrt Daten-Statements genau einmal aus (Marker ``system_settings.key = "migration_<name>"``,
    Wert = ISO-UTC). Fehler -> Rollback, ``logger.error``, ``MIGRATION_ERRORS``, kein Marker (der
    naechste Start versucht es erneut); der Start laeuft weiter. True = jetzt ausgefuehrt."""
    from app.core.timeutil import iso_utc, utcnow

    key = _marker_key(name)
    try:
        found = (await session.execute(
            text("SELECT 1 FROM system_settings WHERE `key` = :k"), {"k": key}
        )).first()
        if found:
            return False
        for stmt in statements:
            await session.execute(text(stmt))
        now = utcnow()
        await session.execute(
            text("INSERT INTO system_settings (`key`, `value`, updated_at) VALUES (:k, :v, :u)"),
            {"k": key, "v": iso_utc(now), "u": now},
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - Datenmigration darf den Start nicht abbrechen
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        msg = _error_text(exc)
        logger.error("Datenmigration %s fehlgeschlagen (wird beim naechsten Start wiederholt): %s", name, msg)
        MIGRATION_ERRORS.append((f"data:{name}", msg))
        return False
    logger.info("Datenmigration %s ausgefuehrt", name)
    return True


def _missing_columns(db_columns: dict[str, set[str]], tables: Iterable) -> list[str]:
    """Gemappte Spalten, die in existierenden DB-Tabellen fehlen (``"tabelle.spalte"``)."""
    missing: list[str] = []
    for table in tables:
        have = db_columns.get(table.name.lower())
        if have is None:
            continue  # Tabelle existiert nicht (Pruefung nur fuer vorhandene Tabellen)
        for col in table.columns:
            if col.name.lower() not in have:
                missing.append(f"{table.name}.{col.name}")
    return missing


async def verify_mapped_columns(conn) -> None:
    """[D15] Jede ORM-gemappte Tabelle, die in der DB existiert, muss alle gemappten Spalten haben.

    Nur MySQL/MariaDB (andere Dialekte: keine Pruefung). Fehlende Spalten ->
    ``SchemaStartupError("SCHEMA_INCOMPLETE")``. Fehlende Indizes/Backfill sind nicht fatal.
    """
    if not _is_mysql(conn):
        return
    import app.models  # noqa: F401 - alle Modelle inkl. Slots an Base.metadata registrieren

    rows = (await conn.execute(text(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
    ))).all()
    db_columns: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        db_columns.setdefault(str(table_name).lower(), set()).add(str(column_name).lower())
    missing = _missing_columns(db_columns, Base.metadata.sorted_tables)
    if not missing:
        return
    lines = [f"Spalte {m} fehlt in der Datenbank." for m in missing]
    if MIGRATION_ERRORS:
        lines.append("Fehlgeschlagene Migrationsschritte:")
        lines.extend(f"  {stmt} -> {err}" for stmt, err in MIGRATION_ERRORS)
    lines.append(
        "Der Start wurde abgebrochen, damit keine Aenderungen ohne Audit-Eintrag entstehen. Ursache beheben "
        "(Rechte des Datenbank-Benutzers fuer ALTER TABLE, Speicherplatz, Sperren) und neu starten – die "
        "Migration wird dann wiederholt."
    )
    raise SchemaStartupError("SCHEMA_INCOMPLETE", "Datenbankschema unvollstaendig", lines)


def _iter_migration_slots(package: str = "app.core.migrations") -> list[ModuleType]:
    """Migrations-Slots ``<package>/*.py`` (ohne ``_``-Praefix), sortiert nach Dateiname (Regel 11)."""
    pkg = importlib.import_module(package)
    names = sorted(
        m.name for m in pkgutil.iter_modules(pkg.__path__)
        if not m.name.startswith("_") and not m.ispkg
    )
    mods: list[ModuleType] = []
    for name in names:
        mod = importlib.import_module(f"{package}.{name}")
        stmts = getattr(mod, "SCHEMA_STATEMENTS", [])
        data = getattr(mod, "DATA_MIGRATIONS", [])
        if not isinstance(stmts, (list, tuple)) or not all(isinstance(s, str) for s in stmts):
            raise TypeError(f"{mod.__name__}.SCHEMA_STATEMENTS muss eine Liste von Strings sein")
        if not isinstance(data, (list, tuple)) or not all(
            isinstance(d, (list, tuple)) and len(d) == 2 and isinstance(d[0], str) for d in data
        ):
            raise TypeError(f"{mod.__name__}.DATA_MIGRATIONS muss eine Liste von (name, [statements]) sein")
        mods.append(mod)
    return mods


async def init_db() -> None:
    """Tabellen anlegen und Schema/Daten migrieren (idempotent; Ablauf siehe Moduldoku).

    Wirft ``SchemaStartupError``, wenn nach der Migration gemappte Spalten fehlen.
    """
    import app.models  # noqa: F401 - Kern- und Slot-Modelle registrieren (create_all braucht sie)

    MIGRATION_ERRORS.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        is_mysql = _is_mysql(conn)
    if not is_mysql:
        logger.debug("init_db: Dialekt %s – nur create_all, keine Migrationen", engine.dialect.name)
        return

    slots = _iter_migration_slots()
    # DDL im AUTOCOMMIT-Modus: jedes Statement fuer sich, ein Fehler beeintraechtigt die folgenden nicht.
    async with engine.connect() as raw_conn:
        conn = await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
        for entry in SCHEMA_STATEMENTS:
            await _run_schema_entry(conn, entry)
        for mod in slots:
            for stmt in getattr(mod, "SCHEMA_STATEMENTS", []):
                await _run_schema_entry(conn, stmt)
        await verify_mapped_columns(conn)

    async with async_session() as session:
        for name, stmts in DATA_MIGRATIONS:
            await run_data_migration_once(session, name, list(stmts))
        for mod in slots:
            for name, stmts in getattr(mod, "DATA_MIGRATIONS", []):
                await run_data_migration_once(session, name, list(stmts))

    from app.services.audit import backfill_audit_zone_names  # spaet: audit -> models -> database

    await backfill_audit_zone_names()

    if MIGRATION_ERRORS:
        logger.error(
            "Datenbank-Migration mit %d Fehler(n) abgeschlossen – Details siehe vorherige Meldungen.",
            len(MIGRATION_ERRORS),
        )
