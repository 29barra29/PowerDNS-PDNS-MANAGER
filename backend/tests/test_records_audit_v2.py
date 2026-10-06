"""Record-Endpunkte: Audit v2, Fan-out Betriebsart B und Webhook-Daten (F7 9.2 Nr. 12–18, [D1], [D3], [D8]).

Die Handler aus ``routers/records.py`` laufen direkt gegen zwei In-Memory-PowerDNS-Server mit getrennten
Zonenstaenden (``tests/fakes/pdns.py``). ``write_audit`` und ``enqueue_event`` werden aufgezeichnet; der
Outbox-Test (F6 9.1 Nr. 12) nutzt das echte ``enqueue_event`` mit einer Fake-Session.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from fakes.pdns import FakeDB, FakeResult, fake_db, fake_pdns, make_zone, rr  # noqa: F401 - Fixtures
from app.models.models import User, Webhook
from app.routers import records
from app.schemas.dns import BulkRecordUpdate, RecordCreate, RecordDelete, RecordUpdate
from app.services import bulk as bulk_service
from app.services import fanout, webhook_outbox
from app.services.pdns_client import PowerDNSAPIError

Z = "example.com."
WWW = "www.example.com."


@pytest.fixture
def admin():
    return User(id=1, username="admin", role="admin", is_active=True, hashed_password="x")


class AuditRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, action, resource_type, resource_name=None, **kw):
        entry = {"action": action, "resource_type": resource_type, "resource_name": resource_name, **kw}
        self.calls.append(entry)
        if kw.get("status", "success") != "success":
            return None
        return SimpleNamespace(id=100 + len(self.calls))

    @property
    def last(self):
        return self.calls[-1]


class EventRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, event, *, actor, data, zone=None, server=None, audit_log_id=None, actor_via=None):
        self.calls.append({"event": event, "data": data, "zone": zone, "server": server, "audit_log_id": audit_log_id})
        return 1


@pytest.fixture
def audit(monkeypatch):
    rec = AuditRecorder()
    monkeypatch.setattr(records, "write_audit", rec)
    monkeypatch.setattr(bulk_service, "write_audit", rec)  # Bulk schreibt seit F1 in services/bulk.py
    return rec


@pytest.fixture
def events(monkeypatch):
    rec = EventRecorder()
    monkeypatch.setattr(webhook_outbox, "enqueue_event", rec)
    return rec


@pytest.fixture
def two(fake_pdns, monkeypatch):
    """ns1 (Primary) und ns2 mit getrenntem Stand; ns2 hat einen eigenen Zusatzwert [D1]."""
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)
    fake_pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", ttl=3600,
                                            comments=[{"content": "Webserver", "account": "ops", "modified_at": 1}])]))
    fake_pdns.ns2.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", "192.0.2.99", ttl=3600)]))
    return fake_pdns


def _create(values, ttl=300, name=WWW, rtype="A"):
    return RecordCreate(name=name, type=rtype, ttl=ttl, records=[{"content": v} for v in values])


# --- Nr. 12: Create auf bestehendes RRset (Merge) ------------------------------------------------------------
async def test_create_merge_audit_v2(two, fake_db, admin, audit, events):
    res = await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert res.details == {"ns1": "saved", "ns2": "saved"}
    a = audit.last
    assert a["action"] == "CREATE" and a["status"] == "success" and a["zone_name"] == Z and a["server_name"] == "ns1"
    d = a["details"]
    assert d["version"] == 2 and d["zone"] == Z and d["after_source"] == "reread" and d["primary_outcome"] == "ok"
    (ch,) = d["changes"]
    assert ch["name"] == WWW and ch["type"] == "A"
    assert ch["before"]["ttl"] == 3600 and [r["content"] for r in ch["before"]["records"]] == ["192.0.2.1"]
    assert ch["after"]["ttl"] == 300 and [r["content"] for r in ch["after"]["records"]] == ["192.0.2.1", "192.0.2.2"]
    assert ch["after"]["comments"] == ch["before"]["comments"] != []  # PowerDNS behaelt Kommentare
    assert d["type"] == "A" and d["ttl"] == 300 and d["records"] == ["192.0.2.1", "192.0.2.2"]  # Legacy v1
    assert d["fanout"] == {"ns1": "saved", "ns2": "saved"}
    # Webhook-Daten F6 5.3 (+ v1-Felder)
    (ev,) = events.calls
    assert ev["event"] == "record.created" and ev["audit_log_id"] == 101 and ev["zone"] == Z
    data = ev["data"]
    assert {k: data[k] for k in ("server", "zone", "name", "type")} == {"server": "ns1", "zone": Z, "name": WWW,
                                                                         "type": "A"}
    assert data["ttl"] == 300 and data["added"] == ["192.0.2.2"] and data["fanout"] == res.details
    assert data["changes"][0]["after"] == {"ttl": 300, "records": [{"content": "192.0.2.1", "disabled": False},
                                                                   {"content": "192.0.2.2", "disabled": False}]}


# --- Nr. 13: Update aendert TTL und Wert ------------------------------------------------------------------
async def test_update_ttl_and_value(two, fake_db, admin, audit, events):
    upd = RecordUpdate(name=WWW, type="A", ttl=60, old_content="192.0.2.1", new_content="192.0.2.5")
    res = await records.update_record("ns1", Z, upd, fake_db, admin)
    assert res.details == {"ns1": "saved", "ns2": "saved"}
    d = audit.last["details"]
    (ch,) = d["changes"]
    assert ch["before"]["ttl"] == 3600 and [r["content"] for r in ch["before"]["records"]] == ["192.0.2.1"]
    assert ch["after"]["ttl"] == 60 and [r["content"] for r in ch["after"]["records"]] == ["192.0.2.5"]
    assert d["old"] == "192.0.2.1" and d["new"] == "192.0.2.5" and d["type"] == "A"
    data = events.calls[0]["data"]
    assert events.calls[0]["event"] == "record.updated"
    assert data["old_content"] == "192.0.2.1" and data["new_content"] == "192.0.2.5" and data["ttl"] == 60
    assert data["changes"][0]["before"]["ttl"] == 3600


async def test_update_missing_value_is_404_without_peer_write(two, fake_db, admin, audit, events):
    upd = RecordUpdate(name=WWW, type="A", ttl=60, old_content="192.0.2.77", new_content="192.0.2.5")
    with pytest.raises(HTTPException) as ei:
        await records.update_record("ns1", Z, upd, fake_db, admin)
    assert ei.value.status_code == 404 and "Original record content not found" in ei.value.detail
    assert two.ns1.patches == [] and two.ns2.patches == []
    assert audit.last["status"] == "error" and audit.last["details"]["changes"] == [] and events.calls == []


# --- Nr. 14: Delete Einzelwert / ganzes RRset ----------------------------------------------------------------
async def test_delete_single_value_after_is_rest(two, fake_db, admin, audit, events):
    two.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", "192.0.2.2")]))
    res = await records.delete_record("ns1", Z, RecordDelete(name=WWW, type="A", content="192.0.2.1"), fake_db, admin)
    assert res.details == {"ns1": "deleted", "ns2": "deleted"}
    (ch,) = audit.last["details"]["changes"]
    assert [r["content"] for r in ch["after"]["records"]] == ["192.0.2.2"]
    assert audit.last["details"]["content"] == "192.0.2.1"
    assert events.calls[0]["event"] == "record.deleted" and events.calls[0]["data"]["content"] == "192.0.2.1"


async def test_delete_rrset_reads_before_and_after_is_none(two, fake_db, admin, audit, events):
    await records.delete_record("ns1", Z, RecordDelete(name=WWW, type="A"), fake_db, admin)
    gets_before_patch = [c for c in two.ns1.calls if c[0] == "GET"]
    assert gets_before_patch and two.ns1.calls.index(gets_before_patch[0]) < [c[0] for c in two.ns1.calls].index("PATCH")
    (ch,) = audit.last["details"]["changes"]
    assert ch["after"] is None and [r["content"] for r in ch["before"]["records"]] == ["192.0.2.1"]
    assert ch["before"]["comments"]  # Vorher-Zustand inkl. Kommentare (F11: PTR-Pflege beim Loeschen)
    assert audit.last["details"]["content"] is None
    assert two.ns2.rrset(Z, WWW, "A") is None


async def test_delete_missing_value_is_404(two, fake_db, admin, audit, events):
    with pytest.raises(HTTPException) as ei:
        await records.delete_record("ns1", Z, RecordDelete(name=WWW, type="A", content="192.0.2.50"), fake_db, admin)
    assert ei.value.status_code == 404 and ei.value.detail.startswith("Wert '192.0.2.50' nicht im RRset")
    with pytest.raises(HTTPException) as ei:
        await records.delete_record("ns1", Z, RecordDelete(name="nix.example.com.", type="A", content="x"),
                                    fake_db, admin)
    assert ei.value.status_code == 404 and "nicht vorhanden" in ei.value.detail
    assert two.ns1.patches == [] and two.ns2.patches == [] and events.calls == []


# --- Nr. 15: Primary-PATCH 422 -----------------------------------------------------------------------------
async def test_primary_patch_error(two, fake_db, admin, audit, events):
    two.ns1.fail_on_patch = PowerDNSAPIError(422, '{"error": "RRset www.example.com. IN A: bad content"}', "ns1")
    with pytest.raises(HTTPException) as ei:
        await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert ei.value.status_code == 422
    a = audit.last
    assert a["status"] == "error" and a["details"]["changes"] == [] and a["details"]["after_source"] is None
    assert a["details"]["primary_outcome"] == "failed" and "bad content" in a["error_message"]
    assert two.ns2.patches == []  # f52: kein Peer-Write ohne Primary
    assert events.calls == []


# --- Nr. 16: Re-Read scheitert -> berechnet -------------------------------------------------------------------
async def test_reread_failure_falls_back_to_computed(two, fake_db, admin, audit, events, monkeypatch):
    async def broken(*a, **k):
        raise PowerDNSAPIError(503, "Cannot connect", "ns1")

    monkeypatch.setattr(two.ns1, "get_rrsets", broken)
    await records.create_record("ns1", Z, _create(["192.0.2.2"], ttl=120), fake_db, admin)
    d = audit.last["details"]
    assert audit.last["status"] == "success" and d["after_source"] == "computed"
    (ch,) = d["changes"]
    assert ch["after"]["ttl"] == 120 and [r["content"] for r in ch["after"]["records"]] == ["192.0.2.1", "192.0.2.2"]
    assert ch["after"]["comments"] == ch["before"]["comments"]


# --- Nr. 17: Peer ohne Zone ------------------------------------------------------------------------------------
async def test_peer_without_zone_skipped(fake_pdns, fake_db, admin, audit, events):
    fake_pdns.ns1.add_zone(make_zone(Z, []))
    res = await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert res.details == {"ns1": "saved", "ns2": "skipped (zone not present)"}
    assert audit.last["status"] == "success"
    assert audit.last["details"]["fanout"]["ns2"] == "skipped (zone not present)"
    (ch,) = audit.last["details"]["changes"]
    assert ch["before"] is None and ch["after"]["records"] == [{"content": "192.0.2.2", "disabled": False}]


async def test_primary_without_zone_is_404(fake_pdns, fake_db, admin, audit, events):
    fake_pdns.ns2.add_zone(make_zone(Z, []))
    with pytest.raises(HTTPException) as ei:
        await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert ei.value.status_code == 404 and "existiert auf Server 'ns1' nicht" in ei.value.detail
    assert fake_pdns.ns2.patches == []


# --- Nr. 18: Bulk (seit F1 services/bulk.py; ausfuehrlich in test_bulk_endpoint.py) ------------------------------
async def test_bulk_single_audit_with_all_changes(two, fake_db, admin, audit, events):
    two.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", "192.0.2.2"), rr("old.example.com.", "TXT", '"x"'),
                                   rr("mail.example.com.", "MX", "10 mx1.example.com.", "20 mx2.example.com.")]))
    bulk = BulkRecordUpdate(
        create=[{"name": "new.example.com.", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.10"}]}],
        delete=[{"name": "old.example.com.", "type": "TXT"},
                {"name": "mail.example.com.", "type": "MX", "content": "20 mx2.example.com."}],
    )
    res = await records.bulk_update_records("ns1", Z, bulk, fake_db, admin)
    assert res.details["fanout"]["ns1"] == "saved"
    assert len(audit.calls) == 1
    a = audit.last
    assert a["action"] == "BULK_UPDATE" and a["resource_name"] == Z and a["zone_name"] == Z
    d = a["details"]
    assert d["version"] == 2 and d["created"] == 1 and d["deleted"] == 2 and d["source"] == "api"
    by_key = {(c["name"], c["type"]): c for c in d["changes"]}
    assert set(by_key) == {("new.example.com.", "A"), ("old.example.com.", "TXT"), ("mail.example.com.", "MX")}
    assert by_key[("new.example.com.", "A")]["before"] is None
    assert by_key[("old.example.com.", "TXT")]["after"] is None
    assert [r["content"] for r in by_key[("mail.example.com.", "MX")]["after"]["records"]] == ["10 mx1.example.com."]
    assert len(two.ns1.patches) == 1  # ein atomarer PATCH je Server
    assert events.calls[0]["event"] == "record.bulk"
    data = events.calls[0]["data"]
    assert {k: data[k] for k in ("server", "zone", "created", "deleted")} == {"server": "ns1", "zone": Z,
                                                                              "created": 1, "deleted": 2}
    assert data["changes_total"] == 3 and data["source"] == "api" and data["fanout"] == res.details["fanout"]


async def test_bulk_missing_value_on_primary_is_404(two, fake_db, admin, audit, events):
    bulk = BulkRecordUpdate(delete=[{"name": WWW, "type": "A", "content": "192.0.2.42"}])
    with pytest.raises(HTTPException) as ei:
        await records.bulk_update_records("ns1", Z, bulk, fake_db, admin)
    assert ei.value.status_code == 404
    assert ei.value.detail["issues"][0]["code"] == "value_missing"
    assert ei.value.detail["issues"][0]["params"]["content"] == "192.0.2.42"
    assert two.ns1.patches == [] and two.ns2.patches == []


async def test_bulk_value_delete_and_replace_same_rrset_single_entry(two, fake_db, admin, audit, events):
    # REPLACE und Wert-Loeschung desselben RRsets sind seit F1 widerspruechlich (Schema, F1 3.1 Regel 4) ...
    with pytest.raises(ValidationError, match="widersprüchliche Operationen"):
        BulkRecordUpdate(create=[{"name": WWW, "type": "A", "ttl": 60, "records": [{"content": "192.0.2.8"}]}],
                         delete=[{"name": WWW, "type": "A", "content": "192.0.2.1"}])
    # ... relative Ops auf demselben RRset ergeben weiterhin genau einen PATCH-Eintrag
    bulk = BulkRecordUpdate(merge=[{"name": WWW, "type": "A", "ttl": 60, "records": [{"content": "192.0.2.8"}]}],
                            delete=[{"name": WWW, "type": "A", "content": "192.0.2.1"}])
    await records.bulk_update_records("ns1", Z, bulk, fake_db, admin)
    (patch,) = two.ns1.patches
    assert [(p["name"], p["changetype"]) for p in patch] == [(WWW, "REPLACE")]
    assert two.ns1.values(Z, WWW, "A") == ["192.0.2.8"]


# --- [D1] Peer-Erhalt -------------------------------------------------------------------------------------------
async def test_peer_extra_value_survives_create_update_delete(two, fake_db, admin, audit, events):
    await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert two.ns2.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2", "192.0.2.99"]
    await records.update_record("ns1", Z, RecordUpdate(name=WWW, type="A", ttl=300, old_content="192.0.2.1",
                                                       new_content="192.0.2.3"), fake_db, admin)
    assert two.ns2.values(Z, WWW, "A") == ["192.0.2.2", "192.0.2.3", "192.0.2.99"]
    await records.delete_record("ns1", Z, RecordDelete(name=WWW, type="A", content="192.0.2.3"), fake_db, admin)
    assert two.ns1.values(Z, WWW, "A") == ["192.0.2.2"]
    assert two.ns2.values(Z, WWW, "A") == ["192.0.2.2", "192.0.2.99"]
    # Audit-Historie stammt nur vom Primary
    assert all("192.0.2.99" not in json.dumps(c["details"]["changes"]) for c in audit.calls)


async def test_update_missing_on_peer_is_skipped(two, fake_db, admin, audit, events):
    two.ns2.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.99")]))
    res = await records.update_record("ns1", Z, RecordUpdate(name=WWW, type="A", ttl=300, old_content="192.0.2.1",
                                                             new_content="192.0.2.3"), fake_db, admin)
    assert res.details == {"ns1": "saved", "ns2": "skipped (no matching content)"}
    assert two.ns2.values(Z, WWW, "A") == ["192.0.2.99"]


# --- [D3] Primary-Timeout ---------------------------------------------------------------------------------------
async def test_primary_timeout_verified_after_timeout(two, fake_db, admin, audit, events):
    two.ns1.timeout_after_patch = True
    res = await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert res.details == {"ns1": "saved", "ns2": "saved"}
    a = audit.last
    assert a["status"] == "success" and a["details"]["primary_outcome"] == "verified_after_timeout"
    assert a["details"]["after_source"] == "reread"
    assert [r["content"] for r in a["details"]["changes"][0]["after"]["records"]] == ["192.0.2.1", "192.0.2.2"]
    assert events.calls and events.calls[0]["event"] == "record.created"


async def test_primary_timeout_not_applied_is_failed(two, fake_db, admin, audit, events):
    two.ns1.timeout_before_patch = True
    with pytest.raises(HTTPException) as ei:
        await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert ei.value.status_code == 504
    assert audit.last["status"] == "error" and audit.last["details"]["primary_outcome"] == "failed"
    assert two.ns2.patches == [] and events.calls == []


async def test_primary_timeout_unknown_records_actual_state(two, fake_db, admin, audit, events, monkeypatch):
    orig = two.ns1._patch

    def patch_then_unreadable(zid, zone, rrsets):
        two.ns1.timeout_after_patch = True
        two.ns1.fail_reads = fanout.REREAD_ATTEMPTS  # Nachpruefung scheitert komplett
        return orig(zid, zone, rrsets)

    monkeypatch.setattr(two.ns1, "_patch", patch_then_unreadable)
    with pytest.raises(HTTPException) as ei:
        await records.create_record("ns1", Z, _create(["192.0.2.2"]), fake_db, admin)
    assert ei.value.status_code == 504
    a = audit.last
    d = a["details"]
    assert a["status"] == "error" and d["primary_outcome"] == "unknown" and d["history_incomplete"] is True
    assert d["after_source"] == "reread" and d["fanout"]["ns1"].startswith("error: unklar")
    assert [r["content"] for r in d["changes"][0]["after"]["records"]] == ["192.0.2.1", "192.0.2.2"]
    assert two.ns2.patches == [] and events.calls == []


# --- [D8] F6 9.1 Nr. 12: Outbox-Body v2 -------------------------------------------------------------------------
class OutboxDB(FakeDB):
    """Fake-Session fuer das echte ``enqueue_event``: ein Webhook des Akteurs, Zeilen werden gesammelt."""

    def __init__(self, hook):
        super().__init__(execute_handler=self._handle)
        self.hook = hook

    def _handle(self, stmt):
        desc = getattr(stmt, "column_descriptions", None) or []
        if desc and desc[0].get("entity") is Webhook:
            return FakeResult([(self.hook, "admin")])
        return FakeResult([])

    def add_all(self, objs):
        self.added.extend(objs)


def _hook():
    return SimpleNamespace(id=5, user_id=1, name="h", secret="geheim-1234567890", url="https://hooks.example/x",
                           events=["record"], scope="own", is_active=True)


async def test_record_create_enqueues_v2(fake_pdns, admin, monkeypatch):
    from app.models.models import WebhookDelivery

    async def fake_audit(db, *a, **k):
        return SimpleNamespace(id=4711) if k.get("status", "success") == "success" else None

    monkeypatch.setattr(records, "write_audit", fake_audit)
    fake_pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", ttl=3600)]))  # ns2 ohne Zone -> skipped
    db = OutboxDB(_hook())
    await records.create_record("ns1", Z, _create(["192.0.2.2"], ttl=300), db, admin)
    deliveries = [o for o in db.added if isinstance(o, WebhookDelivery)]
    assert len(deliveries) == 1
    row = deliveries[0]
    assert row.event == "record.created" and row.status == "queued" and row.audit_log_id == 4711
    body = json.loads(row.body)
    assert body["v"] == 2 and body["zone"] == Z and body["server"] == "ns1"
    data = body["data"]
    assert data["changes"][0]["before"] == {"ttl": 3600, "records": [{"content": "192.0.2.1", "disabled": False}]}
    assert data["changes"][0]["after"]["ttl"] == 300
    assert data["fanout"] == {"ns1": "saved", "ns2": "skipped (zone not present)"}
    assert data["added"] == ["192.0.2.2"] and data["name"] == WWW

    # PowerDNS-Fehler am Primary -> keine Zustellung
    fake_pdns.ns1.fail_on_patch = PowerDNSAPIError(422, "kaputt", "ns1")
    db2 = OutboxDB(_hook())
    monkeypatch.setattr("app.services.audit.write_audit_detached", _noop_detached)
    with pytest.raises(HTTPException):
        await records.create_record("ns1", Z, _create(["192.0.2.3"]), db2, admin)
    assert not [o for o in db2.added if isinstance(o, WebhookDelivery)]


async def _noop_detached(*a, **k):
    return None
