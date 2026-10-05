"""Panel-Token-Scope fuer zones/records/dnssec und /api/v1/metrics (F14 9.2 Nr. 2–4, 7, 9, 10; ohne DB).

Gegenstueck zu ``test_panel_token_scope.py`` (W0-INT-BE2a, dort die uebrigen Router). Header immer
``Authorization: Bearer <PLAIN>``; Besitzer ist Admin, damit ``assert_zone_access`` ohne Zonenrechte-Abfrage
auskommt – der Token-Scope greift trotzdem (vor dem Admin-Shortcut). ``FakePDNS.calls`` zaehlt nur
schreibende PowerDNS-Aufrufe.
"""
import pytest
from fastapi.testclient import TestClient

from authfakes import FakePDNS, FakeSession, bearer, build_app, make_token, make_user
from app.core.auth import TOKEN_NO_ADMIN_DETAIL, TOKEN_READ_ONLY_DETAIL, create_access_token
from app.core.database import get_db
from app.routers import dnssec, records, zones
from app.services import audit as audit_service
from app.services import webhook_outbox
from app.services.pdns_client import pdns_manager

A = "/api/v1"
OTHER = "other.example."
RECORD = {"name": f"www.{OTHER}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.1"}]}
UPDATE = {"name": f"www.{OTHER}", "type": "A", "ttl": 300, "old_content": "192.0.2.1", "new_content": "192.0.2.2"}


@pytest.fixture
def pdns(monkeypatch):
    fake = FakePDNS()
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    return fake


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Keine echten Outbox-/Detached-Audit-Zugriffe (Fake-Session, keine DB)."""
    async def enqueue(*a, **k):
        return 0

    async def detached(*a, **k):
        return None

    monkeypatch.setattr(webhook_outbox, "enqueue_event", enqueue)
    monkeypatch.setattr(audit_service, "write_audit_detached", detached)


def _client(token_row=None, user_row=None, **kw):
    session = FakeSession(token_row=token_row, user_row=user_row if user_row is not None else make_user(), **kw)
    return TestClient(build_app(session, zones, records, dnssec), raise_server_exceptions=False), session


# --- Nr. 2: 17 Zonen-Endpunkte ausserhalb des Scopes ----------------------------------------------
ZONE_ENDPOINTS = [
    ("GET", f"{A}/zones/srv1/{OTHER}/detail", None),
    ("PUT", f"{A}/zones/srv1/{OTHER}", {"soa_edit_api": "DEFAULT"}),
    ("POST", f"{A}/zones/srv1/{OTHER}/notify", {}),
    ("GET", f"{A}/zones/srv1/{OTHER}/export", None),
    ("GET", f"{A}/records/srv1/{OTHER}", None),
    ("POST", f"{A}/records/srv1/{OTHER}", RECORD),
    ("PUT", f"{A}/records/srv1/{OTHER}", UPDATE),
    ("DELETE", f"{A}/records/srv1/{OTHER}/delete", {"name": f"www.{OTHER}", "type": "A"}),
    ("POST", f"{A}/records/srv1/{OTHER}/bulk", {"create": [], "delete": []}),
    ("GET", f"{A}/dnssec/srv1/{OTHER}/keys", None),
    ("GET", f"{A}/dnssec/srv1/{OTHER}/keys/1", None),
    ("POST", f"{A}/dnssec/srv1/{OTHER}/enable", {}),
    ("POST", f"{A}/dnssec/srv1/{OTHER}/disable", None),
    ("POST", f"{A}/dnssec/srv1/{OTHER}/keys/1/activate", None),
    ("POST", f"{A}/dnssec/srv1/{OTHER}/keys/1/deactivate", None),
    ("DELETE", f"{A}/dnssec/srv1/{OTHER}/keys/1", None),
    ("GET", f"{A}/dnssec/srv1/{OTHER}/ds", None),
]


def test_zone_endpoint_list_is_complete():
    assert len(ZONE_ENDPOINTS) == 17


@pytest.mark.parametrize("method,path,body", ZONE_ENDPOINTS)
def test_out_of_scope_zone_endpoints_return_403(pdns, method, path, body):
    c, s = _client(make_token())
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, r.text
    assert "„other.example.“ nicht freigegeben" in r.json()["detail"]
    assert pdns.calls == {} and s.added == []


# --- Nr. 3: in Scope --------------------------------------------------------------------------------
@pytest.mark.parametrize("zone", ["allowed.example.", "ALLOWED.example"])
def test_in_scope_zone_detail_ok(pdns, zone):
    c, _ = _client(make_token())
    assert c.get(f"{A}/zones/srv1/{zone}/detail", headers=bearer()).status_code == 200
    assert c.get(f"{A}/records/srv1/{zone}", headers=bearer()).status_code == 200


# --- Nr. 4: Zonenliste ------------------------------------------------------------------------------
def test_zone_list_filtered_by_scope(pdns):
    c, _ = _client(make_token())
    r = c.get(f"{A}/zones/srv1", headers=bearer())
    assert r.status_code == 200, r.text
    assert [z["name"] for z in r.json()["zones"]] == ["allowed.example."]


def test_zone_list_without_scope_admin_token_sees_all(pdns):
    c, _ = _client(make_token(scope_zones=None))
    names = [z["name"] for z in c.get(f"{A}/zones/srv1", headers=bearer()).json()["zones"]]
    assert names == ["allowed.example.", "other.example.", "Third.Example."]


def test_zone_list_user_acl_normalized_and_intersected_with_scope(pdns):
    user = make_user(role="user", uid=5, username="bob")
    access = [("third.example", "read"), ("allowed.example.", "manage")]
    # Session: Zonenrechte, Vergleich normalisiert (gross/klein, Trailing-Dot)
    c, _ = _client(None, user, zone_access=access)
    jwt = create_access_token(data={"sub": "5"}, user=user)
    names = [z["name"] for z in c.get(f"{A}/zones/srv1", headers={"Authorization": f"Bearer {jwt}"}).json()["zones"]]
    assert names == ["allowed.example.", "Third.Example."]
    # Token des Benutzers: Schnittmenge aus Zonenrechten und Scope
    c, _ = _client(make_token(user_id=5, scope_zones=["third.example."]), user, zone_access=access)
    names = [z["name"] for z in c.get(f"{A}/zones/srv1", headers=bearer()).json()["zones"]]
    assert names == ["Third.Example."]


def test_zone_list_includes_edited_serial(pdns, monkeypatch):
    async def list_zones(timeout: float = 30.0):
        return [{"name": "allowed.example.", "id": "allowed.example.", "kind": "Native", "serial": 5,
                 "edited_serial": 6}]

    monkeypatch.setattr(pdns, "list_zones", list_zones)
    c, _ = _client(make_token(scope_zones=None))
    zone = c.get(f"{A}/zones/srv1", headers=bearer()).json()["zones"][0]
    assert zone["serial"] == 5 and zone["edited_serial"] == 6


# --- Nr. 7: Admin-Zonen-Endpunkte brauchen allow_admin ------------------------------------------------
ADMIN_ZONE_CASES = [
    ("POST", f"{A}/zones", {"name": "allowed.example", "servers": ["srv1"]}),
    ("DELETE", f"{A}/zones/srv1/allowed.example.", None),
    ("POST", f"{A}/zones/import/preview", {"name": "allowed.example", "content": "x"}),
    ("POST", f"{A}/zones/import", {"name": "allowed.example", "content": "x"}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ZONE_CASES)
def test_admin_zone_endpoints_require_allow_admin(pdns, method, path, body):
    c, s = _client(make_token(scope_zones=None))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == TOKEN_NO_ADMIN_DETAIL
    assert pdns.calls == {} and s.added == []


@pytest.mark.parametrize("method,path,body", [
    ("POST", f"{A}/zones", {"name": "other.example", "servers": ["srv1"]}),
    ("DELETE", f"{A}/zones/srv1/other.example.", None),
    ("POST", f"{A}/zones/import/preview", {"name": "other.example", "content": "x"}),
    ("POST", f"{A}/zones/import", {"name": "other.example", "content": "x"}),
])
def test_admin_zone_endpoints_respect_token_scope(pdns, method, path, body):
    """F14 5.6: auch mit allow_admin nur Zonen im Scope (Defense in Depth)."""
    c, s = _client(make_token(allow_admin=True))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, r.text
    assert "„other.example.“ nicht freigegeben" in r.json()["detail"]
    assert pdns.calls == {} and s.added == []


def test_admin_zone_endpoints_with_allow_admin_in_scope(pdns):
    c, _ = _client(make_token(allow_admin=True))
    r = c.post(f"{A}/zones", headers=bearer(), json={"name": "allowed.example", "servers": ["srv1"]})
    assert r.status_code == 200 and r.json()["details"] == {"srv1": "created"}
    r = c.post(f"{A}/zones/import/preview", headers=bearer(), json={"name": "allowed.example", "content": "x"})
    assert r.status_code == 400  # kein schreibbarer Server in der Fake-DB – aber Scope und Admin-Pruefung bestanden
    assert c.delete(f"{A}/zones/srv1/allowed.example.", headers=bearer()).status_code == 200
    assert pdns.calls == {"create_zone": 1, "delete_zone": 1}


# --- Nr. 9: Lese-Token --------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", [
    ("POST", f"{A}/records/srv1/allowed.example.", {**RECORD, "name": "www.allowed.example."}),
    ("PUT", f"{A}/records/srv1/allowed.example.", {**UPDATE, "name": "www.allowed.example."}),
    ("DELETE", f"{A}/records/srv1/allowed.example./delete", {"name": "www.allowed.example.", "type": "A"}),
    ("POST", f"{A}/zones/srv1/allowed.example./notify", {}),
    ("POST", f"{A}/dnssec/srv1/allowed.example./enable", {}),
    ("POST", f"{A}/zones", {"name": "allowed.example", "servers": ["srv1"]}),
])
def test_read_token_blocks_mutations(pdns, method, path, body):
    c, s = _client(make_token(scope_zones=None, permission="read", allow_admin=True))
    r = c.request(method, path, headers=bearer(), json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == TOKEN_READ_ONLY_DETAIL
    assert pdns.calls == {} and s.flushes == 0


def test_read_token_allows_reads(pdns):
    c, _ = _client(make_token(scope_zones=None, permission="read", allow_admin=True))
    assert c.get(f"{A}/records/srv1/allowed.example.", headers=bearer()).status_code == 200
    assert c.get(f"{A}/zones/srv1", headers=bearer()).status_code == 200


# --- Nr. 10: kind/masters/account nur effektive Admins -------------------------------------------------
def test_update_zone_kind_requires_effective_admin(pdns):
    c, _ = _client(make_token())
    r = c.put(f"{A}/zones/srv1/allowed.example.", headers=bearer(), json={"kind": "Master"})
    assert r.status_code == 403
    assert r.json()["detail"] == "kind, masters und account darf nur ein Admin aendern"
    assert pdns.calls == {}
    # ohne Sonderfelder darf derselbe Token die Zone aendern
    assert c.put(f"{A}/zones/srv1/allowed.example.", headers=bearer(), json={"soa_edit_api": "DEFAULT"}).status_code == 200
    c, _ = _client(make_token(scope_zones=None, allow_admin=True))
    r = c.put(f"{A}/zones/srv1/allowed.example.", headers=bearer(), json={"kind": "Master"})
    assert r.status_code == 200, r.text
    assert pdns.calls.get("update_zone") == 2


def test_update_zone_kind_non_admin_session_forbidden(pdns):
    user = make_user(role="user", uid=5, username="bob")
    c, _ = _client(None, user, zone_access=[("allowed.example.", "manage")])
    jwt = create_access_token(data={"sub": "5"}, user=user)
    r = c.put(f"{A}/zones/srv1/allowed.example.", headers={"Authorization": f"Bearer {jwt}"},
              json={"masters": ["192.0.2.1"]})
    assert r.status_code == 403 and pdns.calls == {}


# --- Nr. 7 (Teil): GET /api/v1/metrics in main.py --------------------------------------------------
def _main_client(session):
    from app.main import app

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return app, TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("token_kw,user_kw,status", [
    ({"scope_zones": None}, {}, 403),                                   # Admin-Token ohne allow_admin
    ({"scope_zones": None, "allow_admin": True}, {}, 200),              # mit Freigabe
    ({"scope_zones": None, "allow_admin": True}, {"role": "user"}, 403),  # Nicht-Admin-Besitzer
])
def test_json_metrics_requires_effective_admin(pdns, token_kw, user_kw, status):
    session = FakeSession(token_row=make_token(**token_kw), user_row=make_user(**user_kw))
    app, c = _main_client(session)
    try:
        r = c.get(f"{A}/metrics", headers=bearer())
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == status, r.text
    if status == 200:
        assert set(r.json()) == {"app", "version", "uptime_seconds", "api_request_count"}
    elif token_kw.get("allow_admin") is None:
        assert r.json()["detail"] == TOKEN_NO_ADMIN_DETAIL
