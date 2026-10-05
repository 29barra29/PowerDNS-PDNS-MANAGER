"""Suche: ACL vor der Kappung, truncated-Flag, Validierung, keine internen Fehlertexte (F8 9.1 Nr. 1–6, F14)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from app.core.auth import get_current_user
from app.routers import search
from app.services.pdns_client import pdns_manager


class _Client:
    def __init__(self, hits=None, exc=None):
        self.hits = hits or []
        self.exc = exc
        self.calls = []

    async def search(self, q, max_results=100, object_type="all"):
        self.calls.append((q, max_results, object_type))
        if self.exc:
            raise self.exc
        return list(self.hits[:max_results])


def _hits(n_foreign, n_own, own_zone="kunde.de."):
    foreign = [{"object_type": "record", "name": f"x{i}.fremd.de.", "zone_id": "fremd.de."} for i in range(n_foreign)]
    own = [{"object_type": "record", "name": f"r{i}.{own_zone}", "zone_id": own_zone} for i in range(n_own)]
    return foreign + own


def _app(monkeypatch, client, allowed, role="user"):
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": client})
    monkeypatch.setattr(search, "_allowed_zones_for", AsyncMock(return_value=allowed))
    app = build_app(FakeSession(), search)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=5, role=role)
    return TestClient(app, raise_server_exceptions=False)


def test_search_acl_filters_before_cap(monkeypatch):
    # 998 fremde + 1 eigener Treffer: die Ueberabfrage (1000) ist nicht ausgeschoepft -> nicht truncated.
    # (F8 9.1 Nr. 1 nennt 999 + 1; dann liefert PowerDNS genau die Ueberabfrage-Menge und nach der
    #  Definition in F8 3.1/5.1 "mehr Treffer moeglich" ist truncated korrekt true – siehe naechster Test.)
    client = _Client(_hits(998, 1))
    c = _app(monkeypatch, client, {"kunde.de."})
    r = c.get("/api/v1/search/srv1?q=x&max_results=100")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["results"][0]["zone_id"] == "kunde.de."
    assert client.calls[0][1] >= 1000
    assert body["truncated"] is False


def test_search_truncated_flag_for_non_admin(monkeypatch):
    client = _Client(_hits(0, 150))
    c = _app(monkeypatch, client, {"kunde.de."})
    body = c.get("/api/v1/search/srv1?q=r&max_results=100").json()
    assert body["count"] == 100 and body["truncated"] is True


def test_search_truncated_when_overfetch_exhausted(monkeypatch):
    # PowerDNS liefert genau die Ueberabfrage-Menge -> es koennte mehr geben
    client = _Client(_hits(1000, 2))
    c = _app(monkeypatch, client, {"kunde.de."})
    body = c.get("/api/v1/search/srv1?q=x&max_results=10").json()
    assert client.calls[0][1] == 1000
    assert body["truncated"] is True


def test_search_admin_passes_limit_unchanged(monkeypatch):
    client = _Client(_hits(100, 0))
    c = _app(monkeypatch, client, None, role="admin")
    body = c.get("/api/v1/search/srv1?q=x&max_results=100").json()
    assert client.calls[0][1] == 100 and body["count"] == 100 and body["truncated"] is True


def test_search_without_zone_access_skips_pdns(monkeypatch):
    client = _Client(_hits(5, 5))
    c = _app(monkeypatch, client, set())
    body = c.get("/api/v1/search/srv1?q=x").json()
    assert client.calls == [] and body["count"] == 0 and body["truncated"] is False


def test_search_object_type_validated(monkeypatch):
    client = _Client([])
    c = _app(monkeypatch, client, None, role="admin")
    assert c.get("/api/v1/search/srv1?q=x&object_type=foo").status_code == 422
    for ok in ("all", "zone", "record", "comment"):
        assert c.get(f"/api/v1/search/srv1?q=x&object_type={ok}").status_code == 200
    assert [x[2] for x in client.calls] == ["all", "zone", "record", "comment"]


def test_search_all_servers_hides_exception_text(monkeypatch):
    bad = _Client(exc=Exception("http://10.0.0.1:8081 boom"))
    good = _Client(_hits(0, 2))
    monkeypatch.setattr(pdns_manager, "clients", {"bad": bad, "good": good})
    monkeypatch.setattr(search, "_allowed_zones_for", AsyncMock(return_value={"kunde.de."}))
    app = build_app(FakeSession(), search)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=5, role="user")
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/search?q=x")
    assert r.status_code == 200
    assert "Suche auf diesem Server fehlgeschlagen" in r.text and "10.0.0.1" not in r.text
    servers = r.json()["servers"]
    assert servers["bad"] == {"count": 0, "truncated": False, "results": [],
                              "error": "Suche auf diesem Server fehlgeschlagen"}
    assert servers["good"]["count"] == 2


def test_search_unknown_server_404(monkeypatch):
    c = _app(monkeypatch, _Client([]), None, role="admin")
    assert c.get("/api/v1/search/nope?q=x").status_code == 404


@pytest.mark.asyncio
async def test_allowed_zones_delegates_to_effective_zone_filter(monkeypatch):
    seen = {}

    async def fake_filter(db, user):
        seen["args"] = (db, user)
        return {"a."}

    monkeypatch.setattr(search, "effective_zone_filter", fake_filter)
    db, user = object(), object()
    assert await search._allowed_zones_for(db, user) == {"a."}
    assert seen["args"] == (db, user)


def test_record_belongs_to_zone_normalized():
    assert search._record_belongs_to_zone("Kunde.DE", {"kunde.de."})
    assert not search._record_belongs_to_zone("", {"kunde.de."})
    assert not search._record_belongs_to_zone("fremd.de.", {"kunde.de."})


def test_user_acl_and_token_scope_intersection_end_to_end(monkeypatch):
    """Echter Pfad ohne Mock von _allowed_zones_for: Benutzer-ACL aus der (Fake-)DB."""
    client = _Client(_hits(3, 2))
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": client})
    session = FakeSession(zone_access=[("Kunde.de", "read")])
    app = build_app(session, search)
    app.dependency_overrides[get_current_user] = lambda: make_user(role="user", uid=5, username="bob")
    body = TestClient(app, raise_server_exceptions=False).get("/api/v1/search/srv1?q=x").json()
    assert body["count"] == 2 and {r["zone_id"] for r in body["results"]} == {"kunde.de."}
