"""Rollback und Rollback-Vorschau (F7 9.2 Nr. 19–28, Bauplan B.7 [D12]).

``history.rollback_change``/``rollback_preview`` laufen direkt gegen zwei In-Memory-PowerDNS-Server; die
Session ist eine Fake-Session, die die Abfragen des Routers beantwortet (Audit-Eintrag per ``db.get``,
ServerConfig, Zonenrechte, LUA-Policy, Zonenloeschungen, Rollback-Verweise).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fakes.pdns import FakeDB, FakeResult, fake_pdns, make_zone, rr, server_config  # noqa: F401 - Fixtures
from app.models.models import AuditLog, ServerConfig, SystemSetting, User, UserZoneAccess
from app.routers import history
from app.schemas.history import RollbackRequest
from app.services import fanout, webhook_outbox
from app.services.pdns_client import PowerDNSAPIError

Z = "example.com."
WWW = "www.example.com."


def snap(*contents, ttl=3600, comments=None):
    return {"ttl": ttl, "records": [{"content": c, "disabled": False} for c in sorted(contents)],
            "comments": list(comments or [])}


def entry(changes, *, id=10, zone=Z, action="UPDATE", status="success", resource_type="record", version=2,
          server="ns1"):
    details = {"version": version, "zone": zone, "changes": changes, "after_source": "reread",
               "fanout": {"ns1": "saved"}} if version == 2 else {"zone": zone, "type": "A", "old": "x", "new": "y"}
    return AuditLog(id=id, action=action, resource_type=resource_type, resource_name=WWW, server_name=server,
                    details=details, status=status, user_id=1, zone_name=zone)


UPDATE_CHANGE = {"name": WWW, "type": "A", "before": snap("192.0.2.1"), "after": snap("192.0.2.2")}


class HistoryDB(FakeDB):
    """Beantwortet die Abfragen von ``routers/history.py`` und ``services/record_history.py``."""

    def __init__(self, logs=(), *, configs=(), perm=None, lua_policy=None, zone_deletes=(), reverted=()):
        super().__init__(server_configs=configs, execute_handler=self._handle)
        for log in logs:
            self.objects[(AuditLog, log.id)] = log
        self.perm = perm
        self.lua_policy = lua_policy
        self.zone_deletes = list(zone_deletes)   # [(id, details)]
        self.reverted = list(reverted)           # [(revert_of_id, max_id)]

    def _handle(self, stmt):
        desc = getattr(stmt, "column_descriptions", None) or []
        entity = desc[0].get("entity") if desc else None
        names = [d.get("name") for d in desc]
        if entity is ServerConfig:
            return FakeResult(self.server_configs)
        if entity is UserZoneAccess:
            return FakeResult([self.perm] if self.perm else [])
        if entity is SystemSetting:
            return FakeResult([("lua_records_policy", self.lua_policy)] if self.lua_policy else [])
        if entity is AuditLog and names == ["id", "details"]:
            return FakeResult(self.zone_deletes)
        if entity is AuditLog and names and names[0] == "revert_of_id":
            return FakeResult(self.reverted)
        return FakeResult([])


@pytest.fixture
def admin():
    return User(id=1, username="admin", role="admin", is_active=True, hashed_password="x")


@pytest.fixture
def manager():
    return User(id=5, username="bob", role="user", is_active=True, hashed_password="x")


@pytest.fixture
def pdns(fake_pdns, monkeypatch):
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)
    for srv in (fake_pdns.ns1, fake_pdns.ns2):
        srv.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.2"), rr(Z, "SOA", "ns1. h. 5 10800 3600 604800 3600")]))
    return fake_pdns


@pytest.fixture
def audit(monkeypatch):
    calls = []

    async def rec(db, action, resource_type, resource_name=None, **kw):
        calls.append({"action": action, "resource_type": resource_type, "resource_name": resource_name, **kw})
        return SimpleNamespace(id=900) if kw.get("status", "success") == "success" else None

    monkeypatch.setattr(history, "write_audit", rec)
    return calls


@pytest.fixture
def events(monkeypatch):
    calls = []

    async def rec(db, event, *, actor, data, zone=None, server=None, audit_log_id=None, actor_via=None):
        calls.append({"event": event, "data": data, "zone": zone, "server": server, "audit_log_id": audit_log_id})
        return 1

    monkeypatch.setattr(webhook_outbox, "enqueue_event", rec)
    return calls


async def _rollback(db, user, audit_id=10, *, force=False, zone=Z, server="ns1"):
    return await history.rollback_change(server, zone, audit_id, db, RollbackRequest(force=force), user)


# --- Nr. 19: erfolgreicher Rollback eines UPDATE ---------------------------------------------------------------
async def test_rollback_update_success(pdns, admin, audit, events):
    db = HistoryDB([entry([UPDATE_CHANGE])])
    res = await _rollback(db, admin)
    assert pdns.ns1.patches == [[{"name": WWW, "type": "A", "ttl": 3600, "changetype": "REPLACE",
                                  "records": [{"content": "192.0.2.1", "disabled": False}]}]]
    assert pdns.ns2.values(Z, WWW, "A") == ["192.0.2.1"]  # Peer folgt (Betriebsart A)
    (a,) = audit
    assert a["action"] == "RECORD_ROLLBACK" and a["resource_type"] == "record" and a["resource_name"] == WWW
    assert a["revert_of_id"] == 10 and a["zone_name"] == Z and a["server_name"] == "ns1" and a["user_id"] == 1
    d = a["details"]
    assert d["version"] == 2 and d["revert_of"] == 10 and d["forced"] is False and d["skipped"] == []
    assert d["after_source"] == "reread" and d["primary_outcome"] == "ok"
    (ch,) = d["changes"]
    assert ch["before"]["records"][0]["content"] == "192.0.2.2" and ch["after"]["records"][0]["content"] == "192.0.2.1"
    (ev,) = events
    assert ev["event"] == "record.rollback" and ev["audit_log_id"] == 900 and ev["zone"] == Z
    assert ev["data"]["reverted_audit_log_id"] == 10 and ev["data"]["forced"] is False
    assert ev["data"]["changes"][0]["after"] == {"ttl": 3600, "records": [{"content": "192.0.2.1", "disabled": False}]}
    assert res.details["revert_audit_id"] == 900 and res.details["rolled_back"] == [{"name": WWW, "type": "A"}]
    assert res.details["fanout"] == {"ns1": "saved", "ns2": "saved"}


# --- Nr. 20/21: Konflikt ohne/mit force -------------------------------------------------------------------------
async def test_conflict_without_force_is_409(pdns, admin, audit, events):
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.3")]))
    db = HistoryDB([entry([UPDATE_CHANGE])], reverted=[(10, 15)])
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin)
    assert ei.value.status_code == 409
    detail = ei.value.detail
    assert detail["code"] == "rollback_conflict" and detail["already_reverted_by"] == 15
    assert detail["conflicts"][0]["name"] == WWW and detail["conflicts"][0]["current"]["records"][0]["content"] == "192.0.2.3"
    assert pdns.ns1.patches == [] and audit == [] and events == []


async def test_conflict_with_force(pdns, admin, audit, events):
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.3")]))
    db = HistoryDB([entry([UPDATE_CHANGE])])
    res = await _rollback(db, admin, force=True)
    assert pdns.ns1.values(Z, WWW, "A") == ["192.0.2.1"]
    d = audit[0]["details"]
    assert d["forced"] is True and d["conflicts"][0]["expected"]["records"][0]["content"] == "192.0.2.2"
    assert res.details["forced"] is True and events[0]["data"]["forced"] is True


# --- Nr. 22: fremde Zone / unbekannte ID -------------------------------------------------------------------------
async def test_wrong_zone_or_unknown_id_is_404(pdns, admin, audit):
    db = HistoryDB([entry([UPDATE_CHANGE], zone="other.example.")])
    for audit_id in (10, 99):
        with pytest.raises(HTTPException) as ei:
            await _rollback(db, admin, audit_id)
        assert ei.value.status_code == 404 and ei.value.detail == "Audit-Eintrag nicht gefunden"
    assert pdns.ns1.patches == []


# --- Nr. 23: v1, nur SOA, gemischt -------------------------------------------------------------------------------
async def test_blocked_entries_are_422(pdns, admin, audit):
    soa = {"name": Z, "type": "SOA", "before": snap("ns1. h. 4 10800 3600 604800 3600"),
           "after": snap("ns1. h. 5 10800 3600 604800 3600")}
    db = HistoryDB([entry([], id=1, version=1), entry([soa], id=2, action="BULK_UPDATE")])
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin, 1)
    assert ei.value.status_code == 422 and ei.value.detail["code"] == "legacy_format"
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin, 2)
    assert ei.value.status_code == 422 and ei.value.detail["code"] == "only_excluded_records"
    assert ei.value.detail["message"].startswith("Der Eintrag betrifft nur SOA")
    assert pdns.ns1.patches == []


async def test_mixed_bulk_skips_soa(pdns, admin, audit, events):
    soa = {"name": Z, "type": "SOA", "before": snap("ns1. h. 4 10800 3600 604800 3600"),
           "after": snap("ns1. h. 5 10800 3600 604800 3600")}
    db = HistoryDB([entry([soa, UPDATE_CHANGE], action="BULK_UPDATE")])
    res = await _rollback(db, admin)
    assert [(p["name"], p["type"]) for p in pdns.ns1.patches[0]] == [(WWW, "A")]
    assert res.details["skipped"] == [{"name": Z, "type": "SOA", "reason": "soa"}]
    assert audit[0]["details"]["skipped"] == [{"name": Z, "type": "SOA", "reason": "soa"}]


# --- Nr. 24: Noop -----------------------------------------------------------------------------------------------
async def test_noop_when_already_on_before_state(pdns, admin, audit, events):
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1")]))
    db = HistoryDB([entry([UPDATE_CHANGE])])
    res = await _rollback(db, admin, force=True)
    assert res.details["noop"] is True
    assert pdns.ns1.patches == [] and audit == [] and events == []


# --- L10 (Review Welle 1): RRsets, die schon auf dem Vorher-Stand sind, sind kein Konflikt -------------------------
MAIL = "mail.example.com."
MX_CHANGE = {"name": MAIL, "type": "MX", "before": snap("10 a.example.com."), "after": snap("10 b.example.com.")}


async def test_noop_without_force_is_not_a_conflict(pdns, admin, audit, events):
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1")]))
    db = HistoryDB([entry([UPDATE_CHANGE])])
    res = await _rollback(db, admin)  # ohne force: kein 409
    assert res.details == {"noop": True, "skipped": []}
    assert pdns.ns1.patches == [] and pdns.ns2.patches == [] and audit == [] and events == []
    preview = await history.rollback_preview("ns1", Z, 10, db, admin)
    assert preview["has_conflicts"] is False
    (item,) = preview["plan"]
    assert item["noop"] is True and item["conflict"] is False


async def test_mixed_noop_and_change_writes_only_the_change(pdns, admin, audit, events):
    # www steht schon auf dem Vorher-Stand, mail noch auf dem Nachher-Stand -> nur mail wird geschrieben
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1"), rr(MAIL, "MX", "10 b.example.com.")]))
    pdns.ns2.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1"), rr(MAIL, "MX", "10 b.example.com.")]))
    db = HistoryDB([entry([UPDATE_CHANGE, MX_CHANGE], action="BULK_UPDATE")])
    preview = await history.rollback_preview("ns1", Z, 10, db, admin)
    assert preview["has_conflicts"] is False
    assert [(i["name"], i["noop"], i["conflict"]) for i in preview["plan"]] == [(WWW, True, False), (MAIL, False, False)]
    res = await _rollback(db, admin)
    assert [[(p["name"], p["type"]) for p in batch] for batch in pdns.ns1.patches] == [[(MAIL, "MX")]]
    assert pdns.ns1.values(Z, MAIL, "MX") == ["10 a.example.com."] and pdns.ns2.values(Z, MAIL, "MX") == ["10 a.example.com."]
    assert pdns.ns1.values(Z, WWW, "A") == ["192.0.2.1"]
    (a,) = audit
    assert a["details"]["forced"] is False and "conflicts" not in a["details"]
    assert res.details["forced"] is False and res.details["rolled_back"] == [{"name": MAIL, "type": "MX"}]
    assert events[0]["data"]["forced"] is False


async def test_noop_item_is_not_listed_as_conflict_next_to_a_real_one(pdns, admin, audit, events):
    # www schon zurueck (Noop), mail seitdem anders geaendert (echter Konflikt) -> 409 nennt nur mail
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1"), rr(MAIL, "MX", "20 c.example.com.")]))
    db = HistoryDB([entry([UPDATE_CHANGE, MX_CHANGE], action="BULK_UPDATE")])
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin)
    assert ei.value.status_code == 409
    assert [c["name"] for c in ei.value.detail["conflicts"]] == [MAIL]
    res = await _rollback(db, admin, force=True)
    assert [[p["name"] for p in batch] for batch in pdns.ns1.patches] == [[MAIL]]
    assert [c["name"] for c in audit[0]["details"]["conflicts"]] == [MAIL] and res.details["forced"] is True


# --- Nr. 25: Primary-PATCH-Fehler -------------------------------------------------------------------------------
async def test_primary_patch_error_audited(pdns, admin, audit, events):
    pdns.ns1.fail_on_patch = PowerDNSAPIError(422, '{"error": "kaputt"}', "ns1")
    db = HistoryDB([entry([UPDATE_CHANGE])])
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin)
    assert ei.value.status_code == 422
    (a,) = audit
    assert a["status"] == "error" and a["revert_of_id"] == 10 and a["zone_name"] == Z
    assert a["details"]["changes"] == [] and a["details"]["revert_of"] == 10
    assert pdns.ns2.patches == [] and events == []


# --- Nr. 26: Read-only-Primary, Lese-Nutzer ----------------------------------------------------------------------
async def test_read_only_primary_and_read_user_are_403(pdns, admin, manager, audit):
    db = HistoryDB([entry([UPDATE_CHANGE])], configs=[server_config("ns1", allow_writes=False),
                                                      server_config("ns2")])
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin)
    assert ei.value.status_code == 403 and "Speichern: Nein" in ei.value.detail
    db = HistoryDB([entry([UPDATE_CHANGE])], perm="read")
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, manager)
    assert ei.value.status_code == 403 and ei.value.detail == "Nur Lese-Zugriff auf diese Zone"
    assert pdns.ns1.patches == [] and audit == []


# --- Nr. 27: Rollback eines RRset-DELETE -------------------------------------------------------------------------
async def test_rollback_of_rrset_delete_restores_comments(pdns, admin, audit, events):
    comments = [{"content": "Mailserver", "account": "ops", "modified_at": 1}]
    deleted = {"name": "mail.example.com.", "type": "MX", "before": snap("10 mx.example.com.", comments=comments),
               "after": None}
    db = HistoryDB([entry([deleted], action="DELETE")])
    await _rollback(db, admin)
    restored = pdns.ns1.rrset(Z, "mail.example.com.", "MX")
    assert restored["records"] == [{"content": "10 mx.example.com.", "disabled": False}]
    assert restored["comments"] == comments
    assert pdns.ns1.patches[0][0]["comments"] == comments


# --- Nr. 28: LUA-Policy -----------------------------------------------------------------------------------------
async def test_lua_target_needs_lua_policy(pdns, manager, admin, audit):
    lua = {"name": "geo.example.com.", "type": "LUA", "before": snap('A "pickrandom({\'192.0.2.1\'})"'), "after": None}
    db = HistoryDB([entry([lua], action="DELETE")], perm="manage")
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, manager)
    assert ei.value.status_code == 403 and "Administratoren" in ei.value.detail
    assert pdns.ns1.patches == []
    db = HistoryDB([entry([lua], action="DELETE")], perm="manage", lua_policy="manage")
    await _rollback(db, manager)
    assert pdns.ns1.rrset(Z, "geo.example.com.", "LUA") is not None


# --- [D12] Zone neu angelegt ------------------------------------------------------------------------------------
async def test_entry_before_final_zone_delete(pdns, admin, manager, audit):
    deletes = [(30, {"zone_still_on_other_server": True}), (20, {"zone_still_on_other_server": False})]
    db = HistoryDB([entry([UPDATE_CHANGE], id=10)], zone_deletes=deletes, perm="manage")
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, admin)
    assert ei.value.status_code == 422 and ei.value.detail["code"] == "zone_recreated"
    with pytest.raises(HTTPException) as ei:
        await _rollback(db, manager)
    assert ei.value.status_code == 404  # fuer Nicht-Admins unsichtbar
    preview = await history.rollback_preview("ns1", Z, 10, db, admin)
    assert preview["rollbackable"] is False and preview["blocked_reason"] == "zone_recreated"
    assert pdns.ns1.patches == []


# --- Vorschau ----------------------------------------------------------------------------------------------------
async def test_preview_plan_conflict_and_targets(pdns, admin):
    pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.3")]))
    db = HistoryDB([entry([UPDATE_CHANGE], server="ns2")], configs=[server_config("ns1"), server_config("ns2")],
                   reverted=[(10, 12)])
    p = await history.rollback_preview("ns1", Z, 10, db, admin)
    assert p["rollbackable"] is True and p["has_conflicts"] is True and p["already_reverted_by"] == 12
    assert p["source_server"] == "ns2" and p["targets"] == {"ns1": "write", "ns2": "write"}
    (item,) = p["plan"]
    assert item["changetype"] == "REPLACE" and item["conflict"] is True and item["noop"] is False
    assert item["target"]["records"][0]["content"] == "192.0.2.1"
    assert pdns.ns1.patches == []


async def test_preview_read_only_and_blocked(pdns, admin):
    db = HistoryDB([entry([UPDATE_CHANGE]), entry([], id=11, version=1)],
                   configs=[server_config("ns1", allow_writes=False), server_config("ns2")])
    p = await history.rollback_preview("ns1", Z, 10, db, admin)
    assert p["rollbackable"] is False and p["blocked_reason"] == "server_read_only"
    assert p["targets"] == {"ns1": "skipped (read-only)"}
    p = await history.rollback_preview("ns1", Z, 11, db, admin)
    assert p["blocked_reason"] == "legacy_format" and p["blocked_message"] and p["plan"] == []


async def test_preview_zone_missing_on_primary_is_404(fake_pdns, admin):
    db = HistoryDB([entry([UPDATE_CHANGE])])
    with pytest.raises(HTTPException) as ei:
        await history.rollback_preview("ns1", Z, 10, db, admin)
    assert ei.value.status_code == 404 and "existiert auf Server 'ns1' nicht" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        await history.rollback_preview("nsX", Z, 10, db, admin)
    assert ei.value.status_code == 404


async def test_rollback_primary_timeout_verified(pdns, admin, audit, events):
    pdns.ns1.timeout_after_patch = True
    db = HistoryDB([entry([UPDATE_CHANGE])])
    res = await _rollback(db, admin)
    assert res.details["primary_outcome"] == "verified_after_timeout"
    assert audit[0]["details"]["after_source"] == "reread" and audit[0].get("status", "success") == "success"
    assert pdns.ns2.values(Z, WWW, "A") == ["192.0.2.1"]


# --- L-8 (WS-W3-NACHARBEIT): Rollback pflegt PTRs wie jeder Record-Schreiber -------------------------------------
async def test_rollback_runs_ptr_sync_with_admin_default(pdns, admin, audit, events, monkeypatch):
    from app.services import ptr as ptr_service

    seen = {}

    async def manage(db, requested):
        seen["requested"] = requested
        return seen.get("default", False)

    calls = []

    async def fake_sync(db, user, server_name, changes, *, ttl_default=3600, actor_user_id=None, action=None,
                        zone=None):
        calls.append({"server": server_name, "changes": changes, "action": action, "zone": zone})
        return [{"ip": "192.0.2.1", "ptr": "1.2.0.192.in-addr.arpa.", "zone": "2.0.192.in-addr.arpa.",
                 "target": WWW, "op": "set", "action": "set", "reason": None, "existing": [],
                 "classless_zone": None, "fanout": {"ns1": "saved"}, "detail": None}]

    monkeypatch.setattr(ptr_service, "resolve_manage_ptr", manage)
    monkeypatch.setattr(ptr_service, "sync_for_changes", fake_sync)
    # Admin-Default aus: keine PTR-Pflege, Antwort/Audit/Webhook ohne ptr
    res = await _rollback(HistoryDB([entry([UPDATE_CHANGE])]), admin)
    assert seen["requested"] is None and calls == []
    assert "ptr" not in res.details and "ptr" not in audit[-1]["details"] and "ptr" not in events[-1]["data"]
    # Admin-Default an: PTR-Pflege mit action ROLLBACK
    pdns.ns1.zones[Z]["rrsets"] = [r for r in pdns.ns1.zones[Z]["rrsets"] if r["type"] != "A"] + [
        rr(WWW, "A", "192.0.2.2")]
    pdns.ns2.zones[Z]["rrsets"] = [r for r in pdns.ns2.zones[Z]["rrsets"] if r["type"] != "A"] + [
        rr(WWW, "A", "192.0.2.2")]
    seen["default"] = True
    res = await _rollback(HistoryDB([entry([UPDATE_CHANGE])]), admin)
    (call,) = calls
    assert call["action"] == "ROLLBACK" and call["zone"] == Z and call["server"] == "ns1"
    assert call["changes"][0]["after"]["records"][0]["content"] == "192.0.2.1"
    assert res.details["ptr"][0]["action"] == "set"
    assert audit[-1]["details"]["ptr"] == ptr_service.compact(res.details["ptr"])
    assert events[-1]["data"]["ptr"] == audit[-1]["details"]["ptr"]
