"""Gemeinsame Test-Fixtures.

Der SQLAlchemy-Async-Engine ist ein Modul-Global. Jeder ``TestClient`` bringt einen
eigenen Event-Loop mit; gepoolte aiomysql-Verbindungen aus einem frueheren Loop wuerden
im naechsten Test mit "attached to a different loop" / "Event loop is closed" scheitern
(in der CI mit echter MariaDB). Deshalb wird der Pool nach jedem Test verworfen, ohne
die alten Verbindungen im (bereits geschlossenen) Loop schliessen zu wollen.
"""
import asyncio

import pytest


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
