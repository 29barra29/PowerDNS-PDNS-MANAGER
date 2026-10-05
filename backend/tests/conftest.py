"""Gemeinsame Test-Fixtures.

Der SQLAlchemy-Async-Engine ist ein Modul-Global. Jeder ``TestClient`` bringt einen
eigenen Event-Loop mit; gepoolte aiomysql-Verbindungen aus einem frueheren Loop wuerden
im naechsten Test mit "attached to a different loop" / "Event loop is closed" scheitern
(in der CI mit echter MariaDB). Deshalb wird der Pool nach jedem Test verworfen, ohne
die alten Verbindungen im (bereits geschlossenen) Loop schliessen zu wollen.

Ausserdem (vor dem ersten App-Import):
- ``SECRET_ENCRYPTION_KEY``: fester Testschluessel (gueltiger Fernet-Key, nur fuer Tests – urlsafe-Base64
  von ``test-key-for-pdns-manager-32byte``). ``SECOND_TEST_KEY`` ist ein zweiter Testschluessel
  (Rotation/Fremdschluessel). Beide sind oeffentlich und schuetzen nichts.
- ``BACKGROUND_WORKERS_ENABLED=false``: keine Hintergrund-Tasks (Webhook-Worker, Audit-Bereinigung).
- Nach jedem Test: Modulzustand von ``core.secrets`` und der Request-Kontext (ContextVars) zurueckgesetzt.

DB-Tests: ``tests/dbutil.py`` (``requires_db``, Fixtures ``fresh_db``/``db_241``) wird hier importiert,
damit die Fixtures in allen Testmodulen verfuegbar sind.
"""
import asyncio
import os

import pytest

TEST_SECRET_KEY = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="
SECOND_TEST_KEY = "c2Vjb25kLXRlc3Qta2V5LXBkbnMtbWFuYWdlci0zMmI="

# Vor dem ersten App-Import: kein Verbindungspool in Tests. Sonst bleiben aiomysql-
# Verbindungen aus dem Event-Loop eines TestClients haengen und erzeugen beim naechsten
# Test "attached to a different loop" bzw. beim GC "Event loop is closed"-Tracebacks.
os.environ.setdefault("DB_POOL_SIZE", "0")
os.environ.setdefault("SECRET_ENCRYPTION_KEY", TEST_SECRET_KEY)
os.environ.setdefault("BACKGROUND_WORKERS_ENABLED", "false")

from dbutil import db_241, fresh_db  # noqa: E402,F401 - Fixtures fuer alle Testmodule registrieren


@pytest.fixture(autouse=True)
def _discard_db_pool_after_test():
    yield
    try:
        from app.core.database import engine
    except Exception:  # noqa: BLE001 - App nicht importierbar -> nichts zu tun
        return
    try:
        asyncio.run(engine.dispose(close=False))
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _reset_secret_state():
    """Modulzustand der Geheimnis-Verschluesselung nach jedem Test zuruecksetzen (F5 §9)."""
    yield
    from app.core import secrets as secret_store

    secret_store.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_request_context():
    """ContextVars (auth_via, Token-Scope, Akteur, Client-IP) nicht von Test zu Test vererben."""
    yield
    from app.core.request_context import reset_request_context

    reset_request_context()
