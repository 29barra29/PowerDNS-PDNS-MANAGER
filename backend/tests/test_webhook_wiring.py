"""Verdrahtung der Webhook-Outbox (F6 9.1 Nr. 17–20, Bauplan B.8).

- AST ueber ``backend/app``: jeder ``enqueue_event``-Aufruf steht unter ``await``, sendet ein String-Literal aus
  ``EVENT_CATALOG``; ``deliver_webhooks_background`` gibt es nicht mehr.
- Routen-Walk: Webhook-Verwaltung nur per Browser-Session (Liste auch per Token), keine Ueberdeckung durch den
  SPA-Catch-all.
- Aufrufstellen in records/zones/dnssec: Ereignis, Zone, Server, Akteur und ``audit_log_id`` (HTTP gegen die
  Router mit Fake-Session und Fake-PowerDNS, ``enqueue_event`` aufgezeichnet).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authfakes import FakePDNS, FakeSession, build_app, make_user
from app.core.auth import create_access_token, get_current_user, get_session_user
from app.services import audit as audit_service
from app.services import webhook_outbox
from app.services.pdns_client import PowerDNSAPIError, pdns_manager
from app.services.webhook_events import EVENT_CATALOG

APP_DIR = Path(__file__).resolve().parents[1] / "app"
A = "/api/v1"


# --------------------------------------------------------------------------- AST
def _sources():
    for path in sorted(APP_DIR.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _enqueue_calls():
    for path, tree in _sources():
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "enqueue_event":
                yield path, node, parents.get(node)


def test_no_unawaited_enqueue_calls():
    calls = list(_enqueue_calls())
    assert calls, "keine enqueue_event-Aufrufe gefunden"
    bad = [f"{p.relative_to(APP_DIR)}:{n.lineno}" for p, n, parent in calls if not isinstance(parent, ast.Await)]
    assert not bad, f"enqueue_event ohne await: {bad}"


def test_no_legacy_delivery_identifier():
    hits = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.alias):
                names.append(node.name)
            if "deliver_webhooks_background" in names:
                hits.append(f"{path.relative_to(APP_DIR)}:{getattr(node, 'lineno', '?')}")
        if "deliver_webhooks_background" in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(APP_DIR)))
    assert not hits, hits


def _event_arg(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "event":
            return kw.value
    return call.args[1] if len(call.args) >= 2 else None


def test_emitted_events_are_in_catalog():
    emitted = {}
    for path, node, _ in _enqueue_calls():
        if path.name == "webhook_outbox.py":
            continue  # Definition/Docstring-Beispiele
        arg = _event_arg(node)
        loc = f"{path.relative_to(APP_DIR)}:{node.lineno}"
        assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), f"{loc}: Ereignis kein String-Literal"
        assert arg.value in EVENT_CATALOG, f"{loc}: {arg.value} nicht im Katalog"
        emitted.setdefault(arg.value, []).append(loc)
    expected = {
        "record.created", "record.updated", "record.deleted", "record.bulk",
        "zone.created", "zone.updated", "zone.deleted", "zone.imported",
        "dnssec.enabled", "dnssec.disabled", "dnssec.key_activated", "dnssec.key_deactivated", "dnssec.key_deleted",
    }
    assert expected <= set(emitted), sorted(expected - set(emitted))


# --------------------------------------------------------------------------- Routen
def _routes():
    from app.main import app
    from route_policy import iter_app_routes

    return app, iter_app_routes(app)


def _direct(route):
    return [d.call for d in route.dependant.dependencies]


def test_webhook_mutations_require_browser_session():
    _, routes = _routes()
    hooks = [r for r in routes if r.path.startswith(f"{A}/auth/me/webhooks")]
    assert {(r.method, r.path) for r in hooks} >= {
        ("GET", f"{A}/auth/me/webhooks"), ("POST", f"{A}/auth/me/webhooks"),
        ("PUT", f"{A}/auth/me/webhooks/{{webhook_id}}"), ("DELETE", f"{A}/auth/me/webhooks/{{webhook_id}}"),
    }
    for r in hooks:
        if (r.method, r.path) == ("GET", f"{A}/auth/me/webhooks"):
            assert get_current_user in _direct(r)
        else:
            assert get_session_user in _direct(r), (r.method, r.path)


def test_webhook_routes_not_shadowed():
    from fastapi.routing import iter_route_contexts

    from app.main import app

    ctxs = list(iter_route_contexts(app.routes))
    paths = [c.path for c in ctxs]
    spa = paths.index("/{path:path}")
    for p in (f"{A}/auth/me/webhooks", f"{A}/auth/me/webhooks/{{webhook_id}}"):
        idx = [i for i, x in enumerate(paths) if x == p]
        assert idx and all(i < spa for i in idx), p
    keys = [(m, c.path) for c in ctxs for m in (c.methods or ())]
    hook_keys = [k for k in keys if k[1].startswith(f"{A}/auth/me/webhooks")]
    assert len(hook_keys) == len(set(hook_keys))


# --------------------------------------------------------------------------- Aufrufstellen
class Recorder:
    def __init__(self, session=None):
        self.calls = []
        self.session = session

    async def __call__(self, db, event, *, actor, data, zone=None, server=None, audit_log_id=None, actor_via=None):
        self.calls.append({
            "event": event, "actor": getattr(actor, "id", None), "data": data, "zone": zone, "server": server,
            "audit_log_id": audit_log_id, "executed_before": len(getattr(db, "executed", [])),
        })
        return 1

    def events(self):
        return [c["event"] for c in self.calls]


class DnssecPDNS(FakePDNS):
    """Schluessel mit Zustand (F4-A: enable ist idempotent, Schutzregeln pruefen den Ist-Zustand)."""

    def __init__(self, name: str = "srv1"):
        super().__init__(name)
        self.keys: list[dict] = []

    async def get_zone_meta(self, zone_id, **kw):
        return {"name": zone_id, "kind": "Native"}

    async def get_cryptokeys(self, zone_id, timeout: float = 30.0):
        return [dict(k, privatekey="GEHEIM") for k in self.keys]

    async def add_cryptokey(self, zone_id, key_data):
        self._count("add_cryptokey")
        key = {"id": len(self.keys) + 1, "keytype": key_data["keytype"], "algorithm": key_data["algorithm"],
               "active": key_data["active"], "published": True, "ds": ["ds-line"]}
        self.keys.append(key)
        return dict(key, privatekey="GEHEIM")

    async def update_cryptokey(self, zone_id, key_id, data):
        self._count("update_cryptokey")
        for k in self.keys:
            if k["id"] == key_id:
                k.update(data)

    async def delete_cryptokey(self, zone_id, key_id):
        self._count("delete_cryptokey")
        self.keys = [k for k in self.keys if k["id"] != key_id]


@pytest.fixture
def pdns(monkeypatch):
    fake = DnssecPDNS()
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    return fake


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(webhook_outbox, "enqueue_event", rec)
    detached = []

    async def fake_detached(*a, **k):
        detached.append((a, k))
        return None

    monkeypatch.setattr(audit_service, "write_audit_detached", fake_detached)
    rec.detached = detached
    return rec


def _admin_client(session=None):
    from app.routers import dnssec, records, zones

    admin = make_user()
    session = session or FakeSession(user_row=admin)
    jwt = create_access_token(data={"sub": str(admin.id)}, user=admin)
    client = TestClient(build_app(session, zones, records, dnssec), raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {jwt}"
    return client, session


def _audits(session):
    from app.models.models import AuditLog

    return [o for o in session.added if isinstance(o, AuditLog)]


def _with_www(pdns, monkeypatch, *values):
    """Primary liest das RRset seit F7-BE gefiltert (``get_zone_rrset``, Fan-out Betriebsart B)."""
    async def get_zone_rrset(zone_id, name, rtype, **kw):
        return {"name": zone_id, "rrsets": [{"name": "www.allowed.example.", "type": "A", "ttl": 300,
                                             "records": [{"content": v, "disabled": False} for v in values]}]}

    monkeypatch.setattr(pdns, "get_zone_rrset", get_zone_rrset, raising=False)


def test_record_endpoints_enqueue_after_audit(pdns, recorder, monkeypatch):
    _with_www(pdns, monkeypatch, "192.0.2.1")
    c, s = _admin_client()
    z = "allowed.example."
    rec = {"name": f"www.{z}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.1"}]}
    assert c.post(f"{A}/records/srv1/{z}", json=rec).status_code == 200
    assert c.put(f"{A}/records/srv1/{z}", json={"name": f"www.{z}", "type": "A", "ttl": 300,
                                                "old_content": "192.0.2.9", "new_content": "192.0.2.2"}).status_code == 404
    assert c.request("DELETE", f"{A}/records/srv1/{z}/delete",
                     json={"name": f"www.{z}", "type": "A", "content": "192.0.2.1"}).status_code == 200
    assert c.post(f"{A}/records/srv1/{z}/bulk", json={"create": [rec], "delete": []}).status_code == 200
    # PUT: Inhalt nicht vorhanden -> 404, kein Ereignis
    assert recorder.events() == ["record.created", "record.deleted", "record.bulk"]
    audits = _audits(s)
    assert [a.action for a in audits] == ["CREATE", "DELETE", "BULK_UPDATE"]
    for call, audit in zip(recorder.calls, audits):
        assert call["zone"] == z and call["server"] == "srv1" and call["actor"] == 1
        assert call["audit_log_id"] == audit.id and audit.id is not None
        assert audit.zone_name == z  # write_audit(zone_name=...) auch fuer Record-Namen
        assert audit.details["version"] == 2  # Audit v2 (F7-BE)
    v1 = ("server", "zone", "name", "type")
    assert {k: recorder.calls[0]["data"][k] for k in v1} == {"server": "srv1", "zone": z, "name": f"www.{z}",
                                                             "type": "A"}
    # F6 5.3 (F7-BE [D8]): changes/fanout zusaetzlich zu den v1-Feldern
    assert {"ttl", "added", "changes", "fanout"} <= set(recorder.calls[0]["data"])
    assert recorder.calls[1]["data"]["content"] == "192.0.2.1" and "changes" in recorder.calls[1]["data"]
    # record.bulk (F1 3.3 / F6 5.3): v1-Felder bleiben, dazu source/mode/changes/changes_total/fanout
    bulk_data = recorder.calls[2]["data"]
    assert {k: bulk_data[k] for k in ("server", "zone", "created", "deleted")} == {
        "server": "srv1", "zone": z, "created": 1, "deleted": 0}
    assert bulk_data["source"] == "api" and bulk_data["mode"] is None and bulk_data["changes_total"] == 1
    assert bulk_data["changes"][0]["name"] == f"www.{z}" and bulk_data["fanout"] == {"srv1": "saved"}
    assert [a[0][0] for a in recorder.detached] == ["UPDATE"]  # Fehler-Audit detached


def test_record_update_enqueues_updated(pdns, recorder, monkeypatch):
    _with_www(pdns, monkeypatch, "192.0.2.1")
    c, s = _admin_client()
    r = c.put(f"{A}/records/srv1/allowed.example.", json={"name": "www.allowed.example.", "type": "A", "ttl": 300,
                                                         "old_content": "192.0.2.1", "new_content": "192.0.2.2"})
    assert r.status_code == 200, r.text
    assert recorder.events() == ["record.updated"]
    assert recorder.calls[0]["audit_log_id"] == _audits(s)[0].id


def test_record_primary_error_enqueues_nothing(pdns, recorder, monkeypatch):
    async def fail(*a, **k):
        raise PowerDNSAPIError(422, "Record www.allowed.example./A: bad content", server="srv1")

    monkeypatch.setattr(pdns, "update_records", fail, raising=False)
    c, s = _admin_client()
    r = c.post(f"{A}/records/srv1/allowed.example.", json={"name": "www.allowed.example.", "type": "A",
                                                          "records": [{"content": "192.0.2.1"}]})
    assert r.status_code == 422
    assert recorder.calls == [] and _audits(s) == []
    assert recorder.detached and recorder.detached[0][1]["zone_name"] == "allowed.example."


def _import_session():
    """Fake-Session, in der srv1 als schreibbarer Server konfiguriert ist (Import ermittelt die Ziele per DB)."""
    from app.models.models import ServerConfig

    return FakeSession(user_row=make_user(), extra={ServerConfig: [("srv1",)]})


def test_zone_create_update_import_events(pdns, recorder):
    c, s = _admin_client(_import_session())
    r = c.post(f"{A}/zones", json={"name": "neu.example", "nameservers": ["ns1.example.com"], "servers": ["srv1"]})
    assert r.status_code == 200 and r.json()["details"] == {"srv1": "created"}
    r = c.put(f"{A}/zones/srv1/neu.example.", json={"soa_edit_api": "INCREASE"})
    assert r.status_code == 200
    r = c.post(f"{A}/zones/import", json={"name": "imp.example", "content": "imp.example. 3600 IN A 192.0.2.1"})
    assert r.status_code == 200
    assert recorder.events() == ["zone.created", "zone.updated", "zone.imported"]
    created, updated, imported = recorder.calls
    assert created["zone"] == "neu.example." and created["server"] is None
    assert created["data"] == {"zone": "neu.example.", "kind": "Native", "nameservers": ["ns1.example.com."],
                               "dnssec": False, "results": {"srv1": "created"}}
    assert updated["data"] == {"zone": "neu.example.", "server": "srv1", "changed": {"soa_edit_api": "INCREASE"}}
    assert imported["data"]["results"] == {"srv1": "imported"} and imported["data"]["content_length"] > 0
    audits = _audits(s)
    assert [a.action for a in audits] == ["CREATE", "UPDATE", "IMPORT"]
    assert [c_["audit_log_id"] for c_ in recorder.calls] == [a.id for a in audits]
    assert [a.zone_name for a in audits] == ["neu.example.", "neu.example.", "imp.example."]


def test_zone_import_without_success_sends_nothing(pdns, recorder, monkeypatch):
    async def fail(payload):
        raise PowerDNSAPIError(422, "kaputt", server="srv1")

    monkeypatch.setattr(pdns, "create_zone", fail, raising=False)
    c, _ = _admin_client(_import_session())
    r = c.post(f"{A}/zones/import", json={"name": "imp.example", "content": "x"})
    assert r.status_code == 200 and r.json()["details"]["srv1"].startswith("error")
    assert recorder.calls == []


def test_zone_delete_enqueues_before_acl_cleanup_and_prunes_token_scopes(pdns, recorder):
    from app.models.models import PanelToken

    token = PanelToken(id=9, user_id=1, name="t", token_prefix="p", token_hash="h", is_active=True,
                       revoked_at=None, scope_zones=["gone.example.", "keep.example."], permission="manage",
                       allow_admin=False)
    session = FakeSession(user_row=make_user(), token_row=token)
    c, s = _admin_client(session)
    r = c.delete(f"{A}/zones/srv1/gone.example.")
    assert r.status_code == 200, r.text
    assert recorder.events() == ["zone.deleted"]
    call = recorder.calls[0]
    assert call["data"] == {"zone": "gone.example.", "server": "srv1", "zone_still_on_other_server": False}
    assert call["audit_log_id"] is None
    executed = [str(x) for x in s.executed]
    acl_delete = next(i for i, x in enumerate(executed) if x.startswith("DELETE FROM user_zone_access"))
    assert call["executed_before"] <= acl_delete  # Ereignis VOR dem Entfernen der Zonenrechte
    assert token.scope_zones == ["keep.example."]
    audit = _audits(s)[-1]
    assert audit.action == "DELETE" and audit.details["pruned_panel_token_scopes"] == 1
    assert audit.zone_name == "gone.example."


def test_zone_delete_keeps_acl_when_zone_on_other_server(pdns, recorder, monkeypatch):
    other = FakePDNS("srv2")
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": pdns, "srv2": other})
    c, s = _admin_client()
    assert c.delete(f"{A}/zones/srv1/gone.example.").status_code == 200
    assert recorder.calls[0]["data"]["zone_still_on_other_server"] is True
    assert not any(str(x).startswith("DELETE FROM user_zone_access") for x in s.executed)
    assert _audits(s)[-1].details["pruned_panel_token_scopes"] == 0


def test_dnssec_endpoints_enqueue(pdns, recorder):
    c, s = _admin_client()
    z = "allowed.example."
    base = f"{A}/dnssec/srv1/{z}"
    assert c.post(f"{base}/enable", json={}).status_code == 200                      # CSK 1 aktiv
    assert c.post(f"{base}/enable", json={}).status_code == 200                      # idempotent: kein Ereignis
    assert c.post(f"{base}/keys", json={"keytype": "csk"}).status_code == 200        # CSK 2 inaktiv
    assert c.post(f"{base}/keys/2/activate").status_code == 200
    assert c.post(f"{base}/keys/1/deactivate").status_code == 200
    assert c.post(f"{base}/keys/2/deactivate").status_code == 409                    # Schutzregel: kein Ereignis
    assert c.delete(f"{base}/keys/1").status_code == 200
    assert c.post(f"{base}/disable").status_code == 200
    assert recorder.events() == ["dnssec.enabled", "dnssec.key_created", "dnssec.key_activated",
                                 "dnssec.key_deactivated", "dnssec.key_deleted", "dnssec.disabled"]
    enabled = recorder.calls[0]["data"]
    assert enabled["algorithm"] == "ECDSAP256SHA256" and enabled["nsec3param"] == "1 0 0 -"
    assert enabled["keys"][0]["id"] == 1 and enabled["keys"][0]["ds"] == ["ds-line"]
    assert "GEHEIM" not in repr(recorder.calls)
    assert recorder.calls[2]["data"]["key_id"] == 2 and recorder.calls[2]["data"]["zone"] == z
    assert all(c_["zone"] == z and c_["server"] == "srv1" and c_["actor"] == 1 for c_ in recorder.calls)
    audits = _audits(s)
    assert [a.action for a in audits] == ["DNSSEC_ENABLE", "KEY_CREATE", "KEY_ACTIVATE", "KEY_DEACTIVATE",
                                         "KEY_DELETE", "DNSSEC_DISABLE"]
    assert all(a.zone_name == z for a in audits)
    assert [c_["audit_log_id"] for c_ in recorder.calls] == [a.id for a in audits]
