"""PTR-Service (F11 9 test_ptr_service, Bauplan B.6a [D7], [D11], [S5]).

``sync_ptrs`` laeuft gegen zwei In-Memory-PowerDNS-Server (echter Zonen-Index und Fan-out); Zonenrechte,
``write_audit`` und ``enqueue_event`` sind ersetzt bzw. werden aufgezeichnet.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.request_context import TokenScope, current_token_scope
from app.models.models import User
from app.services import fanout, ptr, webhook_outbox, zone_index
from app.services.pdns_client import PowerDNSAPIError
from app.services.ptr import PtrOp
from fakes.pdns import FakeDB, fake_pdns, make_zone, rr  # noqa: F401 - Fixture

REV = "2.0.192.in-addr.arpa."
REV2 = "113.0.203.in-addr.arpa."
FWD = "example.com."
HOST = "host.example.com."
SOURCE = {"action": "CREATE", "zone": FWD, "name": HOST, "server": "ns1"}


def ptr_name(octet: int) -> str:
    return f"{octet}.{REV}"


# ---------------------------------------------------------------------------------------------------------------------
# Reine Funktionen
# ---------------------------------------------------------------------------------------------------------------------
def test_reverse_name_v4_v6():
    assert ptr.reverse_name("192.0.2.5") == "5.2.0.192.in-addr.arpa."
    assert ptr.reverse_name("2001:db8::1").endswith(".8.b.d.0.1.0.0.2.ip6.arpa.")
    assert ptr.reverse_name("2001:db8::1").startswith("1.0.0.0.")


def test_canonical_ip():
    assert ptr.canonical_ip(" 2001:DB8:0::1 ") == "2001:db8::1"
    assert ptr.canonical_ip("192.0.2.1") == "192.0.2.1"
    assert ptr.canonical_ip("mail.example.com.") is None and ptr.canonical_ip(None) is None


@pytest.mark.parametrize("zones, ptr_name_, expected", [
    (["0/25.2.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", "0/25.2.0.192.in-addr.arpa."),
    (["0-25.2.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", "0-25.2.0.192.in-addr.arpa."),
    (["64-127.2.0.192.in-addr.arpa."], "70.2.0.192.in-addr.arpa.", "64-127.2.0.192.in-addr.arpa."),
    (["128/25.2.0.192.in-addr.arpa."], "200.2.0.192.in-addr.arpa.", "128/25.2.0.192.in-addr.arpa."),
    # [D11] Host-Oktett ausserhalb des Bereichs -> keine Classless-Zone
    (["0/26.2.0.192.in-addr.arpa."], "70.2.0.192.in-addr.arpa.", None),
    (["0-25.2.0.192.in-addr.arpa."], "30.2.0.192.in-addr.arpa.", None),
    (["64-127.2.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", None),
    # keine Classless-Labels
    (["2.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", None),
    (["5.2.0.192.in-addr.arpa."], "5.2.0.192.in-addr.arpa.", None),
    (["0/20.2.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", None),
    (["200/24.2.0.192.in-addr.arpa."], "210.2.0.192.in-addr.arpa.", None),
    (["30-10.2.0.192.in-addr.arpa."], "20.2.0.192.in-addr.arpa.", None),
    # andere Elternzone / IPv6
    (["0/25.3.0.192.in-addr.arpa."], "10.2.0.192.in-addr.arpa.", None),
    (["0/25.2.0.192.in-addr.arpa."], ptr.reverse_name("2001:db8::1"), None),
])
def test_detect_classless_zone(zones, ptr_name_, expected):
    assert ptr.detect_classless_zone(ptr_name_, zones) == expected


def test_classless_range():
    assert ptr.classless_range("0/26") == (0, 63)
    assert ptr.classless_range("128/25") == (128, 255)
    assert ptr.classless_range("64-127") == (64, 127)
    assert ptr.classless_range("0/32") == (0, 0)
    assert ptr.classless_range("5") is None and ptr.classless_range("256-300") is None


def test_ops_for_values():
    ops = ptr.ops_for_values("Host.Example.com", [("192.0.2.1", False), ("192.0.2.2", True), ("x", False)],
                             op="set", ttl=300)
    assert ops == [PtrOp("set", "192.0.2.1", HOST, 300)]
    ops = ptr.ops_for_values(HOST, [("192.0.2.1", False), ("192.0.2.2", True)], op="remove", ttl=0)
    assert [o.ip for o in ops] == ["192.0.2.1", "192.0.2.2"]


def test_ops_for_update():
    assert ptr.ops_for_update(HOST, "A", "192.0.2.1", "192.0.2.2", 300, False) == [
        PtrOp("remove", "192.0.2.1", HOST, 300), PtrOp("set", "192.0.2.2", HOST, 300)]
    assert ptr.ops_for_update(HOST, "AAAA", "2001:db8::1", "2001:DB8:0::1", 300, False) == []
    assert ptr.ops_for_update(HOST, "A", "192.0.2.1", "192.0.2.1", 300, True) == [PtrOp("remove", "192.0.2.1", HOST, 300)]
    assert ptr.ops_for_update(HOST, "A", "192.0.2.1", "192.0.2.2", 300, True) == [PtrOp("remove", "192.0.2.1", HOST, 300)]
    assert ptr.ops_for_update(HOST, "MX", "10 a.", "10 b.", 300, False) == []


def test_ops_for_rrset_change():
    ops = ptr.ops_for_rrset_change(HOST, [("192.0.2.1", False), ("192.0.2.2", False)],
                                   [("192.0.2.2", False), ("192.0.2.3", False)], 120)
    assert ops == [PtrOp("remove", "192.0.2.1", HOST, 120), PtrOp("set", "192.0.2.3", HOST, 120)]
    assert ptr.ops_for_rrset_change(HOST, [("192.0.2.1", False)], None, 60) == [PtrOp("remove", "192.0.2.1", HOST, 60)]
    # deaktivieren entfernt, aktivieren setzt
    assert ptr.ops_for_rrset_change(HOST, [("192.0.2.1", False)], [("192.0.2.1", True)], 60) == [
        PtrOp("remove", "192.0.2.1", HOST, 60)]
    assert ptr.ops_for_rrset_change(HOST, [("192.0.2.1", True)], [("192.0.2.1", False)], 60) == [
        PtrOp("set", "192.0.2.1", HOST, 60)]


def test_ops_for_changes_bulk_semantics():
    """[D7] merge legt an, set_disabled entfernt, set_ttl erzeugt nichts, Nicht-A/AAAA wird ignoriert."""
    snap = lambda ttl, *vals, dis=(): {"ttl": ttl, "records": [{"content": v, "disabled": v in dis} for v in vals],  # noqa: E731
                                       "comments": []}
    changes = [
        {"name": "a.example.com.", "type": "A", "before": snap(60, "192.0.2.1"), "after": snap(60, "192.0.2.1", "192.0.2.2")},
        {"name": "b.example.com.", "type": "A", "before": snap(60, "192.0.2.3"), "after": snap(60, "192.0.2.3", dis=("192.0.2.3",))},
        {"name": "c.example.com.", "type": "A", "before": snap(60, "192.0.2.4"), "after": snap(900, "192.0.2.4")},
        {"name": "d.example.com.", "type": "AAAA", "before": None, "after": snap(300, "2001:db8::4")},
        {"name": "e.example.com.", "type": "A", "before": snap(60, "192.0.2.5"), "after": None},
        {"name": "f.example.com.", "type": "TXT", "before": None, "after": snap(60, '"x"')},
    ]
    ops = ptr.ops_for_changes(changes)
    assert [(o.op, o.ip, o.target, o.ttl) for o in ops] == [
        ("set", "192.0.2.2", "a.example.com.", 60),
        ("remove", "192.0.2.3", "b.example.com.", 60),
        ("set", "2001:db8::4", "d.example.com.", 300),
        ("remove", "192.0.2.5", "e.example.com.", 3600),
    ]


def test_compact():
    res = [{"ip": "1", "ptr": "p", "zone": "z", "action": "set", "reason": None, "target": "t", "fanout": {}}]
    assert ptr.compact(res) == [{"ip": "1", "ptr": "p", "zone": "z", "action": "set", "reason": None}]
    assert ptr.compact(None) is None


# ---------------------------------------------------------------------------------------------------------------------
# sync_ptrs
# ---------------------------------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret

    async def __call__(self, *args, **kwargs):
        self.calls.append(SimpleNamespace(args=args, kwargs=kwargs))
        return self.ret


class World:
    def __init__(self, monkeypatch, pdns):
        self.pdns = pdns
        self.db = FakeDB()
        self.user = User(id=3, username="alice", role="user", is_active=True, hashed_password="x")
        self.write = {REV, REV2, "8.b.d.0.1.0.0.2.ip6.arpa."}
        self.read = set()
        self.audit = Recorder(ret=SimpleNamespace(id=900))
        self.events = Recorder(ret=1)
        self.applied = []

        async def has_zone_access(db, user, zone, *, write=False):
            return zone in self.write or (not write and zone in self.read)

        orig_apply = fanout.apply_rrsets

        async def apply(*a, **kw):
            self.applied.append((a, kw))
            return await orig_apply(*a, **kw)

        monkeypatch.setattr(ptr, "has_zone_access", has_zone_access)
        monkeypatch.setattr(ptr, "write_audit", self.audit)
        monkeypatch.setattr(webhook_outbox, "enqueue_event", self.events)
        monkeypatch.setattr(ptr.fanout, "apply_rrsets", apply)
        monkeypatch.setattr(fanout, "REREAD_DELAY", 0)

    async def sync(self, ops, **kw):
        kw.setdefault("source", SOURCE)
        return await ptr.sync_ptrs(self.db, ops, acl_user=self.user, actor_user_id=self.user.id, **kw)


@pytest.fixture
def world(monkeypatch, fake_pdns):
    zone_index.invalidate()
    fake_pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(20), "PTR", "other.example.com.", ttl=7200)]))
    fake_pdns.ns2.add_zone(make_zone(REV, [rr(ptr_name(20), "PTR", "other.example.com.", ttl=7200)]))
    fake_pdns.ns1.add_zone(make_zone(FWD))
    yield World(monkeypatch, fake_pdns)
    zone_index.invalidate()


def set_op(octet, target=HOST, ttl=300):
    return PtrOp("set", f"192.0.2.{octet}", target, ttl)


def rm_op(octet, target=HOST):
    return PtrOp("remove", f"192.0.2.{octet}", target, 300)


async def test_set_into_empty_rrset(world):
    (res,) = await world.sync([set_op(10)])
    assert res["action"] == "set" and res["zone"] == REV and res["ptr"] == ptr_name(10) and res["reason"] is None
    assert res["fanout"] == {"ns1": "saved", "ns2": "saved"}
    for srv in (world.pdns.ns1, world.pdns.ns2):
        r = srv.rrset(REV, ptr_name(10), "PTR")
        assert r["ttl"] == 300 and [x["content"] for x in r["records"]] == [HOST]
    (audit,) = world.audit.calls
    assert audit.args[1:4] == ("PTR_SYNC", "record", ptr_name(10))
    kw = audit.kwargs
    assert kw["zone_name"] == REV and kw["server_name"] == "ns1" and kw["user_id"] == 3
    d = kw["details"]
    assert d["version"] == 2 and d["zone"] == REV and d["auto_ptr"] is True and d["source"] == SOURCE
    assert d["type"] == "PTR" and d["after_source"] == "reread"
    (ch,) = d["changes"]
    assert ch["before"] is None and ch["after"]["records"] == [{"content": HOST, "disabled": False}]
    (ev,) = world.events.calls
    assert ev.args[1] == "record.ptr_synced" and ev.kwargs["zone"] == REV and ev.kwargs["audit_log_id"] == 900
    assert ev.kwargs["data"]["source"] == SOURCE and ev.kwargs["data"]["changes"][0]["name"] == ptr_name(10)


async def test_same_target_unchanged(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(10), "PTR", "HOST.example.com.")]))
    (res,) = await world.sync([set_op(10)])
    assert res["action"] == "unchanged" and world.pdns.ns1.patches == [] and world.audit.calls == []


async def test_other_target_is_conflict(world):
    (res,) = await world.sync([set_op(20)])
    assert res["action"] == "skipped" and res["reason"] == "conflict" and res["existing"] == ["other.example.com."]
    assert world.pdns.ns1.patches == []


async def test_remove_sole_value_deletes(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(10), "PTR", HOST)]))
    (res,) = await world.sync([rm_op(10)])
    assert res["action"] == "removed"
    assert world.pdns.ns1.patches == [[{"name": ptr_name(10), "type": "PTR", "changetype": "DELETE"}]]
    assert world.pdns.ns1.rrset(REV, ptr_name(10), "PTR") is None


async def test_remove_from_two_values_keeps_rest_and_disabled(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(10), "PTR", HOST, "keep.example.com.", ttl=900,
                                               disabled=["keep.example.com."], comments=[{"content": "c", "account": "a"}])]))
    (res,) = await world.sync([rm_op(10)])
    assert res["action"] == "removed"
    (patch,) = world.pdns.ns1.patches
    assert patch == [{"name": ptr_name(10), "type": "PTR", "ttl": 900, "changetype": "REPLACE",
                      "records": [{"content": "keep.example.com.", "disabled": True}],
                      "comments": [{"content": "c", "account": "a"}]}]


async def test_remove_foreign_target_other_target(world):
    (res,) = await world.sync([rm_op(20)])
    assert res["action"] == "skipped" and res["reason"] == "other_target" and res["existing"] == ["other.example.com."]
    assert world.pdns.ns1.patches == []


async def test_remove_missing_is_unchanged(world):
    (res,) = await world.sync([rm_op(30)])
    assert res["action"] == "unchanged" and world.pdns.ns1.patches == []


async def test_no_reverse_zone(world):
    (res,) = await world.sync([PtrOp("set", "198.51.100.7", HOST, 300)])
    assert res["action"] == "skipped" and res["reason"] == "no_reverse_zone" and res["zone"] is None


async def test_classless_zone_and_cname(world):
    world.pdns.ns1.add_zone(make_zone("0/26.2.0.192.in-addr.arpa."))
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(100), "CNAME", "100.64-127.2.0.192.in-addr.arpa.")]))
    world.read.add("0/26.2.0.192.in-addr.arpa.")
    a, b, c = await world.sync([set_op(10), set_op(70), set_op(100)])
    # [D11] .10 liegt in 0/26 -> classless; .70 nicht -> Elternzone
    assert a["action"] == "skipped" and a["reason"] == "classless" and a["classless_zone"] == "0/26.2.0.192.in-addr.arpa."
    assert b["action"] == "set" and b["zone"] == REV
    assert c["action"] == "skipped" and c["reason"] == "classless" and c["classless_zone"] == "64-127.2.0.192.in-addr.arpa."
    assert world.pdns.ns1.values(REV, ptr_name(70), "PTR") == [HOST]


async def test_classless_zone_name_hidden_without_read(world):
    """[S5] Ohne Leserecht auf die Classless-Zone erscheint ihr Name nicht."""
    world.pdns.ns1.add_zone(make_zone("0/26.2.0.192.in-addr.arpa."))
    (res,) = await world.sync([set_op(10)])
    assert res["reason"] == "classless" and res["classless_zone"] is None


async def test_acl_forbidden_only_affects_that_zone(world):
    world.pdns.ns1.add_zone(make_zone(REV2))
    world.write.discard(REV)
    a, b = await world.sync([set_op(10), PtrOp("set", "203.0.113.5", HOST, 300)])
    assert a["action"] == "skipped" and a["reason"] == "forbidden" and a["zone"] is None
    assert b["action"] == "set" and b["zone"] == REV2
    world.read.add(REV)
    (c,) = await world.sync([set_op(11)])
    assert c["reason"] == "forbidden" and c["zone"] == REV  # mit Leserecht darf die Zone genannt werden


async def test_two_ops_one_zone_one_patch(world):
    await world.sync([set_op(10), set_op(11, "b.example.com.")])
    assert len(world.applied) == 1
    (patch,) = world.pdns.ns1.patches
    assert sorted(x["name"] for x in patch) == [ptr_name(10), ptr_name(11)]
    assert len(world.audit.calls) == 1 and world.audit.calls[0].args[3] == REV  # mehrere -> Zone als Name


async def test_ip_moves_between_hosts(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(10), "PTR", "old.example.com.")]))
    a, b = await world.sync([PtrOp("set", "192.0.2.10", "new.example.com.", 300), rm_op(10, "old.example.com.")])
    assert a["action"] == "set" and b["action"] == "removed"
    (patch,) = world.pdns.ns1.patches
    assert patch[0]["changetype"] == "REPLACE" and patch[0]["records"] == [{"content": "new.example.com.",
                                                                             "disabled": False}]


async def test_remove_and_set_same_target_is_noop(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(10), "PTR", HOST)]))
    a, b = await world.sync([rm_op(10), set_op(10)])
    assert a["action"] == "unchanged" and b["action"] == "unchanged" and world.pdns.ns1.patches == []


async def test_rrset_read_error_is_error(world):
    await zone_index.writable_zone_map(world.db)  # Cache fuellen
    world.pdns.ns1.fail_on_get = PowerDNSAPIError(500, '{"error": "kaputt"}', "ns1")
    (res,) = await world.sync([set_op(10)])
    assert res["action"] == "error" and "kaputt" in res["detail"] and res["reason"] == "error"


async def test_index_failure_never_raises(world, monkeypatch):
    async def boom(db):
        raise RuntimeError("weg")

    monkeypatch.setattr(zone_index, "writable_zone_map", boom)
    res = await world.sync([set_op(10), set_op(11)])
    assert [r["action"] for r in res] == ["error", "error"] and all(r["detail"] for r in res)


async def test_wildcard_and_limit(world):
    res = await world.sync([PtrOp("set", "192.0.2.10", "*.example.com.", 300)])
    assert res[0]["reason"] == "wildcard"
    ops = [PtrOp("set", f"10.0.{i // 256}.{i % 256}", HOST, 300) for i in range(ptr.MAX_OPS + 2)]
    res = await world.sync(ops)
    assert [r["reason"] for r in res[-2:]] == ["limit", "limit"]
    assert res[0]["reason"] == "no_reverse_zone"


async def test_primary_failure_is_error_with_detached_audit(world):
    world.pdns.ns1.fail_on_patch = PowerDNSAPIError(422, '{"error": "abgelehnt"}', "ns1")
    (res,) = await world.sync([set_op(10)])
    assert res["action"] == "error" and "abgelehnt" in res["detail"]
    (audit,) = world.audit.calls
    assert audit.kwargs["status"] == "error" and world.events.calls == []
    assert world.pdns.ns2.patches == []  # require_primary: kein Peer-Write


async def test_preferred_server_used(world):
    await world.sync([set_op(10)], preferred_server="ns2")
    assert world.applied[0][0][1] == "ns2"
    world.applied.clear()
    await world.sync([set_op(11)], preferred_server="ns9")
    assert world.applied[0][0][1] == "ns1"


async def test_ipv6_ptr(world):
    world.pdns.ns1.add_zone(make_zone("8.b.d.0.1.0.0.2.ip6.arpa."))
    (res,) = await world.sync([PtrOp("set", "2001:db8::5", HOST, 300)])
    assert res["action"] == "set" and res["zone"] == "8.b.d.0.1.0.0.2.ip6.arpa."


async def test_token_scope_respected_via_real_acl(world, monkeypatch):
    """Panel-Token mit Scope example.com. darf keine PTRs in der Reverse-Zone schreiben (echtes has_zone_access)."""
    from app.core import auth

    monkeypatch.setattr(ptr, "has_zone_access", auth.has_zone_access)
    world.user = User(id=1, username="root", role="admin", is_active=True, hashed_password="x")
    tok = current_token_scope.set(TokenScope(token_id=1, name="t", token_prefix="dnsmgr_usr_x",
                                             zones=frozenset({FWD}), permission="manage", allow_admin=True))
    try:
        (res,) = await world.sync([set_op(10)])
    finally:
        current_token_scope.reset(tok)
    assert res["reason"] == "forbidden" and res["zone"] is None


# ---------------------------------------------------------------------------------------------------------------------
# sync_for_changes, lookup, reverse_zones_available
# ---------------------------------------------------------------------------------------------------------------------
async def test_sync_for_changes(world):
    changes = [{"name": HOST, "type": "A", "before": None,
                "after": {"ttl": 600, "records": [{"content": "192.0.2.10", "disabled": False}], "comments": []}},
               {"name": HOST, "type": "MX", "before": None, "after": {"ttl": 600, "records": [{"content": "10 x."}]}}]
    res = await ptr.sync_for_changes(world.db, world.user, "ns1", changes, action="CREATE", zone="Example.com")
    assert [r["action"] for r in res] == ["set"]
    assert world.pdns.ns1.rrset(REV, ptr_name(10), "PTR")["ttl"] == 600
    src = world.audit.calls[0].kwargs["details"]["source"]
    assert src == {"action": "CREATE", "zone": FWD, "name": HOST, "server": "ns1"}
    assert await ptr.sync_for_changes(world.db, world.user, "ns1", [{"name": HOST, "type": "TXT"}]) == []


async def test_lookup_states(world):
    db = world.db
    out = await ptr.lookup(db, world.user, "192.0.2.10", "host.example.com", None)
    assert out["status"] == "ok" and out["would"] == "set" and out["current"] == [] and out["zone"] == REV
    out = await ptr.lookup(db, world.user, "192.0.2.20", HOST, "ns2")
    assert out["would"] == "conflict" and out["current"] == ["other.example.com."]
    out = await ptr.lookup(db, world.user, "192.0.2.20", "other.example.com", None)
    assert out["would"] == "unchanged"
    out = await ptr.lookup(db, world.user, "198.51.100.1", None, None)
    assert out["status"] == "no_reverse_zone" and out["zone"] is None
    with pytest.raises(ValueError):
        await ptr.lookup(db, world.user, "keine-ip", None, None)


async def test_lookup_forbidden_variants(world):
    world.write.discard(REV)
    out = await ptr.lookup(world.db, world.user, "192.0.2.20", HOST, None)
    assert out["status"] == "forbidden" and out["zone"] is None and out["current"] is None
    world.read.add(REV)
    out = await ptr.lookup(world.db, world.user, "192.0.2.20", HOST, None)
    assert out["status"] == "forbidden" and out["zone"] == REV and out["current"] == ["other.example.com."]
    assert out["would"] is None


async def test_lookup_classless_and_error(world):
    world.pdns.ns1.add_zone(make_zone(REV, [rr(ptr_name(100), "CNAME", "100.64-127.2.0.192.in-addr.arpa.")]))
    out = await ptr.lookup(world.db, world.user, "192.0.2.100", None, None)
    assert out["status"] == "classless" and out["classless_zone"] == "64-127.2.0.192.in-addr.arpa."
    await zone_index.writable_zone_map(world.db)
    world.pdns.ns1.fail_on_get = PowerDNSAPIError(500, '{"error": "kaputt"}', "ns1")
    out = await ptr.lookup(world.db, world.user, "192.0.2.5", None, "ns1")
    assert out["status"] == "error" and out["zone"] == REV


async def test_reverse_zones_available_and_writable_filter(world, monkeypatch):
    world.pdns.ns1.add_zone(make_zone(REV2))

    async def flt(db, user):
        return {REV, "example.com."}

    monkeypatch.setattr(ptr, "writable_zone_filter", flt)
    assert await ptr.reverse_zones_available(world.db, world.user) == 1

    async def everything(db, user):
        return None

    monkeypatch.setattr(ptr, "writable_zone_filter", everything)
    assert await ptr.reverse_zones_available(world.db, world.user) == 2


async def test_writable_zone_filter_read_only_token():
    user = User(id=1, username="root", role="admin", is_active=True, hashed_password="x")
    tok = current_token_scope.set(TokenScope(token_id=1, name="t", token_prefix="p", zones=None, permission="read",
                                             allow_admin=True))
    try:
        assert await ptr.writable_zone_filter(FakeDB(), user) == set()
    finally:
        current_token_scope.reset(tok)
    assert await ptr.writable_zone_filter(FakeDB(), user) is None
    tok = current_token_scope.set(TokenScope(token_id=1, name="t", token_prefix="p", zones=frozenset({REV}),
                                             permission="manage", allow_admin=False))
    try:
        assert await ptr.writable_zone_filter(FakeDB(), user) == {REV}
    finally:
        current_token_scope.reset(tok)


async def test_resolve_manage_ptr(monkeypatch):
    async def gbs(db, key, default):
        assert key == ptr.KEY_AUTO_DEFAULT
        return True

    monkeypatch.setattr(ptr, "get_bool_setting", gbs)
    assert await ptr.resolve_manage_ptr(FakeDB(), None) is True
    assert await ptr.resolve_manage_ptr(FakeDB(), False) is False
