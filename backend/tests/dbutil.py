"""Gemeinsame DB-Test-Infrastruktur (Bauplan [F13]).

- ``requires_db``: einziges Gate fuer Tests gegen eine echte MariaDB – laeuft nur mit ``CI=true`` oder
  ``RUN_DB_TESTS=1`` UND gesetzter ``DATABASE_URL`` (lokal: ``scripts/dev/test-db.sh``, eine Datenbank
  je Workstream; Befehle in ``scripts/dev/README.md``).
- ``reset_schema(engine)``: loescht ALLE Tabellen der Testdatenbank (nur fuer Wegwerf-Datenbanken!).
- ``load_schema_241(engine)``: legt das Schema von PDNS Manager 2.4.1 an (``fixtures/schema_241.sql``).
- Fixtures ``fresh_db`` (Modul-Scope: reset + ``init_db()``) und ``db_241`` (Modul-Scope: reset + 2.4.1-DDL).
  Beide liefern den App-Engine (``app.core.database.engine``, ``DATABASE_URL``).

Die Fixtures sind synchron und fuehren ihre DB-Schritte mit ``asyncio.run`` aus; Tests duerfen async sein
(der Engine nutzt in Tests ``NullPool`` – keine Verbindung ueberlebt den Event-Loop).
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

SCHEMA_241_FILE = Path(__file__).resolve().parent / "fixtures" / "schema_241.sql"


def db_tests_enabled() -> bool:
    gate = os.environ.get("CI") == "true" or os.environ.get("RUN_DB_TESTS") == "1"
    return gate and bool(os.environ.get("DATABASE_URL"))


requires_db = pytest.mark.skipif(
    not db_tests_enabled(),
    reason="DB-Test: nur mit CI=true oder RUN_DB_TESTS=1 und DATABASE_URL (scripts/dev/test-db.sh)",
)


def _guard_dialect(engine) -> None:
    if engine.dialect.name not in ("mysql", "mariadb"):
        raise RuntimeError(f"DB-Tests brauchen MySQL/MariaDB, nicht {engine.dialect.name}")


async def reset_schema(engine) -> list[str]:
    """Loescht alle Tabellen (und Views) der aktuellen Datenbank. Rueckgabe: geloeschte Tabellen."""
    from sqlalchemy import text

    _guard_dialect(engine)
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        rows = (await conn.execute(text(
            "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
        ))).all()
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        names = []
        for name, kind in rows:
            stmt = "DROP VIEW" if str(kind).upper() == "VIEW" else "DROP TABLE"
            await conn.execute(text(f"{stmt} IF EXISTS `{name}`"))
            names.append(str(name))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return names


def schema_241_statements() -> list[str]:
    """CREATE-TABLE-Statements aus ``fixtures/schema_241.sql`` (Kommentarzeilen entfernt)."""
    raw = SCHEMA_241_FILE.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("--"))
    stmts = [s.strip() for s in re.split(r";\s*(?:\n|$)", body)]
    return [s for s in stmts if re.match(r"CREATE\s+TABLE\b", s, re.IGNORECASE)]


async def load_schema_241(engine) -> None:
    """Legt das 2.4.1-Schema an (Datenbank muss leer sein, siehe ``reset_schema``)."""
    from sqlalchemy import text

    _guard_dialect(engine)
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        for stmt in schema_241_statements():
            await conn.execute(text(stmt))


def _app_engine():
    from app.core.database import engine

    return engine


async def _fresh(engine) -> None:
    from app.core.database import init_db

    await reset_schema(engine)
    await init_db()


async def _schema_241(engine) -> None:
    await reset_schema(engine)
    await load_schema_241(engine)


@pytest.fixture(scope="module")
def fresh_db():
    """Leere Testdatenbank mit 3.0-Schema (reset + ``init_db()``), einmal je Testmodul."""
    if not db_tests_enabled():
        pytest.skip("DB-Test: nur mit CI=true oder RUN_DB_TESTS=1 und DATABASE_URL")
    engine = _app_engine()
    asyncio.run(_fresh(engine))
    asyncio.run(engine.dispose(close=False))
    yield engine
    asyncio.run(engine.dispose(close=False))


@pytest.fixture(scope="module")
def db_241():
    """Testdatenbank mit dem Schema von 2.4.1 (reset + DDL), einmal je Testmodul. Daten legt der Test an."""
    if not db_tests_enabled():
        pytest.skip("DB-Test: nur mit CI=true oder RUN_DB_TESTS=1 und DATABASE_URL")
    engine = _app_engine()
    asyncio.run(_schema_241(engine))
    asyncio.run(engine.dispose(close=False))
    yield engine
    asyncio.run(engine.dispose(close=False))
