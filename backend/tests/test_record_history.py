"""Reine Funktionen der Record-Historie (F7 9.2 Nr. 1–10, ohne DB und ohne PowerDNS)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import record_history as rh
from app.services.record_history import (
    build_changes,
    computed_after,
    exclusion_reason,
    history_details,
    inverse_rrsets,
    plan_rollback,
    rollback_block_reason,
    rr_key,
    simulate_patch,
    snapshot_rrset,
    snapshots_equivalent,
)

Z = "example.com."


def snap(*contents, ttl=3600, disabled=(), comments=None):
    dis = set(disabled)
    recs = sorted(({"content": c, "disabled": c in dis} for c in contents), key=lambda r: (r["content"], r["disabled"]))
    return {"ttl": ttl, "records": recs, "comments": list(comments or [])}


def zone(*rrsets):
    return {"name": Z, "rrsets": list(rrsets)}


def rr(name, rtype, *contents, ttl=3600, comments=None, disabled=()):
    return {"name": name, "type": rtype, "ttl": ttl, "comments": comments or [],
            "records": [{"content": c, "disabled": c in set(disabled)} for c in contents]}


def v2_log(changes, *, id=10, status="success", resource_type="record", **extra):
    details = {"version": 2, "zone": Z, "changes": changes, "after_source": "reread", "fanout": {"ns1": "saved"}}
    details.update(extra)
    return SimpleNamespace(id=id, status=status, resource_type=resource_type, details=details, zone_name=Z)


# --- Nr. 1: snapshot_rrset --------------------------------------------------------------------------------
def test_snapshot_rrset_case_insensitive_sorted_with_comments():
    comments = [{"content": "Hinweis", "account": "ops", "modified_at": 1}]
    z = zone(rr("WWW.Example.COM.", "a", "192.0.2.9", "192.0.2.1", ttl=300, comments=comments))
    s = snapshot_rrset(z, "www.example.com", "A")
    assert s == {"ttl": 300, "records": [{"content": "192.0.2.1", "disabled": False},
                                         {"content": "192.0.2.9", "disabled": False}], "comments": comments}


def test_snapshot_rrset_missing_or_empty_is_none():
    z = zone(rr("www.example.com.", "A"), rr("mail.example.com.", "MX", "10 mx.example.com."))
    assert snapshot_rrset(z, "www.example.com.", "A") is None  # leer (nur Kommentare/keine Records)
    assert snapshot_rrset(z, "nope.example.com.", "A") is None
    assert snapshot_rrset(None, "www.example.com.", "A") is None


# --- Nr. 2: computed_after --------------------------------------------------------------------------------
def test_computed_after_replace_without_comments_keeps_before_comments():
    before = snap("192.0.2.1", comments=[{"content": "c", "account": "a"}])
    payload = {"name": "www.example.com.", "type": "A", "ttl": 60, "changetype": "REPLACE",
               "records": [{"content": "192.0.2.2", "disabled": False}, {"content": "192.0.2.1", "disabled": True}]}
    after = computed_after(before, payload)
    assert after == {"ttl": 60, "records": [{"content": "192.0.2.1", "disabled": True},
                                            {"content": "192.0.2.2", "disabled": False}],
                     "comments": [{"content": "c", "account": "a"}]}
    assert computed_after(before, {**payload, "comments": []})["comments"] == []


def test_computed_after_delete_and_empty_records_are_none():
    before = snap("192.0.2.1")
    assert computed_after(before, {"name": "x", "type": "A", "changetype": "DELETE"}) is None
    assert computed_after(before, {"name": "x", "type": "A", "ttl": 60, "changetype": "REPLACE", "records": []}) is None


# --- Nr. 3: simulate_patch --------------------------------------------------------------------------------
def test_simulate_patch_value_delete_then_replace_in_order():
    k = rr_key("www.example.com.", "A")
    other = rr_key("old.example.com.", "A")
    before = {k: snap("192.0.2.1", "192.0.2.2"), other: snap("192.0.2.5")}
    out = simulate_patch(
        before,
        value_deletes=[(k, "192.0.2.1"), (other, "192.0.2.5")],
        rrsets=[{"name": "WWW.example.com.", "type": "a", "ttl": 120, "changetype": "REPLACE",
                 "records": [{"content": "192.0.2.2", "disabled": False}, {"content": "192.0.2.3", "disabled": False}]}],
    )
    assert out[k]["ttl"] == 120 and [r["content"] for r in out[k]["records"]] == ["192.0.2.2", "192.0.2.3"]
    assert out[other] is None  # letzter Wert entfernt
    # ohne REPLACE bleibt der Rest der Wert-Loeschung
    only_delete = simulate_patch(before, value_deletes=[(k, "192.0.2.1")])
    assert [r["content"] for r in only_delete[k]["records"]] == ["192.0.2.2"]
    assert before[k]["records"][0]["content"] == "192.0.2.1"  # Eingabe unveraendert


# --- Nr. 4: Kanonisierung / snapshots_equivalent -----------------------------------------------------------
def test_aaaa_expanded_equals_compressed():
    assert snapshots_equivalent(snap("2001:0db8:0000:0000:0000:0000:0000:0001"), snap("2001:db8::1"), "AAAA",
                                include_comments=False, origin=Z)


def test_cname_case_insensitive_txt_case_sensitive():
    assert snapshots_equivalent(snap("Target.Example.COM."), snap("target.example.com."), "CNAME",
                                include_comments=False, origin=Z)
    assert not snapshots_equivalent(snap('"Hallo Welt"'), snap('"hallo welt"'), "TXT", include_comments=False, origin=Z)


def test_ttl_and_disabled_differences_are_not_equivalent():
    assert not snapshots_equivalent(snap("192.0.2.1", ttl=60), snap("192.0.2.1", ttl=300), "A", include_comments=False)
    assert not snapshots_equivalent(snap("192.0.2.1", disabled=["192.0.2.1"]), snap("192.0.2.1"), "A",
                                    include_comments=False)
    assert snapshots_equivalent(None, None, "A", include_comments=True)
    assert not snapshots_equivalent(None, snap("192.0.2.1"), "A", include_comments=True)


def test_comments_only_count_with_include_comments():
    a = snap("192.0.2.1", comments=[{"content": "x", "account": "a", "modified_at": 1}])
    b = snap("192.0.2.1", comments=[{"content": "y", "account": "a", "modified_at": 1}])
    c = snap("192.0.2.1", comments=[{"content": "x", "account": "a", "modified_at": 99}])
    assert snapshots_equivalent(a, b, "A", include_comments=False)
    assert not snapshots_equivalent(a, b, "A", include_comments=True)
    assert snapshots_equivalent(a, c, "A", include_comments=True)  # modified_at zaehlt nicht


# --- Nr. 5: build_changes -------------------------------------------------------------------------------
def test_build_changes_create_delete_update_noop_sorted():
    k_new = rr_key("b.example.com.", "A")
    k_del = rr_key("a.example.com.", "TXT")
    k_upd = rr_key("a.example.com.", "A")
    k_same = rr_key("c.example.com.", "AAAA")
    before = {k_del: snap('"x"'), k_upd: snap("192.0.2.1"), k_same: snap("2001:db8::1"), k_new: None}
    after = {k_new: snap("192.0.2.7"), k_del: None, k_upd: snap("192.0.2.1", "192.0.2.2", ttl=60),
             k_same: snap("2001:0db8::0001")}
    changes = build_changes(before, after, origin=Z)
    assert [(c["name"], c["type"]) for c in changes] == [k_upd, k_del, k_new]
    assert changes[1]["after"] is None and changes[2]["before"] is None
    assert changes[0]["before"]["ttl"] == 3600 and changes[0]["after"]["ttl"] == 60
    assert build_changes(before, after, origin=Z) == changes  # deterministisch


# --- Nr. 6: exclusion_reason ----------------------------------------------------------------------------
@pytest.mark.parametrize("name,rtype,expected", [
    ("example.com.", "SOA", "soa"),
    ("example.com.", "DNSKEY", "dnssec"),
    ("sub.example.com.", "DS", "dnssec"),
    ("example.com.", "CDS", "dnssec"),
    ("_acme-challenge.www.example.com.", "TXT", "acme_challenge"),
    ("_ACME-Challenge.www.example.com.", "CNAME", "acme_challenge"),
    ("www.example.com.", "A", None),
    ("x_acme-challenge.example.com.", "TXT", None),
])
def test_exclusion_reason(name, rtype, expected):
    assert exclusion_reason(name, rtype) == expected


# --- Nr. 7: rollback_block_reason -----------------------------------------------------------------------
def test_rollback_block_reasons():
    good = [{"name": "www.example.com.", "type": "A", "before": snap("192.0.2.1"), "after": snap("192.0.2.2")}]
    soa = [{"name": "example.com.", "type": "SOA", "before": snap("a b 1 2 3 4 5"), "after": snap("a b 2 2 3 4 5")}]
    v1 = SimpleNamespace(id=1, status="success", resource_type="record", details={"zone": Z, "type": "A"})
    assert rollback_block_reason(v1) == "legacy_format"
    assert rollback_block_reason(v2_log(good, status="error")) == "failed_action"
    assert rollback_block_reason(v2_log(good, resource_type="zone")) == "not_record_change"
    assert rollback_block_reason(v2_log([], history_incomplete=True)) == "incomplete"
    assert rollback_block_reason(v2_log([], history_truncated=True)) == "incomplete"
    assert rollback_block_reason(v2_log([])) == "no_changes"
    assert rollback_block_reason(v2_log(soa)) == "only_excluded_records"
    assert rollback_block_reason(v2_log(good)) is None
    # [D12] Eintrag vor der letzten endgueltigen Zonenloeschung
    assert rollback_block_reason(v2_log(good, id=5), recreated_cutoff=5) == "zone_recreated"
    assert rollback_block_reason(v2_log(good, id=6), recreated_cutoff=5) is None
    assert set(rh.BLOCK_MESSAGES) >= {"legacy_format", "failed_action", "not_record_change", "incomplete",
                                      "no_changes", "only_excluded_records", "zone_recreated",
                                      "no_write_permission", "server_read_only"}


# --- Nr. 8: plan_rollback --------------------------------------------------------------------------------
def test_plan_rollback_conflict_noop_and_changetypes():
    change = {"name": "www.example.com.", "type": "A", "before": snap("192.0.2.1"), "after": snap("192.0.2.2")}
    log = v2_log([change])
    plan, skipped = plan_rollback(log, zone(rr("www.example.com.", "A", "192.0.2.2")))
    assert skipped == [] and len(plan) == 1
    assert plan[0].conflict is False and plan[0].noop is False and plan[0].changetype == "REPLACE"

    plan, _ = plan_rollback(log, zone(rr("www.example.com.", "A", "192.0.2.3")))
    assert plan[0].conflict is True and plan[0].noop is False

    plan, _ = plan_rollback(log, zone(rr("www.example.com.", "A", "192.0.2.1")))
    assert plan[0].noop is True and plan[0].conflict is True

    created = {"name": "new.example.com.", "type": "A", "before": None, "after": snap("192.0.2.9")}
    plan, _ = plan_rollback(v2_log([created]), zone(rr("new.example.com.", "A", "192.0.2.9")))
    assert plan[0].changetype == "DELETE" and plan[0].conflict is False

    comments = [{"content": "wichtig", "account": "ops"}]
    deleted = {"name": "gone.example.com.", "type": "TXT", "before": snap('"v"', comments=comments), "after": None}
    plan, _ = plan_rollback(v2_log([deleted]), zone())
    assert plan[0].changetype == "REPLACE" and plan[0].current is None and plan[0].conflict is False
    payload = inverse_rrsets(plan)
    assert payload == [{"name": "gone.example.com.", "type": "TXT", "ttl": 3600, "changetype": "REPLACE",
                        "records": [{"content": '"v"', "disabled": False}], "comments": comments}]


def test_plan_rollback_skips_excluded_and_ignores_comment_drift():
    soa = {"name": "example.com.", "type": "SOA", "before": snap("a. b. 1 2 3 4 5"), "after": snap("a. b. 2 2 3 4 5")}
    a = {"name": "www.example.com.", "type": "A", "before": snap("192.0.2.1"),
         "after": snap("192.0.2.2", comments=[{"content": "alt", "account": ""}])}
    plan, skipped = plan_rollback(v2_log([soa, a]), zone(rr("www.example.com.", "A", "192.0.2.2",
                                                           comments=[{"content": "neu", "account": "x"}])))
    assert skipped == [{"name": "example.com.", "type": "SOA", "reason": "soa"}]
    assert [i.type for i in plan] == ["A"] and plan[0].conflict is False


# --- Nr. 9: inverse_rrsets -------------------------------------------------------------------------------
def test_inverse_rrsets_payload_shape():
    change = {"name": "www.example.com.", "type": "A",
              "before": snap("192.0.2.1", ttl=300, comments=[{"content": "c", "account": "a"}]),
              "after": snap("192.0.2.2")}
    plan, _ = plan_rollback(v2_log([change]), zone(rr("www.example.com.", "A", "192.0.2.2")))
    assert inverse_rrsets(plan) == [{"name": "www.example.com.", "type": "A", "ttl": 300, "changetype": "REPLACE",
                                     "records": [{"content": "192.0.2.1", "disabled": False}]}]  # kein comments-Key
    created = {"name": "n.example.com.", "type": "A", "before": None, "after": snap("192.0.2.9")}
    plan, _ = plan_rollback(v2_log([created]), zone(rr("n.example.com.", "A", "192.0.2.9")))
    assert inverse_rrsets(plan) == [{"name": "n.example.com.", "type": "A", "changetype": "DELETE"}]


# --- Nr. 10: history_details ------------------------------------------------------------------------------
def test_history_details_legacy_and_extra():
    d = history_details("Example.COM", [], fanout={"ns1": "saved"}, after_source="reread",
                        legacy={"type": "A", "zone": "ignoriert", "records": ["192.0.2.1"]},
                        extra={"primary_outcome": "ok"})
    assert d["version"] == 2 and d["zone"] == "example.com." and d["changes"] == []
    assert d["type"] == "A" and d["records"] == ["192.0.2.1"] and d["primary_outcome"] == "ok"
    assert d["after_source"] == "reread" and d["fanout"] == {"ns1": "saved"}


def test_history_details_over_4mb_is_truncated(monkeypatch):
    monkeypatch.setattr(rh, "MAX_DETAILS_BYTES", 2000)
    changes = [{"name": f"h{i}.example.com.", "type": "TXT", "before": None, "after": snap('"' + "x" * 50 + '"')}
               for i in range(600)]
    d = history_details(Z, changes, fanout={}, after_source="computed", legacy={"created": 600, "deleted": 0})
    assert d["changes"] == [] and d["history_truncated"] is True and d["change_count"] == 600
    assert len(d["change_keys"]) == 500 and d["change_keys"][0] == {"name": "h0.example.com.", "type": "TXT"}
    assert d["created"] == 600
    assert len(json.dumps(d)) < 100_000
    assert rollback_block_reason(SimpleNamespace(id=1, status="success", resource_type="record", details=d)) \
        == "incomplete"


# --- Ergaenzend: Webhook-Changes, Serialisierung ---------------------------------------------------------
def test_webhook_changes_compact_and_limit():
    changes = [{"name": f"h{i}.example.com.", "type": "A", "before": None,
                "after": snap("192.0.2.1", comments=[{"content": "c", "account": ""}])} for i in range(3)]
    out = rh.webhook_changes(changes, limit=2)
    assert out["changes"][0] == {"name": "h0.example.com.", "type": "A", "before": None,
                                 "after": {"ttl": 3600, "records": [{"content": "192.0.2.1", "disabled": False}]}}
    assert out["changes_truncated"] is True and out["changes_count"] == 3 and len(out["changes"]) == 2


def _row(**kw):
    base = dict(id=7, timestamp=None, action="UPDATE", resource_type="record", resource_name="www.example.com.",
                server_name="ns1", zone_name=Z, user_id=3, status="success", error_message=None, revert_of_id=None,
                actor_username="alice", client_ip="198.51.100.7")
    base.update(kw)
    return SimpleNamespace(**base)


def test_serialize_history_entry_public_details_for_non_admin():
    changes = [{"name": f"h{i}.example.com.", "type": "A", "before": None, "after": snap("192.0.2.1")}
               for i in range(25)]
    details = {"version": 2, "zone": Z, "changes": changes, "after_source": "reread",
               "fanout": {"ns1": "saved", "ns2": "error: {\"error\": \"intern\"}"}, "primary_outcome": "ok",
               "client_ip": "198.51.100.7", "auth": {"via": "panel_token", "token_id": 1, "token_name": "ci",
                                                     "token_prefix": "dnsmgr_usr_ab"}}
    log = _row(details=details, status="error", error_message="PowerDNS sagt: interne Info")
    out = rh.serialize_history_entry(log, {3: "alice"}, {7: 9}, user_can_write=True, server_writable=True,
                                     max_changes=20, admin=False)
    assert out["version"] == 2 and len(out["changes"]) == 20 and out["changes_truncated"] is True
    assert out["change_count"] == 25 and out["reverted_by_id"] == 9 and out["username"] == "alice"
    assert "changes" not in out["details"] and "client_ip" not in out["details"]
    assert out["details"]["auth"] == {"via": "panel_token", "token_name": "ci"}
    assert out["details"]["fanout"]["ns2"] == "error: PowerDNS-Fehler"
    assert out["client_ip"] is None and out["error_message"] == "PowerDNS-Fehler"
    assert out["rollback_blocked_reason"] == "failed_action" and out["can_rollback"] is False

    admin = rh.serialize_history_entry(log, {}, {}, user_can_write=True, server_writable=True, max_changes=None,
                                       admin=True)
    assert admin["client_ip"] == "198.51.100.7" and admin["details"]["client_ip"] == "198.51.100.7"
    assert admin["error_message"] == "PowerDNS sagt: interne Info" and len(admin["changes"]) == 25
    assert admin["username"] is None  # Benutzer geloescht


def test_serialize_history_entry_block_order_and_v1():
    good = {"version": 2, "zone": Z, "changes": [{"name": "www.example.com.", "type": "A", "before": snap("192.0.2.1"),
                                                  "after": snap("192.0.2.2")}], "fanout": {}, "after_source": "reread"}
    kw = dict(max_changes=20, admin=True)
    assert rh.serialize_history_entry(_row(details=good), {}, {}, user_can_write=False, server_writable=False,
                                      **kw)["rollback_blocked_reason"] == "no_write_permission"
    assert rh.serialize_history_entry(_row(details=good), {}, {}, user_can_write=True, server_writable=False,
                                      **kw)["rollback_blocked_reason"] == "server_read_only"
    ok = rh.serialize_history_entry(_row(details=good), {}, {}, user_can_write=True, server_writable=True, **kw)
    assert ok["can_rollback"] is True and ok["before_recreate"] is False
    old = rh.serialize_history_entry(_row(details=good, id=3), {}, {}, user_can_write=True, server_writable=True,
                                     recreated_cutoff=4, **kw)
    assert old["before_recreate"] is True and old["rollback_blocked_reason"] == "zone_recreated"
    v1 = rh.serialize_history_entry(_row(details={"zone": Z, "type": "A", "old": "1", "new": "2"}), {}, {},
                                    user_can_write=True, server_writable=True, **kw)
    assert v1["version"] == 1 and v1["changes"] is None and v1["change_count"] == 0
    assert v1["rollback_blocked_reason"] == "legacy_format"


def test_serialize_audit_entry_truncates_without_full():
    changes = [{"name": f"h{i}.example.com.", "type": "A", "before": None, "after": snap("192.0.2.1")}
               for i in range(30)]
    log = _row(details={"version": 2, "zone": Z, "changes": changes})
    short = rh.serialize_audit_entry(log, {3: "alice"}, {}, full=False)
    assert short["details_truncated"] is True and len(short["details"]["changes"]) == 20
    assert short["details"]["change_count"] == 30 and len(log.details["changes"]) == 30
    full = rh.serialize_audit_entry(log, {3: "alice"}, {}, full=True)
    assert full["details_truncated"] is False and len(full["details"]["changes"]) == 30
    assert full["username"] == "alice" and full["client_ip"] == "198.51.100.7"


def test_parse_actions_and_filter_validation():
    from fastapi import HTTPException

    assert rh.parse_actions(" create, UPDATE ,create,,") == ["CREATE", "UPDATE"]
    with pytest.raises(HTTPException) as ei:
        rh.parse_actions(",".join(f"A{i}" for i in range(21)))
    assert ei.value.status_code == 400
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(HTTPException) as ei:
        rh.build_audit_filters(date_from=now, date_to=now - timedelta(hours=1))
    assert ei.value.status_code == 400 and "date_from" in ei.value.detail
    conds = rh.build_audit_filters(zone="Example.COM", action="create", name="WWW.example.com", record_type="mx",
                                   q="50%_x", q_admin_columns=True, status="error", user_id=3,
                                   date_from=now, date_to=now)
    assert len(conds) == 9
    params = {}
    for c in conds:
        params.update({f"{k}#{id(c)}": v for k, v in c.compile().params.items()})
    values = list(params.values())
    assert "example.com." in values and "www.example.com." in values and "MX" in values
    assert "%50\\%\\_x%" in values  # LIKE-Sonderzeichen escaped
    assert datetime(2026, 10, 5, 12, 0) in values  # aware -> naive UTC
