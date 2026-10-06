"""Plan-Builder des Bulk-Editors (F1 9.3): ``build_plan``, ``static_validate``, ``compile_text_ops``, Schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fakes.pdns import make_zone, rr
from app.schemas.bulk import BulkPreviewRequest, BulkRecordUpdate, BulkTextInput
from app.services.bind_fragment import parse_bind_fragment
from app.services.bulk import (
    build_plan,
    compile_text_ops,
    expectations,
    lua_write_keys,
    prune_unchanged,
    static_validate,
)
from app.services.rrsets import rrset_snapshot, snapshot_fingerprint

Z = "example.com."
WWW = "www.example.com."


def zone(*rrsets, dnssec=False):
    return make_zone(Z, [rr(f"{Z}", "SOA", "ns1.example.com. hostmaster.example.com. 1 10800 3600 604800 3600"),
                         rr(Z, "NS", "ns1.example.com.", "ns2.example.com."), *rrsets], dnssec=dnssec)


def ops(**kw) -> BulkRecordUpdate:
    return BulkRecordUpdate(**kw)


def plan_for(z, o, strict=True, **kw):
    return build_plan(Z, z, o, strict=strict, **kw)


def codes(plan, severity=None):
    return [i["code"] for i in plan.issues if severity is None or i["severity"] == severity]


# --- Nr. 1: create = REPLACE -----------------------------------------------------------------------------------
def test_create_replace_removes_value_and_reuses_pdns_string():
    z = zone(rr("v6.example.com.", "AAAA", "2001:db8::1", "2001:db8::2"))
    p = plan_for(z, ops(create=[{"name": "v6.example.com.", "type": "AAAA", "ttl": 3600,
                                 "records": [{"content": "2001:DB8:0:0::1"}]}]))
    (c,) = p.changes
    assert c.op == "update" and c.semantics == ["replace"]
    assert c.removed == ["2001:db8::2"] and c.added == [] and c.kept == ["2001:db8::1"]
    # PowerDNS-Darstellung des bestehenden Werts wird gesendet, nicht die Schreibweise aus dem Request
    assert p.patch_rrsets() == [{"name": "v6.example.com.", "type": "AAAA", "ttl": 3600, "changetype": "REPLACE",
                                 "records": [{"content": "2001:db8::1", "disabled": False}]}]
    assert p.summary()["values_removed"] == 1 and p.summary()["rrsets_updated"] == 1


# --- Nr. 2: merge ------------------------------------------------------------------------------------------------
def test_merge_adds_only_new_values_and_keeps_ttl():
    z = zone(rr(WWW, "A", "192.0.2.1", ttl=600))
    p = plan_for(z, ops(merge=[{"name": WWW, "type": "A", "records": [{"content": "192.0.2.1"},
                                                                       {"content": "192.0.2.2"}]}]))
    (c,) = p.changes
    assert c.added == ["192.0.2.2"] and c.removed == [] and c.ttl_after == 600 and c.semantics == ["merge"]


def test_merge_new_rrset_gets_default_ttl_and_explicit_ttl_wins():
    p = plan_for(zone(), ops(default_ttl=900, merge=[{"name": "new.example.com.", "type": "A",
                                                      "records": [{"content": "192.0.2.5"}]}]))
    (c,) = p.changes
    assert c.op == "create" and c.before is None and c.ttl_after == 900
    z = zone(rr(WWW, "A", "192.0.2.1", ttl=600))
    p = plan_for(z, ops(merge=[{"name": WWW, "type": "A", "ttl": 120, "records": [{"content": "192.0.2.1"}]}]))
    (c,) = p.changes
    assert c.ttl_before == 600 and c.ttl_after == 120 and c.added == []


# --- Nr. 3: letzter Wert geloescht -> DELETE --------------------------------------------------------------------
def test_delete_last_value_becomes_rrset_delete():
    z = zone(rr("t.example.com.", "TXT", '"x"'))
    p = plan_for(z, ops(delete=[{"name": "t.example.com.", "type": "TXT", "content": '"x"'}]))
    (c,) = p.changes
    assert c.op == "delete" and c.after is None and c.semantics == ["delete_value"]
    assert p.patch_rrsets() == [{"name": "t.example.com.", "type": "TXT", "changetype": "DELETE"}]


def test_delete_value_matches_canonically():
    z = zone(rr("t.example.com.", "TXT", '"foo" "bar"', '"keep"'))
    p = plan_for(z, ops(delete=[{"name": "t.example.com.", "type": "TXT", "content": "foo bar"}]))
    (c,) = p.changes
    assert c.removed == ['"foo" "bar"'] and c.kept == ['"keep"']


# --- Nr. 4: fehlender Wert strikt / nicht strikt -----------------------------------------------------------------
def test_missing_value_strict_issue_and_peer_noop():
    z = zone(rr(WWW, "A", "192.0.2.1"))
    o = ops(delete=[{"name": WWW, "type": "A", "content": "192.0.2.9"}])
    strict = plan_for(z, o)
    assert codes(strict) == ["value_missing"] and strict.blocking
    assert strict.issues[0]["params"] == {"name": WWW, "type": "A", "content": "192.0.2.9"}
    peer = plan_for(z, o, strict=False)
    assert peer.noop_ops == 1 and peer.issues == [] and peer.changes == []


def test_duplicate_value_delete_in_same_request_is_no_issue():
    z = zone(rr(WWW, "A", "192.0.2.1", "192.0.2.2"))
    p = plan_for(z, ops(delete=[{"name": WWW, "type": "A", "content": "192.0.2.1"},
                                {"name": WWW, "type": "A", "content": "192.0.2.1"}]))
    assert p.issues == [] and p.changes[0].removed == ["192.0.2.1"]


# --- Nr. 5: set_ttl / set_disabled -------------------------------------------------------------------------------
def test_set_ttl_missing_rrset_and_set_disabled_only_flips_flag():
    z = zone(rr(WWW, "A", "192.0.2.1", "192.0.2.2"))
    p = plan_for(z, ops(set_ttl=[{"name": "nope.example.com.", "type": "A", "ttl": 300}]))
    assert codes(p) == ["rrset_missing"]
    assert plan_for(z, ops(set_ttl=[{"name": "nope.example.com.", "type": "A", "ttl": 300}]),
                    strict=False).noop_ops == 1
    p = plan_for(z, ops(set_disabled=[{"name": WWW, "type": "A", "content": "192.0.2.2", "disabled": True}]))
    (c,) = p.changes
    assert c.disabled_changed == ["192.0.2.2"] and c.added == [] and c.removed == [] and c.semantics == ["disabled"]
    assert {r["content"]: r["disabled"] for r in c.after["records"]} == {"192.0.2.1": False, "192.0.2.2": True}
    p = plan_for(z, ops(set_ttl=[{"name": WWW, "type": "A", "ttl": 300}]))
    (c,) = p.changes
    assert (c.ttl_before, c.ttl_after, c.semantics) == (3600, 300, ["ttl"])


def test_set_disabled_already_in_target_state_is_unchanged():
    z = zone(rr(WWW, "A", "192.0.2.1", disabled=["192.0.2.1"]))
    p = plan_for(z, ops(set_disabled=[{"name": WWW, "type": "A", "content": "192.0.2.1", "disabled": True}]))
    assert p.changes == [] and p.unchanged == 1 and p.issues == []


# --- Nr. 6: unveraenderter REPLACE -------------------------------------------------------------------------------
def test_unchanged_replace_not_in_changes():
    z = zone(rr(WWW, "A", "192.0.2.1", "192.0.2.2", ttl=300))
    p = plan_for(z, ops(create=[{"name": WWW, "type": "A", "ttl": 300,
                                 "records": [{"content": "192.0.2.2"}, {"content": "192.0.2.1"}]}]))
    assert p.changes == [] and p.unchanged == 1 and p.patch_rrsets() == []


# --- Nr. 7: CNAME-Regeln -----------------------------------------------------------------------------------------
def test_cname_rules():
    z = zone(rr(WWW, "A", "192.0.2.1"))
    p = plan_for(z, ops(create=[{"name": WWW, "type": "CNAME", "ttl": 300, "records": [{"content": "x.example."}]}]))
    assert codes(p) == ["cname_conflict"]
    p = plan_for(zone(), ops(create=[{"name": "c.example.com.", "type": "CNAME", "ttl": 300,
                                      "records": [{"content": "a.example."}, {"content": "b.example."}]}]))
    assert codes(p) == ["cname_multi"]
    p = plan_for(zone(), ops(create=[{"name": Z, "type": "CNAME", "ttl": 300, "records": [{"content": "a.example."}]}]))
    assert codes(p) == ["apex_cname"]
    # CNAME ersetzt einen geloeschten A-Record im selben Request -> erlaubt
    p = plan_for(z, ops(delete=[{"name": WWW, "type": "A"}],
                        create=[{"name": WWW, "type": "CNAME", "ttl": 300, "records": [{"content": "x.example."}]}]))
    assert p.issues == [] and len(p.changes) == 2


# --- Nr. 8: Apex-NS und SOA --------------------------------------------------------------------------------------
def test_apex_ns_and_soa_delete_blocked():
    z = zone()
    p = plan_for(z, ops(delete=[{"name": Z, "type": "NS", "content": "ns1.example.com."},
                                {"name": Z, "type": "NS", "content": "ns2.example.com."}]))
    assert codes(p) == ["apex_ns_delete"]
    p = plan_for(z, ops(delete=[{"name": Z, "type": "NS", "content": "ns1.example.com."}]))
    assert p.issues == []  # einzelner NS-Wert darf weg
    p = plan_for(z, ops(delete=[{"name": Z, "type": "SOA",
                                 "content": "ns1.example.com. hostmaster.example.com. 1 10800 3600 604800 3600"}]))
    assert codes(p) == ["soa_forbidden"]
    assert [i["code"] for i in static_validate(Z, ops(delete=[{"name": Z, "type": "SOA"}]))] == ["soa_forbidden"]


# --- Nr. 9: LUA ------------------------------------------------------------------------------------------------
LUA = "A \"ifportup(443, {'192.0.2.1'})\""


def test_lua_forbidden_only_for_writes():
    z = zone(rr("geo.example.com.", "LUA", LUA, "A \"ifportup(443, {'192.0.2.2'})\""))
    o = ops(merge=[{"name": "geo.example.com.", "type": "LUA", "records": [{"content": "A \"ifportup(80, {'192.0.2.3'})\""}]}])
    p = plan_for(z, o, lua_allowed=False, lua_message="Nein.", lua_policy="disabled")
    assert codes(p) == ["lua_forbidden"]
    assert p.issues[0]["message"] == "Nein." and p.issues[0]["params"]["policy"] == "disabled"
    assert plan_for(z, o, lua_allowed=True).issues == []
    # ganzes LUA-RRset loeschen bleibt erlaubt (F15 5.6), einen von zwei Werten entfernen nicht (after bleibt)
    assert plan_for(z, ops(delete=[{"name": "geo.example.com.", "type": "LUA"}]), lua_allowed=False).issues == []
    p = plan_for(z, ops(delete=[{"name": "geo.example.com.", "type": "LUA", "content": LUA}]), lua_allowed=False)
    assert codes(p) == ["lua_forbidden"]
    assert lua_write_keys(o) == [("geo.example.com.", "LUA")]


# --- Nr. 10: Mengenlimit -----------------------------------------------------------------------------------------
def test_too_many_changes():
    o = ops(merge=[{"name": f"h{i}.example.com.", "type": "A", "records": [{"content": "192.0.2.1"}]}
                   for i in range(1001)])
    p = plan_for(zone(), o)
    assert codes(p) == ["too_many_changes"] and p.issues[0]["params"]["max"] == 1000


# --- Nr. 11: Kommentare -------------------------------------------------------------------------------------------
def test_patch_never_sends_comments_after_keeps_before_comments():
    comments = [{"content": "Webserver", "account": "ops", "modified_at": 1}]
    z = zone(rr(WWW, "A", "192.0.2.1", ttl=3600, comments=comments))
    p = plan_for(z, ops(set_ttl=[{"name": WWW, "type": "A", "ttl": 300}],
                        merge=[{"name": "n.example.com.", "type": "A", "records": [{"content": "192.0.2.7"}]}]))
    assert all("comments" not in r for r in p.patch_rrsets())
    by = {c.name: c for c in p.changes}
    assert by[WWW].after["comments"] == by[WWW].before["comments"] == comments
    assert by["n.example.com."].after["comments"] == []


# --- Nr. 12: Schema und statische Pruefung ------------------------------------------------------------------------
def test_schema_rules():
    with pytest.raises(ValidationError, match="leere Werteliste"):
        ops(create=[{"name": WWW, "type": "A", "records": []}])
    with pytest.raises(ValidationError, match="widersprüchliche Operationen"):
        ops(create=[{"name": WWW, "type": "A", "records": [{"content": "192.0.2.1"}]}],
            delete=[{"name": WWW, "type": "A"}])
    with pytest.raises(ValidationError, match="doppelt in 'create'"):
        ops(create=[{"name": WWW, "type": "A", "records": [{"content": "192.0.2.1"}]},
                    {"name": "WWW.example.com", "type": "a", "records": [{"content": "192.0.2.2"}]}])
    with pytest.raises(ValidationError, match="widersprüchliche Operationen"):
        ops(create=[{"name": WWW, "type": "A", "records": [{"content": "192.0.2.1"}]}],
            set_ttl=[{"name": WWW, "type": "A", "ttl": 300}])
    # Wert-Delete + set_ttl desselben RRsets ist erlaubt
    o = ops(delete=[{"name": WWW, "type": "A", "content": "192.0.2.1"}], set_ttl=[{"name": WWW, "type": "A", "ttl": 300}])
    assert len(o.delete) == 1 and o.set_ttl[0].name == WWW
    with pytest.raises(ValidationError, match="maximal 5000"):
        ops(set_ttl=[{"name": f"h{i}.example.com.", "type": "A", "ttl": 300} for i in range(3000)],
            delete=[{"name": f"d{i}.example.com.", "type": "A"} for i in range(2001)])
    with pytest.raises(ValidationError):
        ops(merge=[{"name": WWW, "type": "FOO", "records": [{"content": "x"}]}])
    with pytest.raises(ValidationError):
        ops(set_ttl=[{"name": WWW, "type": "TYPE65402", "ttl": 300}])
    with pytest.raises(ValidationError):
        ops(expected=[{"name": WWW, "type": "A", "fingerprint": "kaputt"}])
    with pytest.raises(ValidationError, match="Entweder 'ops' oder 'text'"):
        BulkPreviewRequest()
    with pytest.raises(ValidationError, match="sync_scope"):
        BulkPreviewRequest(text={"content": "www A 192.0.2.1", "mode": "sync_scope"})
    # Leere Anfrage ist schema-gueltig (der Endpunkt prueft nach der Zonen-Rechtepruefung)
    assert ops().create == []


def test_static_validate():
    o = ops(delete=[{"name": "www.other.org.", "type": "A"}], expected=[{"name": "x.foo.", "type": "A",
                                                                        "fingerprint": "absent"}],
            create=[{"name": WWW, "type": "RRSIG", "ttl": 300, "records": [{"content": "x"}]}])
    got = static_validate(Z, o)
    assert [i["code"] for i in got] == ["outside_zone", "outside_zone", "type_unknown"]
    assert got[0]["params"] == {"name": "www.other.org.", "zone": Z}
    assert static_validate(Z, ops(merge=[{"name": Z, "type": "A", "records": [{"content": "192.0.2.1"}]}])) == []
    # "notexample.com." liegt nicht in "example.com."
    assert [i["code"] for i in static_validate(Z, ops(delete=[{"name": "notexample.com.", "type": "A"}]))] == [
        "outside_zone"]


def test_dnskey_managed_warning():
    key = "257 3 13 mdsswUyr3DPW132mOi8V9xESWE8jTo0dxCjjnopKl+GqJxpVXckHAeF+KkxLbxILfDLUT0rAK9iUzy1L53eKGQ=="
    p = plan_for(zone(dnssec=True), ops(merge=[{"name": Z, "type": "DNSKEY", "records": [{"content": key}]}]),
                 dnssec_enabled=True)
    assert codes(p, "warning") == ["dnskey_managed"] and not p.blocking


# --- Nr. 13: compile_text_ops -------------------------------------------------------------------------------------
def test_compile_sync_scope_deletes_removed_scope_rrsets_and_replace_keeps_ttl():
    z = zone(rr(WWW, "A", "192.0.2.1", ttl=900), rr("old.example.com.", "TXT", '"weg"'),
             rr("mail.example.com.", "MX", "10 mx.example.com."))
    parsed = parse_bind_fragment(Z, "www A 192.0.2.1\nwww A 192.0.2.2")
    text = BulkTextInput(content="x", mode="sync_scope", scope=[
        {"name": WWW, "type": "A"}, {"name": "old.example.com.", "type": "TXT"},
        {"name": "gone.example.com.", "type": "A"}, {"name": Z, "type": "SOA"}, {"name": "x.other.", "type": "A"}])
    o, issues = compile_text_ops(parsed, text, z, Z)
    assert [(c.name, c.type, c.ttl) for c in o.create] == [(WWW, "A", 900)]
    assert [(d.name, d.type, d.content) for d in o.delete] == [("old.example.com.", "TXT", None)]
    assert [i["code"] for i in issues] == ["scope_invalid", "scope_invalid"]
    assert o.source == "text" and o.mode == "sync_scope"
    p = plan_for(z, o)
    assert {(c.name, c.op) for c in p.changes} == {(WWW, "update"), ("old.example.com.", "delete")}


def test_compile_merge_and_replace_ttl_defaults():
    parsed = parse_bind_fragment(Z, "new A 192.0.2.9\n;@disabled www 120 A 192.0.2.8")
    o, _ = compile_text_ops(parsed, BulkTextInput(content="x", mode="merge", default_ttl=600), zone(), Z)
    assert [(m.name, m.ttl, [(r.content, r.disabled) for r in m.records]) for m in o.merge] == [
        ("new.example.com.", None, [("192.0.2.9", False)]), (WWW, 120, [("192.0.2.8", True)])]
    assert o.default_ttl == 600
    o, _ = compile_text_ops(parsed, BulkTextInput(content="x", mode="replace", default_ttl=600), zone(), Z)
    assert [(c.name, c.ttl) for c in o.create] == [("new.example.com.", 600), (WWW, 120)]


def test_prune_unchanged_and_expectations():
    z = zone(rr(WWW, "A", "192.0.2.1"), rr("same.example.com.", "A", "192.0.2.3"))
    parsed = parse_bind_fragment(Z, "www A 192.0.2.2\nsame 3600 A 192.0.2.3")
    o, _ = compile_text_ops(parsed, BulkTextInput(content="x", mode="replace"), z, Z)
    p = plan_for(z, o)
    pruned = prune_unchanged(o, p)
    assert [c.name for c in pruned.create] == [WWW]
    (exp,) = expectations(pruned, z, Z)
    assert exp.fingerprint == snapshot_fingerprint(p.changes[0].before, "A", Z) and exp.fingerprint != "absent"
    new_ops = ops(merge=[{"name": "n.example.com.", "type": "A", "records": [{"content": "192.0.2.1"}]}])
    assert expectations(new_ops, zone(), Z)[0].fingerprint == "absent"


def test_expectations_cover_unchanged_touched_rrsets():
    """Fix-Runde Welle 2: auch unveraenderte, aber beruehrte RRsets bekommen einen Fingerprint."""
    z = zone(rr(WWW, "A", "192.0.2.1", "192.0.2.2"), rr("old.example.com.", "TXT", '"alt"'))
    o = ops(create=[{"name": WWW, "type": "A", "ttl": 3600,
                     "records": [{"content": "192.0.2.1"}, {"content": "192.0.2.2"}]}],
            delete=[{"name": "old.example.com.", "type": "TXT"}])
    p = plan_for(z, o)
    assert [c.key for c in p.changes] == [("old.example.com.", "TXT")] and p.unchanged == 1
    exp = {(e.name, e.type): e.fingerprint for e in expectations(o, z, Z)}
    assert set(exp) == {(WWW, "A"), ("old.example.com.", "TXT")}
    assert exp[(WWW, "A")] == snapshot_fingerprint(rrset_snapshot(z, WWW, "A"), "A", Z)


def test_changes_sorted_and_summary():
    z = zone(rr("b.example.com.", "A", "192.0.2.1"), rr("a.example.com.", "TXT", '"x"'))
    p = plan_for(z, ops(delete=[{"name": "b.example.com.", "type": "A"}, {"name": "a.example.com.", "type": "TXT"}],
                        merge=[{"name": "c.example.com.", "type": "A", "records": [{"content": "192.0.2.4"}]}]))
    assert [(c.name, c.type) for c in p.changes] == [("a.example.com.", "TXT"), ("b.example.com.", "A"),
                                                     ("c.example.com.", "A")]
    assert p.summary() == {"rrsets_created": 1, "rrsets_updated": 0, "rrsets_deleted": 2, "rrsets_unchanged": 0,
                           "values_added": 1, "values_removed": 2}
    assert [c.audit_dict()["op"] for c in p.changes] == ["delete", "delete", "create"]
