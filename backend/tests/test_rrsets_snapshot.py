"""Tests fuer services/rrsets.py (F1 9.2, Plan B.6/[D9]) und die Kleinst-Helfer names/timeutil."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core import names, timeutil
from app.services import rrsets
from app.services.rrsets import (
    canonical_content,
    content_key,
    index_rrsets,
    rrset_snapshot,
    snapshot_fingerprint,
    snapshot_from_rrset_payload,
    zone_fingerprint,
)

ORIGIN = "example.com."


def _zone(*rrs, name="example.com."):
    return {"name": name, "rrsets": list(rrs)}


def _rr(name, rtype, contents, ttl=300, comments=None, disabled=False):
    rr = {"name": name, "type": rtype, "ttl": ttl,
          "records": [{"content": c, "disabled": disabled} for c in contents]}
    if comments is not None:
        rr["comments"] = comments
    return rr


# ------------------------------------------------------------------ names / timeutil
@pytest.mark.parametrize("raw,expected", [
    ("Example.COM", "example.com."), (" example.com. ", "example.com."), ("", ""), (None, ""), (".", "."),
])
def test_normalize_names(raw, expected):
    assert names.normalize_zone_name(raw) == expected
    assert names.normalize_rr_name(raw) == expected


def test_timeutil_helpers():
    now = timeutil.utcnow()
    assert now.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds()) < 5
    assert timeutil.utcnow_naive().tzinfo is None
    aware = datetime(2026, 10, 5, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert timeutil.to_naive_utc(aware) == datetime(2026, 10, 5, 12, 0)
    naive = datetime(2026, 10, 5, 12, 0)
    assert timeutil.to_naive_utc(naive) is naive
    assert timeutil.to_naive_utc(None) is None
    assert timeutil.iso_utc(naive) == "2026-10-05T12:00:00+00:00"


# ------------------------------------------------------------------ rrset_snapshot
def test_rrset_snapshot_case_insensitive_sorted_with_comments():
    z = _zone(_rr("WWW.Example.com.", "a", ["192.0.2.9", "192.0.2.1"], comments=[{"content": "c", "account": "x"}]))
    snap = rrset_snapshot(z, "www.example.com", "A")
    assert snap == {
        "ttl": 300,
        "records": [{"content": "192.0.2.1", "disabled": False}, {"content": "192.0.2.9", "disabled": False}],
        "comments": [{"content": "c", "account": "x"}],
    }


def test_rrset_snapshot_missing_or_empty_is_none():
    z = _zone(_rr("a.example.com.", "A", []))
    assert rrset_snapshot(z, "a.example.com.", "A") is None
    assert rrset_snapshot(z, "b.example.com.", "A") is None
    assert rrset_snapshot(None, "a.example.com.", "A") is None
    assert rrset_snapshot({"rrsets": None}, "a.example.com.", "A") is None


def test_rrset_snapshot_accepts_rrset_list():
    lst = [_rr("a.example.com.", "TXT", ['"x"'])]
    assert rrset_snapshot(lst, "a.example.com.", "TXT")["records"][0]["content"] == '"x"'


def test_index_rrsets():
    z = _zone(_rr("a.example.com.", "A", ["192.0.2.1"]), _rr("A.example.com.", "MX", ["10 mx.example.com."]),
              _rr("e.example.com.", "A", []))
    idx = index_rrsets(z)
    assert set(idx) == {("a.example.com.", "A"), ("a.example.com.", "MX")}


# ------------------------------------------------------------------ Kanonisierung
@pytest.mark.parametrize("rtype,content,expected", [
    ("AAAA", "2001:DB8:0:0::1", "2001:db8::1"),
    ("HTTPS", "1 . alpn=h2", '1 . alpn="h2"'),
    ("TXT", "foo bar", '"foo" "bar"'),
    ("MX", "10 mail", "10 mail.example.com."),
    ("ALIAS", "lb.provider.net.", "lb.provider.net."),
    ("ALIAS", "lb", "lb.example.com."),
])
def test_canonical_content_examples(rtype, content, expected):
    assert canonical_content(rtype, content, ORIGIN) == expected


def test_canonical_content_errors_are_value_errors():
    with pytest.raises(ValueError):
        canonical_content("A", "kein-ip", ORIGIN)
    with pytest.raises(ValueError) as ei:
        canonical_content("TXT", '"' + "x" * 300 + '"', ORIGIN)
    assert "too long" in str(ei.value)
    with pytest.raises(ValueError):
        canonical_content("FOO", "x", ORIGIN)


def test_canonical_content_lua_uses_validator():
    assert canonical_content("LUA", 'a   "pickrandom({\'192.0.2.1\'})"', ORIGIN) == 'A "pickrandom({\'192.0.2.1\'})"'
    with pytest.raises(ValueError):
        canonical_content("LUA", 'A x()', ORIGIN)


def test_content_key_fallback_and_name_types():
    assert content_key("A", "  kaputt   wert ", ORIGIN) == "kaputt wert"
    assert content_key("CNAME", "Target.Example.COM.", ORIGIN) == "target.example.com."
    assert content_key("TXT", '"Foo"', ORIGIN) == '"Foo"'  # TXT bleibt case-sensitiv
    assert content_key("LUA", 'a  "x()"  ', ORIGIN) == 'A "x()"'  # tolerant, wirft nie
    assert content_key("LUA", "kaputt", ORIGIN) == "kaputt"


# ------------------------------------------------------------------ Fingerprints
def _snap(contents, ttl=300, disabled=False, comments=None):
    return rrset_snapshot(_zone(_rr("x.example.com.", "T", contents, ttl=ttl, disabled=disabled,
                                    comments=comments)), "x.example.com.", "T")


def test_snapshot_fingerprint_order_independent():
    a = {"ttl": 60, "records": [{"content": "192.0.2.1", "disabled": False}, {"content": "192.0.2.2", "disabled": False}]}
    b = {"ttl": 60, "records": [{"content": "192.0.2.2", "disabled": False}, {"content": "192.0.2.1", "disabled": False}]}
    fa = snapshot_fingerprint(a, "A", ORIGIN)
    assert fa == snapshot_fingerprint(b, "A", ORIGIN)
    assert len(fa) == 32 and int(fa, 16) >= 0


@pytest.mark.parametrize("rtype,c1,c2", [
    ("AAAA", "2001:DB8:0:0::1", "2001:db8::1"),
    ("HTTPS", "1 . alpn=h2", '1 . alpn="h2"'),
    ("CNAME", "Target.Example.com.", "target.example.com."),
])
def test_snapshot_fingerprint_equivalent_spellings(rtype, c1, c2):
    s1 = {"ttl": 300, "records": [{"content": c1, "disabled": False}]}
    s2 = {"ttl": 300, "records": [{"content": c2, "disabled": False}]}
    assert snapshot_fingerprint(s1, rtype, ORIGIN) == snapshot_fingerprint(s2, rtype, ORIGIN)


def test_snapshot_fingerprint_differences():
    base = {"ttl": 300, "records": [{"content": "192.0.2.1", "disabled": False}]}
    other_ttl = {"ttl": 301, "records": [{"content": "192.0.2.1", "disabled": False}]}
    other_dis = {"ttl": 300, "records": [{"content": "192.0.2.1", "disabled": True}]}
    with_comment = dict(base, comments=[{"content": "neu", "account": ""}])
    f = snapshot_fingerprint(base, "A", ORIGIN)
    assert f != snapshot_fingerprint(other_ttl, "A", ORIGIN)
    assert f != snapshot_fingerprint(other_dis, "A", ORIGIN)
    assert f == snapshot_fingerprint(with_comment, "A", ORIGIN)  # Kommentare gehen nicht ein
    assert snapshot_fingerprint(None, "A", ORIGIN) == "absent"


def test_snapshot_from_rrset_payload():
    assert snapshot_from_rrset_payload({"name": "a.", "type": "A", "changetype": "DELETE"}) is None
    assert snapshot_from_rrset_payload({"name": "a.", "type": "A", "changetype": "REPLACE", "ttl": 60, "records": []}) is None
    snap = snapshot_from_rrset_payload({"name": "a.", "type": "A", "changetype": "REPLACE", "ttl": 60,
                                        "records": [{"content": "192.0.2.2"}, {"content": "192.0.2.1", "disabled": True}]})
    assert snap["ttl"] == 60 and [r["content"] for r in snap["records"]] == ["192.0.2.1", "192.0.2.2"]


def test_zone_fingerprint_ignores_soa_dnssec_comments_and_order():
    z1 = _zone(
        _rr("example.com.", "SOA", ["ns1.example.com. h.example.com. 2026100501 10800 3600 604800 3600"]),
        _rr("www.example.com.", "A", ["192.0.2.1", "192.0.2.2"]),
        _rr("example.com.", "MX", ["10 mail.example.com."]),
        _rr("example.com.", "DNSKEY", ["257 3 13 abc="]),
    )
    z2 = _zone(
        _rr("example.com.", "MX", ["10 Mail.Example.com."], comments=[{"content": "x", "account": ""}]),
        _rr("www.example.com.", "A", ["192.0.2.2", "192.0.2.1"]),
        _rr("example.com.", "SOA", ["ns1.example.com. h.example.com. 2026100599 10800 3600 604800 3600"]),
    )
    assert zone_fingerprint(z1) == zone_fingerprint(z2)
    z3 = _zone(_rr("www.example.com.", "A", ["192.0.2.1"]), _rr("example.com.", "MX", ["10 mail.example.com."]))
    assert zone_fingerprint(z1) != zone_fingerprint(z3)
    # SOA einbeziehen -> Serial-Unterschied sichtbar
    assert zone_fingerprint(z1, exclude_types=("DNSKEY",)) != zone_fingerprint(z2, exclude_types=("DNSKEY",))
    assert len(zone_fingerprint(None)) == 32


def test_constants():
    assert rrsets.PASSTHROUGH_TYPES == {"LUA", "ALIAS"}
    assert "RRSIG" in rrsets.DNSSEC_AUTO_TYPES and "TYPE65534" in rrsets.DNSSEC_AUTO_TYPES
    assert "MX" in rrsets.NAME_CONTENT_TYPES
