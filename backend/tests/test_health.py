"""``GET /health``: oeffentlich nur status/database/servers, Zusatzfelder nur fuer Loopback-Peers [S14].

- Ohne DB: Datenbank-Engine und PowerDNS-Clients werden ersetzt; geprueft werden Status, Felder und die
  Loopback-Regel (direkter Peer 127.0.0.0/8 bzw. ::1, ohne Proxy-Header – Forwarded-Header zaehlen nicht).
- Integration: braucht erreichbare DB (GitHub Actions: MariaDB-Service). Lokal ohne MariaDB wird der Fall
  uebersprungen, damit ``pytest`` nicht an ``init_db`` scheitert.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core import secrets as secret_store
from app.services.pdns_client import pdns_manager

_RUN = os.environ.get("CI") == "true" or os.environ.get("RUN_HEALTH_INTEGRATION") == "1"

PUBLIC_KEYS = {"status", "database", "servers"}
LOCAL_KEYS = PUBLIC_KEYS | {"secrets", "migration_errors", "schema", "background", "servers_not_loaded"}


@pytest.mark.skipif(
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
    assert set(data) == PUBLIC_KEYS  # TestClient-Peer "testclient" ist kein Loopback
    if r.status_code == 200:
        assert data.get("status") in ("healthy", "degraded")
    else:
        assert data.get("status") == "unhealthy"


# --------------------------------------------------------------------------- ohne DB
class _Conn:
    def __init__(self, ok):
        self.ok = ok

    async def __aenter__(self):
        if not self.ok:
            raise OSError("DB weg")
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return None


class _Engine:
    def __init__(self, ok=True):
        self.ok = ok

    def connect(self):
        return _Conn(self.ok)


class _PDNS:
    def __init__(self, ok=True):
        self.ok = ok

    async def get_server_info(self, timeout: float = 30.0):
        if not self.ok:
            raise OSError("down")
        return {"version": "4.9"}


@pytest.fixture
def health(monkeypatch):
    monkeypatch.setattr(main, "engine", _Engine(True))
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": _PDNS(True)})
    monkeypatch.setattr(pdns_manager, "unloaded", {"ns9": "api key unreadable"})
    monkeypatch.setattr(main, "MIGRATION_ERRORS", [("ALTER TABLE audit_logs ADD COLUMN x", "OperationalError: denied")])
    secret_store.configure_for_tests()

    def make(peer=("testclient", 50000)):
        return TestClient(main.app, client=peer)

    return make


def test_public_health_has_only_basic_fields(health):
    r = health().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "database": "connected", "servers": {"ns1": "healthy"}}


@pytest.mark.parametrize("peer", [("192.0.2.10", 1234), ("172.18.0.1", 1234), ("::ffff:10.0.0.1", 1)])
def test_remote_peers_get_public_view(health, peer):
    assert set(health(peer).get("/health").json()) == PUBLIC_KEYS


@pytest.mark.parametrize("peer", [("127.0.0.1", 40000), ("127.8.9.1", 1), ("::1", 40000), ("::ffff:127.0.0.1", 1)])
def test_loopback_peer_gets_details(health, peer):
    r = health(peer).get("/health")
    body = r.json()
    assert r.status_code == 200 and set(body) == LOCAL_KEYS
    assert body["secrets"]["mode"] == "encrypted" and body["secrets"]["key_source"] == "env"
    assert body["migration_errors"] == [{"statement": "ALTER TABLE audit_logs ADD COLUMN x",
                                         "error": "OperationalError: denied"}]
    assert body["schema"] == {"ok": False, "migration_error_count": 1}
    assert set(body["background"]) == {"enabled", "tasks"}
    assert body["servers_not_loaded"] == {"ns9": "api key unreadable"}
    # nie Schluesselmaterial
    assert "SECRET_ENCRYPTION_KEY" not in r.text and os.environ.get("SECRET_ENCRYPTION_KEY", "-") not in r.text


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_loopback_behind_proxy_gets_public_view(health, header):
    """Proxy auf demselben Host: Forwarded-Header werden nicht ausgewertet -> oeffentliche Sicht."""
    r = health(("127.0.0.1", 40000)).get("/health", headers={header: "for=203.0.113.5" if header == "Forwarded"
                                                              else "203.0.113.5"})
    assert set(r.json()) == PUBLIC_KEYS


def test_status_degraded_and_unhealthy(health, monkeypatch):
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": _PDNS(True), "ns2": _PDNS(False)})
    body = health().get("/health").json()
    assert body["status"] == "degraded" and body["servers"] == {"ns1": "healthy", "ns2": "unreachable"}
    monkeypatch.setattr(main, "engine", _Engine(False))
    r = health().get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unhealthy", "database": "disconnected",
                        "servers": {"ns1": "healthy", "ns2": "unreachable"}}


def test_plaintext_fallback_is_degraded_reason_only_local(health):
    secret_store.configure_for_tests(mode="plaintext_fallback")
    pub = health().get("/health").json()
    assert pub == {"status": "degraded", "database": "connected", "servers": {"ns1": "healthy"}}
    local = health(("127.0.0.1", 1)).get("/health").json()
    assert local["status"] == "degraded"
    assert local["secrets"]["mode"] == "plaintext_fallback"
    assert local["secrets"]["fallback_reason"] == "key_file_unwritable"


def test_health_details_failure_never_breaks_health(health, monkeypatch):
    def boom():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(main, "_health_details", boom)
    r = health(("127.0.0.1", 1)).get("/health")
    assert r.status_code == 200 and set(r.json()) == PUBLIC_KEYS
