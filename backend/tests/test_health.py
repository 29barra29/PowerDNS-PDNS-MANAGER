"""Integration: braucht erreichbare DB (GitHub Actions: MariaDB-Service).

Lokal ohne MariaDB: wird übersprungen, damit `pytest` nicht an `init_db` scheitert.
"""
import os

import pytest
from fastapi.testclient import TestClient

_RUN = os.environ.get("CI") == "true" or os.environ.get("RUN_HEALTH_INTEGRATION") == "1"

pytestmark = pytest.mark.skipif(
    not _RUN or not os.environ.get("DATABASE_URL"),
    reason="Nur in CI (MariaDB) oder mit RUN_HEALTH_INTEGRATION=1 + DATABASE_URL",
)


def test_health_includes_real_database_status():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code in (200, 503)
    data = r.json()
    assert data.get("database") in ("connected", "disconnected")
    if r.status_code == 200:
        assert data.get("status") in ("healthy", "degraded")
    else:
        assert data.get("status") == "unhealthy"
