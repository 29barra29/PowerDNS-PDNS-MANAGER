"""Bulk-Endpunkte (F1 9.4): ``POST …/bulk/preview`` und der gehaertete ``POST …/bulk``.

Die Handler laufen gegen zwei In-Memory-PowerDNS-Server mit getrennten Zonenstaenden (``tests/fakes/pdns.py``).
``write_audit`` (in ``services/bulk.py``) und ``enqueue_event`` werden aufgezeichnet. Routing/ACL/Schema laufen
ueber eine Test-App mit Fake-Session (``authfakes``). Der Transportfehler-Test (Nr. 13) liegt seit Welle 0 in
``tests/test_fanout.py::test_pdns_client_transport_error``.

Tests mit ``wave_integration`` brauchen ``services/ptr.py`` (WS-F9F11-BE, parallel in Welle 2).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from fakes.pdns import FakeDB, FakeResult, fake_db, fake_pdns, make_zone, rr, server_config  # noqa: F401
from app.core.auth import create_access_token
from app.models.models import SystemSetting, User
from app.routers import records
from app.schemas.bulk import BulkPreviewRequest, BulkRecordUpdate
from app.services import bulk as bulk_service
from app.services import fanout, webhook_outbox
from app.services.lua_records import MSG_DENIED_ADMIN
from app.services.pdns_client import PowerDNSAPIError
from app.services.rrsets import snapshot_fingerprint

Z = "example.com."
WWW = "www.example.com."
NS = rr(Z, "NS", "ns1.example.com.", "ns2.example.com.")


@pytest.fixture
def admin():
    return User(id=1, username="admin", role="admin", is_active=True, hashed_password="x")


class AuditRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, action, resource_type, resource_name=None, **kw):
        self.calls.append({"action": action, "resource_type": resource_type, "resource_name": resource_name, **kw})
        if kw.get("status", "success") != "success":
            return None
        return SimpleNamespace(id=4700 + len(self.calls))

    @property
    def last(self):
        return self.calls[-1]


class EventRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, event, *, actor, data, zone=None, server=None, audit_log_id=None, actor_via=None):
        self.calls.append({"event": event, "data": data, "zone": zone, "server": server,
                           "audit_log_id": audit_log_id, "actor": getattr(actor, "id", None)})
        return 1


@pytest.fixture
def audit(monkeypatch):
    rec = AuditRecorder()
    monkeypatch.setattr(bulk_service, "write_audit", rec)
    monkeypatch.setattr(records, "write_audit", rec)
    return rec


@pytest.fixture
def events(monkeypatch):
    rec = EventRecorder()
    monkeypatch.setattr(webhook_outbox, "enqueue_event", rec)
    return rec


@pytest.fixture
def two(fake_pdns, monkeypatch):
    """ns1 (Primary) und ns2 mit getrenntem Stand; ns2 fehlt der Wert 192.0.2.2 und hat einen eigenen Zusatzwert."""
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)
    fake_pdns.ns1.add_zone(make_zone(Z, [NS, rr(WWW, "A", "192.0.2.1", "192.0.2.2", ttl=3600),
                                         rr("old.example.com.", "TXT", '"alt"'),
                                         rr("mail.example.com.", "MX", "10 mx1.example.com.", "20 mx2.example.com.")]))
    fake_pdns.ns2.add_zone(make_zone(Z, [NS, rr(WWW, "A", "192.0.2.1", "192.0.2.99", ttl=3600),
                                         rr("old.example.com.", "TXT", '"alt"'),
                                         rr("mail.example.com.", "MX", "10 mx1.example.com.", "20 mx2.example.com.")]))
    return fake_pdns


async def preview(server, body, db, user):
    return await records.preview_bulk_records(server, Z, BulkPreviewRequest(**body), db, user)


async def apply(server, body, db, user):
    return await records.bulk_update_records(server, Z, BulkRecordUpdate(**body), db, user)


def _patch_count(srv):
    return srv.count("PATCH")


# --- Nr. 1: Routenreihenfolge ------------------------------------------------------------------------------------
def test_bulk_preview_route_not_shadowed(two, audit, events):
    user = make_user()
    session = FakeSession(user_row=user)
    client = TestClient(build_app(session, records), raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    r = client.post(f"/api/v1/records/ns1/{Z}/bulk/preview",
                    json={"ops": {"source": "selection", "delete": [{"name": "old.example.com.", "type": "TXT"}]}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changes"][0]["name"] == "old.example.com." and body["changes"][0]["op"] == "delete"
    assert body["ops"]["expected"][0]["fingerprint"] != "absent"
    assert body["peers"] == ["ns2"] and body["blocking"] is False
    # kein Schreiben, kein Audit, kein Webhook
    assert two.ns1.patches == [] and two.ns2.patches == [] and audit.calls == [] and events.calls == []
    # /bulk ist weiterhin erreichbar (gleiche App) und uebernimmt den Body der Vorschau unveraendert
    r2 = client.post(f"/api/v1/records/ns1/{Z}/bulk", json=body["ops"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["details"]["changed_rrsets"] == 1


# --- Nr. 2: Vorschau == Anwendung, ein PATCH je Server ------------------------------------------------------------
async def test_preview_ops_apply_identical_changes(two, fake_db, admin, audit, events):
    p = await preview("ns1", {"ops": {"source": "selection", "set_ttl": [{"name": WWW, "type": "A", "ttl": 300}],
                                      "delete": [{"name": "mail.example.com.", "type": "MX",
                                                  "content": "20 mx2.example.com."}]}}, fake_db, admin)
    assert p["blocking"] is False and len(p["changes"]) == 2
    res = await records.bulk_update_records("ns1", Z, p["ops"], fake_db, admin)
    assert res.details["changed_rrsets"] == 2 and res.details["audit_id"] == 4701
    d = audit.last["details"]
    preview_changes = [{"name": c["name"], "type": c["type"], "op": c["op"], "before": c["before"],
                        "after": c["after"]} for c in p["changes"]]
    assert d["changes"] == preview_changes
    assert _patch_count(two.ns1) == 1 and _patch_count(two.ns2) == 1
    assert two.ns2.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.99"]  # Peer-Wert bleibt [D1]
    assert two.ns2.rrset(Z, WWW, "A")["ttl"] == 300


# --- Nr. 3: Zone fehlt auf dem Primary ----------------------------------------------------------------------------
async def test_primary_zone_missing_is_404_without_peer_writes(fake_pdns, fake_db, admin, audit, events):
    fake_pdns.ns2.add_zone(make_zone(Z, [NS]))
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"delete": [{"name": "x.example.com.", "type": "A"}]}, fake_db, admin)
    assert ei.value.status_code == 404 and "es wurde nichts geschrieben" in ei.value.detail
    assert fake_pdns.ns2.calls == []
    assert audit.last["status"] == "error" and audit.last["details"]["applied"] is False
    assert events.calls == []


# --- Nr. 4: PATCH am Primary scheitert ----------------------------------------------------------------------------
async def test_primary_patch_error_no_peer_write_error_audit(two, fake_db, admin, audit, events):
    two.ns1.fail_on_patch = PowerDNSAPIError(422, json.dumps({"error": "RRset kaputt"}), "ns1")
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}]}, fake_db, admin)
    assert ei.value.status_code == 422
    assert two.ns2.patches == [] and two.ns2.count("GET") == 0
    a = audit.last
    assert a["status"] == "error" and a["details"]["applied"] is False and a["zone_name"] == Z
    assert a["details"]["changes"][0]["name"] == WWW  # geplante Aenderungen
    assert a["details"]["fanout"]["ns1"].startswith("error:")
    assert events.calls == []


# --- Nr. 5: Peer fehlt ein zu loeschender Wert --------------------------------------------------------------------
async def test_peer_missing_value_is_drift_not_error(two, fake_db, admin, audit, events):
    res = await apply("ns1", {"delete": [{"name": WWW, "type": "A", "content": "192.0.2.2"}]}, fake_db, admin)
    assert res.details["fanout"] == {"ns1": "saved", "ns2": "skipped (no changes needed)"}
    assert res.details["peer_drift"] == {"ns2": 1}
    assert audit.last["details"]["peer_drift"] == {"ns2": 1}
    assert two.ns2.patches == []


# --- Nr. 6: Transportfehler am Peer -------------------------------------------------------------------------------
async def test_peer_transport_error_continues(two, fake_db, admin, audit, events):
    two.ns2.fail_on_get = PowerDNSAPIError(502, "Verbindungsfehler zu PowerDNS-Server 'ns2' (ReadError) – Ergebnis "
                                                "unklar, bitte Zone neu laden.", "ns2", transport_error=True)
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}]}, fake_db, admin)
    assert res.details["fanout"]["ns1"] == "saved"
    assert res.details["fanout"]["ns2"].startswith("error: Verbindungsfehler")
    assert audit.last.get("status", "success") == "success"
    assert audit.last["details"]["fanout"]["ns2"].startswith("error:")


# --- Nr. 7: gemeinsame Datenbank ----------------------------------------------------------------------------------
async def test_shared_backend_peer_skips(two, fake_db, admin, audit, events):
    two.ns2.zones = two.ns1.zones  # zwei API-Server auf einer Datenbank
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}]}, fake_db, admin)
    assert res.details["fanout"] == {"ns1": "saved", "ns2": "skipped (no changes needed)"}
    assert len(two.ns1.patches) == 1 and two.ns2.patches == []


# --- Nr. 8: expected / force --------------------------------------------------------------------------------------
async def test_expected_conflict_409_and_force(two, fake_db, admin, audit, events):
    stale = snapshot_fingerprint({"ttl": 3600, "records": [{"content": "192.0.2.1", "disabled": False}]}, "A", Z)
    body = {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}],
            "expected": [{"name": WWW, "type": "A", "fingerprint": stale}]}
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", body, fake_db, admin)
    assert ei.value.status_code == 409
    assert ei.value.detail["conflicts"] == [{"name": WWW, "type": "A"}]
    assert "seit der Vorschau geändert" in ei.value.detail["message"]
    assert two.ns1.patches == [] and two.ns2.patches == []
    assert audit.last["status"] == "error" and audit.last["error_message"] == "Konflikt mit aktuellem Stand"
    assert events.calls == []
    res = await apply("ns1", {**body, "force": True}, fake_db, admin)
    assert res.details["fanout"]["ns1"] == "saved" and audit.last["details"]["forced"] is True
    # passendes expected -> kein Konflikt, forced bleibt False
    ok_fp = snapshot_fingerprint(two.ns1.rrset(Z, WWW, "A"), "A", Z)
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 600}],
                              "expected": [{"name": WWW, "type": "A", "fingerprint": ok_fp}]}, fake_db, admin)
    assert res.details["changed_rrsets"] == 1 and audit.last["details"]["forced"] is False


# --- Fix-Runde Welle 2: Sperre deckt auch unveraenderte, beruehrte RRsets ab -----------------------------------
async def test_replace_of_unchanged_rrset_conflicts_after_concurrent_change(two, fake_db, admin, audit, events):
    """Vorschau zeigt www A als unveraendert; ein danach hinzugefuegter Wert darf nicht still verschwinden."""
    p = await preview("ns1", {"ops": {"source": "api",
                                      "create": [{"name": WWW, "type": "A", "ttl": 3600,
                                                  "records": [{"content": "192.0.2.1"}, {"content": "192.0.2.2"}]}],
                                      "delete": [{"name": "old.example.com.", "type": "TXT"}]}}, fake_db, admin)
    assert [(c["name"], c["type"]) for c in p["changes"]] == [("old.example.com.", "TXT")]
    assert {(e.name, e.type) for e in p["ops"].expected} == {(WWW, "A"), ("old.example.com.", "TXT")}
    # zwischen Vorschau und Anwenden: jemand ergaenzt www A auf dem Primary
    two.ns1.rrset(Z, WWW, "A")["records"].append({"content": "192.0.2.3", "disabled": False})
    with pytest.raises(HTTPException) as ei:
        await records.bulk_update_records("ns1", Z, p["ops"], fake_db, admin)
    assert ei.value.status_code == 409 and ei.value.detail["conflicts"] == [{"name": WWW, "type": "A"}]
    assert two.ns1.patches == [] and two.ns2.patches == [] and events.calls == []
    assert two.ns1.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2", "192.0.2.3"]
    # ohne Zwischenaenderung laeuft derselbe Ablauf durch (nur das geaenderte RRset wird geschrieben)
    two.ns1.rrset(Z, WWW, "A")["records"].pop()
    p = await preview("ns1", {"ops": p["ops"].model_dump(exclude={"expected"})}, fake_db, admin)
    res = await records.bulk_update_records("ns1", Z, p["ops"], fake_db, admin)
    assert res.details["changed_rrsets"] == 1


async def test_expected_must_cover_every_changed_rrset(two, fake_db, admin, audit, events):
    """API-Body mit ``expected``, der ein tatsaechlich geaendertes RRset auslaesst -> 409 (force uebergeht)."""
    www_fp = snapshot_fingerprint(two.ns1.rrset(Z, WWW, "A"), "A", Z)
    body = {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}],
            "delete": [{"name": "old.example.com.", "type": "TXT"}],
            "expected": [{"name": WWW, "type": "A", "fingerprint": www_fp}]}
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", body, fake_db, admin)
    assert ei.value.status_code == 409
    assert ei.value.detail["conflicts"] == [{"name": "old.example.com.", "type": "TXT"}]
    assert two.ns1.patches == []
    res = await apply("ns1", {**body, "force": True}, fake_db, admin)
    assert res.details["changed_rrsets"] == 2 and audit.last["details"]["forced"] is True
    # ohne expected bleibt die Anwendung ungesperrt (Bestandsverhalten der API)
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 600}]}, fake_db, admin)
    assert res.details["changed_rrsets"] == 1


# --- Nr. 9: Audit-Format 4.3 ---------------------------------------------------------------------------------------
async def test_audit_details_v2_format(two, fake_db, admin, audit, events):
    res = await apply("ns1", {"source": "selection",
                              "delete": [{"name": "old.example.com.", "type": "TXT"}],
                              "set_disabled": [{"name": WWW, "type": "A", "content": "192.0.2.1", "disabled": True}]},
                      fake_db, admin)
    a = audit.last
    assert a["action"] == "BULK_UPDATE" and a["resource_type"] == "record" and a["resource_name"] == Z
    assert a["zone_name"] == Z and a["server_name"] == "ns1" and a["user_id"] == 1
    d = a["details"]
    assert d["version"] == 2 and "v" not in d and d["zone"] == Z
    assert d["source"] == "selection" and d["mode"] is None
    assert d["changes_total"] == len(d["changes"]) == 2
    assert d["created"] == 0 and d["deleted"] == 1
    assert d["ops"] == {"create": 0, "delete": 1, "merge": 0, "set_ttl": 0, "set_disabled": 1}
    assert d["forced"] is False and d["peer_drift"] == {} and d["primary_outcome"] == "ok"
    assert d["after_source"] == "computed" and d["fanout"] == res.details["fanout"]
    assert "applied" not in d
    by = {(c["name"], c["type"]): c for c in d["changes"]}
    assert by[("old.example.com.", "TXT")]["after"] is None and by[("old.example.com.", "TXT")]["op"] == "delete"
    www = by[(WWW, "A")]
    assert www["op"] == "update" and set(www["before"]) == {"ttl", "records", "comments"}
    assert {r["content"]: r["disabled"] for r in www["after"]["records"]} == {"192.0.2.1": True, "192.0.2.2": False}
    assert res.message == "Bulk-Änderung angewendet: 2 RRsets geändert"


# --- Nr. 10: Legacy-Body ------------------------------------------------------------------------------------------
async def test_legacy_body(two, fake_db, admin, audit, events):
    res = await apply("ns1", {"create": [{"name": "new.example.com.", "type": "A", "ttl": 300,
                                          "records": [{"content": "192.0.2.10"}]}],
                              "delete": [{"name": "old.example.com.", "type": "TXT"},
                                         {"name": "mail.example.com.", "type": "MX", "content": "20 mx2.example.com."}]},
                      fake_db, admin)
    assert res.details["created"] == 1 and res.details["deleted"] == 2 and res.details["changed_rrsets"] == 3
    assert two.ns1.values(Z, "mail.example.com.", "MX") == ["10 mx1.example.com."]
    assert two.ns2.values(Z, "new.example.com.", "A") == ["192.0.2.10"]
    (patch,) = two.ns1.patches
    assert len(patch) == 3


# --- Nr. 11: Rechte -----------------------------------------------------------------------------------------------
async def test_read_only_primary_is_403(two, admin, audit, events):
    db = FakeDB([server_config("ns1", allow_writes=False), server_config("ns2")])
    for call in (apply("ns1", {"delete": [{"name": "old.example.com.", "type": "TXT"}]}, db, admin),
                 preview("ns1", {"ops": {"delete": [{"name": "old.example.com.", "type": "TXT"}]}}, db, admin)):
        with pytest.raises(HTTPException) as ei:
            await call
        assert ei.value.status_code == 403 and "Speichern: Nein" in ei.value.detail
    assert two.ns1.calls == [] and audit.calls == []


def test_read_permission_user_gets_403(two, audit, events):
    user = make_user(role="user", uid=5, username="leser")
    session = FakeSession(user_row=user, zone_access=[(Z, "read")])
    client = TestClient(build_app(session, records), raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    body = {"delete": [{"name": "old.example.com.", "type": "TXT"}]}
    assert client.post(f"/api/v1/records/ns1/{Z}/bulk", json=body).status_code == 403
    assert client.post(f"/api/v1/records/ns1/{Z}/bulk/preview", json={"ops": body}).status_code == 403
    assert two.ns1.calls == []


def test_empty_request_is_422_after_acl(two, audit, events):
    user = make_user()
    client = TestClient(build_app(FakeSession(user_row=user), records), raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    r = client.post(f"/api/v1/records/ns1/{Z}/bulk", json={"create": [], "delete": []})
    assert r.status_code == 422 and r.json()["detail"]["message"] == "Keine Records zu verarbeiten"
    r = client.post(f"/api/v1/records/ns1/{Z}/bulk", json={"create": [{"name": WWW, "type": "A", "records": []}]})
    assert r.status_code == 422 and "leere Werteliste" in r.text
    assert two.ns1.calls == []


# --- Nr. 12: Webhook ------------------------------------------------------------------------------------------------
async def test_webhook_record_bulk_once_and_not_on_errors(two, fake_db, admin, audit, events):
    with pytest.raises(HTTPException):
        await apply("ns1", {"delete": [{"name": WWW, "type": "A", "content": "192.0.2.77"}]}, fake_db, admin)
    with pytest.raises(HTTPException):
        await apply("ns1", {"delete": [{"name": Z, "type": "NS"}]}, fake_db, admin)
    assert events.calls == []
    merges = [{"name": f"h{i}.example.com.", "type": "A", "records": [{"content": "192.0.2.1"}]} for i in range(205)]
    await apply("ns1", {"merge": merges, "source": "text", "mode": "merge"}, fake_db, admin)
    (ev,) = events.calls
    assert ev["event"] == "record.bulk" and ev["zone"] == Z and ev["server"] == "ns1" and ev["actor"] == 1
    assert ev["audit_log_id"] == 4700 + len(audit.calls)
    data = ev["data"]
    assert data["server"] == "ns1" and data["zone"] == Z and data["created"] == 0 and data["deleted"] == 0
    assert data["source"] == "text" and data["mode"] == "merge" and data["changes_total"] == 205
    assert len(data["changes"]) == 200 and data["changes_truncated"] is True and data["changes_count"] == 205
    assert data["changes"][0] == {"name": "h0.example.com.", "type": "A", "before": None,
                                  "after": {"ttl": 3600, "records": [{"content": "192.0.2.1", "disabled": False}]}}
    assert data["fanout"] == {"ns1": "saved", "ns2": "saved"}
    assert "ptr" not in data


# --- weitere Faelle --------------------------------------------------------------------------------------------------
async def test_missing_value_on_primary_404_with_issues(two, fake_db, admin, audit, events):
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"delete": [{"name": WWW, "type": "A", "content": "192.0.2.42"}]}, fake_db, admin)
    assert ei.value.status_code == 404
    assert ei.value.detail["issues"][0]["code"] == "value_missing"
    assert ei.value.detail["issues"][0]["params"]["content"] == "192.0.2.42"
    assert two.ns1.patches == [] and two.ns2.patches == []
    assert audit.last["status"] == "error"


async def test_blocking_plan_is_422_without_audit(two, fake_db, admin, audit, events):
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"delete": [{"name": Z, "type": "NS"}]}, fake_db, admin)
    assert ei.value.status_code == 422 and ei.value.detail["issues"][0]["code"] == "apex_ns_delete"
    assert audit.calls == []
    calls_before = len(two.ns1.calls)
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"delete": [{"name": "x.other.org.", "type": "A"}]}, fake_db, admin)
    assert ei.value.status_code == 422 and ei.value.detail["issues"][0]["code"] == "outside_zone"
    assert len(two.ns1.calls) == calls_before  # statische Pruefung vor jedem PowerDNS-Zugriff


async def test_no_changes_needed(two, fake_db, admin, audit, events):
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 3600}]}, fake_db, admin)
    assert res.message == "Keine Änderungen nötig" and res.details["audit_id"] is None
    assert res.details["fanout"] == {"ns1": "skipped (no changes needed)", "ns2": "skipped (no changes needed)"}
    assert audit.calls == [] and events.calls == [] and two.ns1.patches == []


async def test_peer_blocking_problem_only_errors_that_peer(two, fake_db, admin, audit, events):
    two.ns2.add_zone(make_zone(Z, [NS, rr("c.example.com.", "A", "192.0.2.1")]))
    res = await apply("ns1", {"create": [{"name": "c.example.com.", "type": "CNAME", "ttl": 300,
                                          "records": [{"content": "target.example.org."}]}]}, fake_db, admin)
    assert res.details["fanout"]["ns1"] == "saved"
    assert res.details["fanout"]["ns2"].startswith("error: c.example.com.: Ein CNAME")
    assert two.ns2.patches == []


async def test_primary_timeout_verified_after_reread(two, fake_db, admin, audit, events):
    two.ns1.timeout_after_patch = True
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}]}, fake_db, admin)
    assert res.details["fanout"] == {"ns1": "saved", "ns2": "saved"}
    d = audit.last["details"]
    assert d["primary_outcome"] == "verified_after_timeout" and d["after_source"] == "reread"


async def test_primary_timeout_without_effect_is_error(two, fake_db, admin, audit, events):
    two.ns1.timeout_before_patch = True  # Transportfehler, PATCH nicht angewendet -> Nachpruefung: "failed"
    with pytest.raises(HTTPException) as ei:
        await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 300}]}, fake_db, admin)
    assert ei.value.status_code == 504
    assert two.ns2.patches == [] and two.ns1.rrset(Z, WWW, "A")["ttl"] == 3600
    a = audit.last
    assert a["status"] == "error" and a["details"]["applied"] is False
    assert a["details"]["primary_outcome"] == "failed"


async def test_lua_gate_before_powerdns(two, audit, events):
    user = User(id=5, username="bob", role="user", is_active=True, hashed_password="x")
    db = FakeDB()
    body = {"merge": [{"name": "geo.example.com.", "type": "LUA",
                       "records": [{"content": "A \"ifportup(443, {'192.0.2.1'})\""}]}]}
    with pytest.raises(HTTPException) as ei:
        await bulk_service.apply_bulk(db, user, "ns1", Z, BulkRecordUpdate(**body))
    assert ei.value.status_code == 403 and ei.value.detail == MSG_DENIED_ADMIN
    assert two.ns1.calls == []
    # Vorschau meldet das Problem als blockierendes Issue statt 403
    p = await bulk_service.preview_bulk(db, user, "ns1", Z, BulkPreviewRequest(ops=body))
    assert p["blocking"] is True and [i["code"] for i in p["issues"]] == ["lua_forbidden"] and p["ops"] is None


async def test_lua_value_delete_keeping_rrset_needs_policy(two, audit, events):
    lua1, lua2 = "A \"ifportup(443, {'192.0.2.1'})\"", "A \"ifportup(443, {'192.0.2.2'})\""
    two.ns1.add_zone(make_zone(Z, [NS, rr("geo.example.com.", "LUA", lua1, lua2)]))
    user = User(id=5, username="bob", role="user", is_active=True, hashed_password="x")
    db = FakeDB()
    with pytest.raises(HTTPException) as ei:
        await bulk_service.apply_bulk(db, user, "ns1", Z, BulkRecordUpdate(
            delete=[{"name": "geo.example.com.", "type": "LUA", "content": lua1}]))
    assert ei.value.status_code == 403 and two.ns1.patches == []
    # ganzes LUA-RRset loeschen ist erlaubt (F15 5.6)
    res = await bulk_service.apply_bulk(db, user, "ns1", Z, BulkRecordUpdate(
        delete=[{"name": "geo.example.com.", "type": "LUA"}]))
    assert res.details["fanout"]["ns1"] == "saved"


def test_lua_bulk_policy_manage_from_settings(two, audit, events):
    user = make_user(role="user", uid=5, username="bob")
    session = FakeSession(user_row=user, zone_access=[(Z, "manage")],
                          extra={SystemSetting: [("lua_records_policy", "manage")]})
    client = TestClient(build_app(session, records), raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    r = client.post(f"/api/v1/records/ns1/{Z}/bulk", json={"merge": [{
        "name": "geo.example.com.", "type": "LUA", "records": [{"content": "A \"ifportup(443, {'192.0.2.1'})\""}]}]})
    assert r.status_code == 200, r.text


# --- Vorschau: Textfluss -------------------------------------------------------------------------------------------
async def test_preview_text_sync_scope_end_to_end(two, fake_db, admin, audit, events):
    text = "$ORIGIN example.com.\nwww\t3600\tIN\tA\t192.0.2.1\n;@disabled www 3600 IN A 192.0.2.2\n"
    req = {"text": {"content": text, "mode": "sync_scope", "default_ttl": 3600,
                    "scope": [{"name": WWW, "type": "A"}, {"name": "old.example.com.", "type": "TXT"}]}}
    p = await preview("ns1", req, fake_db, admin)
    assert p["source"] == "text" and p["mode"] == "sync_scope" and not p["blocking"]
    by = {(c["name"], c["type"]): c for c in p["changes"]}
    assert by[(WWW, "A")]["disabled_changed"] == ["192.0.2.2"] and by[(WWW, "A")]["semantics"] == ["replace"]
    assert by[("old.example.com.", "TXT")]["op"] == "delete"
    assert p["summary"]["rrsets_deleted"] == 1 and p["summary"]["values_removed"] == 1
    ops = p["ops"]
    assert [c.name for c in ops.create] == [WWW] and [d.name for d in ops.delete] == ["old.example.com."]
    assert {e.name for e in ops.expected} == {WWW, "old.example.com."}
    res = await records.bulk_update_records("ns1", Z, ops, fake_db, admin)
    assert res.details["changed_rrsets"] == 2
    assert audit.last["details"]["source"] == "text" and audit.last["details"]["mode"] == "sync_scope"


async def test_preview_text_parse_errors_and_unchanged(two, fake_db, admin, audit, events):
    p = await preview("ns1", {"text": {"content": "www A 192.0.2.1\nkaputt A nope\n$INCLUDE x"}}, fake_db, admin)
    assert p["blocking"] and [(i["code"], i["line"]) for i in p["issues"]] == [("parse_error", 2),
                                                                                ("directive_unsupported", 3)]
    assert p["ops"] is None and p["changes"] == []
    p = await preview("ns1", {"text": {"content": "www A 192.0.2.1", "mode": "merge"}}, fake_db, admin)
    assert p["changes"] == [] and p["summary"]["rrsets_unchanged"] == 1 and p["ops"] is None
    assert not p["blocking"]


async def test_preview_selection_issues_and_peers_info(two, admin, audit, events, monkeypatch):
    from fakes.pdns import FakePowerDNSClient

    monkeypatch.setitem(two.manager.clients, "ns3", FakePowerDNSClient("ns3"))
    db = FakeDB([server_config("ns1"), server_config("ns2"), server_config("ns3", allow_writes=False)])
    p = await preview("ns1", {"ops": {"source": "selection",
                                      "delete": [{"name": WWW, "type": "A", "content": "192.0.2.9"}]}}, db, admin)
    assert p["blocking"] and p["issues"][0]["code"] == "value_missing" and p["ops"] is None
    assert p["peers"] == ["ns2"] and p["skipped_servers"] == {"ns3": "read-only"}
    p = await preview("ns1", {"ops": {"source": "selection"}}, db, admin)
    assert p["blocking"] and p["issues"][0]["code"] == "empty_input"


async def test_preview_errors(fake_pdns, fake_db, admin):
    with pytest.raises(HTTPException) as ei:
        await preview("ns1", {"ops": {"delete": [{"name": WWW, "type": "A"}]}}, fake_db, admin)
    assert ei.value.status_code == 404 and "nicht vorhanden" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        await preview("nsX", {"ops": {"delete": [{"name": WWW, "type": "A"}]}}, fake_db, admin)
    assert ei.value.status_code == 404 and ei.value.detail == "Server 'nsX' nicht gefunden"


# --- PTR-Hooks ----------------------------------------------------------------------------------------------------
async def test_ptr_hook_not_loaded_without_a_changes_or_when_disabled(two, fake_db, admin, audit, events):
    # services/ptr.py wird nur geladen, wenn die PTR-Pflege laeuft (ohne A/AAAA-Aenderung, mit manage_ptr=False
    # bzw. ohne Admin-Default nicht) – in diesem Branch gaebe es sonst einen ModuleNotFoundError.
    res = await apply("ns1", {"delete": [{"name": "old.example.com.", "type": "TXT"}], "manage_ptr": True},
                      fake_db, admin)
    assert "ptr" not in res.details
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 120}], "manage_ptr": False},
                      fake_db, admin)
    assert "ptr" not in res.details and "ptr" not in audit.last["details"]
    # Admin-Default (ptr_auto_default) fehlt -> aus
    res = await apply("ns1", {"set_ttl": [{"name": WWW, "type": "A", "ttl": 180}]}, fake_db, admin)
    assert "ptr" not in res.details and "ptr" not in events.calls[-1]["data"]


@pytest.mark.wave_integration
async def test_ptr_sync_for_changes_called_with_audit_changes(two, fake_db, admin, audit, events, monkeypatch):
    from app.services import ptr as ptr_service

    calls = []

    async def fake_sync(db, user, server_name, changes, *, ttl_default=3600, actor_user_id=None):
        calls.append({"server": server_name, "changes": changes, "ttl_default": ttl_default, "actor": actor_user_id})
        return [{"ip": "192.0.2.5", "ptr": "5.2.0.192.in-addr.arpa.", "zone": "2.0.192.in-addr.arpa.",
                 "target": "n.example.com.", "op": "set", "action": "set", "reason": None, "existing": [],
                 "classless_zone": None, "fanout": {"ns1": "saved"}, "detail": None}]

    monkeypatch.setattr(ptr_service, "sync_for_changes", fake_sync)
    res = await apply("ns1", {"merge": [{"name": "n.example.com.", "type": "A", "records": [{"content": "192.0.2.5"}]}],
                              "manage_ptr": True, "default_ttl": 600}, fake_db, admin)
    (call,) = calls
    assert call["server"] == "ns1" and call["ttl_default"] == 600 and call["actor"] == 1
    assert call["changes"] == audit.last["details"]["changes"]
    assert res.details["ptr"][0]["action"] == "set"
    assert audit.last["details"]["ptr"] == ptr_service.compact(res.details["ptr"])
    assert events.calls[-1]["data"]["ptr"] == audit.last["details"]["ptr"]
    # Einzel-Endpoints: create/update/delete rufen denselben Haken mit den Audit-v2-changes
    from app.schemas.dns import RecordCreate, RecordDelete, RecordUpdate

    calls.clear()
    r = await records.create_record("ns1", Z, RecordCreate(name="m.example.com.", type="A", ttl=300,
                                                           records=[{"content": "192.0.2.6"}], manage_ptr=True),
                                    fake_db, admin)
    assert r.details["ptr"] and calls[-1]["ttl_default"] == 300
    assert calls[-1]["changes"][0]["after"]["records"][0]["content"] == "192.0.2.6"
    r = await records.update_record("ns1", Z, RecordUpdate(name="m.example.com.", type="A", ttl=300,
                                                           old_content="192.0.2.6", new_content="192.0.2.7",
                                                           manage_ptr=True), fake_db, admin)
    assert r.details["ptr"] and len(calls) == 2
    r = await records.delete_record("ns1", Z, RecordDelete(name="m.example.com.", type="A", manage_ptr=True),
                                    fake_db, admin)
    assert r.details["ptr"] and calls[-1]["changes"][0]["after"] is None
    assert "ptr" in events.calls[-1]["data"] and "ptr" in audit.last["details"]


@pytest.mark.wave_integration
async def test_ptr_admin_default_enables_sync(two, admin, audit, events, monkeypatch):
    from app.services import ptr as ptr_service

    calls = []

    async def fake_sync(db, user, server_name, changes, *, ttl_default=3600, actor_user_id=None):
        calls.append(changes)
        return []

    monkeypatch.setattr(ptr_service, "sync_for_changes", fake_sync)

    def handler(stmt):
        desc = getattr(stmt, "column_descriptions", None) or []
        if desc and desc[0].get("entity") is SystemSetting:
            return FakeResult([("ptr_auto_default", "true")])
        return FakeResult([])

    db = FakeDB(execute_handler=handler)
    res = await apply("ns1", {"set_disabled": [{"name": WWW, "type": "A", "content": "192.0.2.2", "disabled": True}]},
                      db, admin)
    assert len(calls) == 1 and res.details["ptr"] == []
