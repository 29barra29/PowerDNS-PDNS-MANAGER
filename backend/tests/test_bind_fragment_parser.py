"""BIND-Fragment-Parser des Text-Editors (F1 9.1, ``services/bind_fragment.py``)."""
from __future__ import annotations

import pytest

from app.services.bind_fragment import ISSUE_MESSAGES, issue, parse_bind_fragment

Z = "example.com."


def parse(text: str, **kw):
    return parse_bind_fragment(Z, text, **kw)


def codes(res, severity=None):
    return [i["code"] for i in res.issues if severity is None or i["severity"] == severity]


def only(res):
    assert not res.has_errors, res.issues
    assert len(res.rrsets) == 1, res.rrsets
    return next(iter(res.rrsets.values()))


# --- Nr. 1 ------------------------------------------------------------------------------------------------------
def test_simple_line():
    rr = only(parse("www 300 IN A 192.0.2.1"))
    assert (rr.name, rr.type, rr.ttl) == ("www.example.com.", "A", 300)
    assert [(v.content, v.disabled, v.line) for v in rr.values] == [("192.0.2.1", False, 1)]


# --- Nr. 2 ------------------------------------------------------------------------------------------------------
def test_apex_mx_relative_target_becomes_absolute():
    rr = only(parse("@ IN MX 10 mail"))
    assert rr.name == "example.com." and rr.ttl is None
    assert rr.values[0].content == "10 mail.example.com."


# --- Nr. 3 ------------------------------------------------------------------------------------------------------
def test_dollar_ttl_counts_as_explicit():
    rr = only(parse("$TTL 1h\nwww A 192.0.2.1"))
    assert rr.ttl == 3600


# --- Nr. 4 ------------------------------------------------------------------------------------------------------
def test_origin_relative_and_outside_zone():
    rr = only(parse("$ORIGIN sub.example.com.\nwww A 192.0.2.2"))
    assert rr.name == "www.sub.example.com."
    rel = only(parse("$ORIGIN sub\nwww A 192.0.2.2"))
    assert rel.name == "www.sub.example.com."
    res = parse("$ORIGIN other.org.\nwww A 192.0.2.2")
    assert codes(res) == ["outside_zone"]
    assert res.issues[0]["line"] == 1 and res.issues[0]["params"]["zone"] == Z
    # nach dem abgelehnten $ORIGIN gilt weiter der alte Origin
    assert ("www.example.com.", "A") in res.rrsets


def test_owner_outside_zone():
    res = parse("www.other.org. A 192.0.2.1")
    assert codes(res) == ["outside_zone"] and res.issues[0]["name"] == "www.other.org."


# --- Nr. 5 ------------------------------------------------------------------------------------------------------
def test_ttl_and_class_in_any_order():
    rr = only(parse("www IN 300 A 192.0.2.1"))
    assert rr.ttl == 300 and rr.values[0].content == "192.0.2.1"


# --- Nr. 6 ------------------------------------------------------------------------------------------------------
def test_leading_whitespace_reuses_owner():
    res = parse("www 300 A 192.0.2.1\n    300 A 192.0.2.2\n\tTXT \"hallo\"")
    assert not res.has_errors
    assert [v.content for v in res.rrsets[("www.example.com.", "A")].values] == ["192.0.2.1", "192.0.2.2"]
    assert res.rrsets[("www.example.com.", "TXT")].values[0].content == '"hallo"'


def test_leading_whitespace_first_line_is_owner_missing():
    res = parse("  300 A 192.0.2.1")
    assert codes(res) == ["owner_missing"] and res.issues[0]["line"] == 1


# --- Nr. 7 ------------------------------------------------------------------------------------------------------
def test_disabled_marker_and_comments():
    text = "; normaler Kommentar\n;@disabled old 60 IN A 192.0.2.9\nnote TXT \"a;b\" ; Kommentar dahinter"
    res = parse(text)
    assert not res.has_errors
    old = res.rrsets[("old.example.com.", "A")]
    assert old.values[0].disabled is True and old.ttl == 60 and old.values[0].line == 2
    assert res.rrsets[("note.example.com.", "TXT")].values[0].content == '"a;b"'


def test_disabled_marker_on_directive_is_error():
    res = parse(";@disabled $TTL 300\nwww A 192.0.2.1")
    assert codes(res) == ["parse_error"] and res.issues[0]["line"] == 1


# --- Nr. 8 ------------------------------------------------------------------------------------------------------
def test_multiline_parentheses_keep_start_line():
    text = 'www A 192.0.2.1\ntxt TXT ( "teil1"\n  "teil2"\n  "teil3" )\nbad A 999.1.1.1'
    res = parse(text)
    txt = res.rrsets[("txt.example.com.", "TXT")]
    assert txt.values[0].content == '"teil1" "teil2" "teil3"' and txt.values[0].line == 2
    assert codes(res) == ["parse_error"] and res.issues[0]["line"] == 5


# --- Nr. 9 ------------------------------------------------------------------------------------------------------
def test_unclosed_parenthesis_reports_start_line():
    res = parse('www A 192.0.2.1\ntxt TXT ( "a"\n "b"')
    assert codes(res) == ["parse_error"]
    assert res.issues[0]["line"] == 2 and "Klammer" in res.issues[0]["message"]


# --- Nr. 10 -----------------------------------------------------------------------------------------------------
def test_soa_dnssec_and_unknown_types():
    res = parse("@ SOA ns1 hostmaster 1 2 3 4 5")
    assert codes(res) == ["soa_forbidden"]
    res = parse("www RRSIG A 13 3 300 20260101000000 20250101000000 1 example.com. abc=\nwww A 192.0.2.1")
    assert codes(res, "warning") == ["dnssec_skipped"] and not res.has_errors
    assert list(res.rrsets) == [("www.example.com.", "A")]
    for t in ("TYPE123", "FOO"):
        r = parse(f"www {t} 1.2.3.4")
        assert codes(r) == ["type_unknown"] and r.issues[0]["params"]["type"] == t


# --- Nr. 11 -----------------------------------------------------------------------------------------------------
def test_class_and_directives_unsupported():
    assert codes(parse("www CH A 1.2.3.4")) == ["class_unsupported"]
    res = parse("$INCLUDE x\n$GENERATE 1-10 host$ A 192.0.2.$\nwww A 192.0.2.1")
    assert codes(res) == ["directive_unsupported", "directive_unsupported"]
    assert [i["params"]["directive"] for i in res.issues] == ["$INCLUDE", "$GENERATE"]
    assert [i["line"] for i in res.issues] == [1, 2]


# --- Nr. 12 -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ttl", [30, 700000])
def test_ttl_range(ttl):
    res = parse(f"www {ttl} A 192.0.2.1")
    assert codes(res) == ["ttl_range"] and res.issues[0]["params"]["ttl"] == ttl


def test_dollar_ttl_range_and_invalid_ttl():
    assert codes(parse("$TTL 10\nwww A 192.0.2.1")) == ["ttl_range"]
    assert codes(parse("www 5x A 192.0.2.1")) == ["parse_error"]


# --- Nr. 13 -----------------------------------------------------------------------------------------------------
def test_txt_string_too_long():
    res = parse('t TXT "' + "x" * 300 + '"')
    assert codes(res) == ["parse_error"]
    assert "string too long" in res.issues[0]["message"]
    assert res.issues[0]["message"].startswith("Zeile 1: Inhalt für TXT ungültig")


# --- Nr. 14 -----------------------------------------------------------------------------------------------------
def test_duplicate_value_and_ttl_conflict():
    res = parse("www 300 A 192.0.2.1\nwww 600 A 192.0.2.2\nwww A 192.0.2.1")
    assert not res.has_errors
    assert codes(res, "warning") == ["ttl_conflict", "duplicate_value"]
    rr = res.rrsets[("www.example.com.", "A")]
    assert rr.ttl == 300 and [v.content for v in rr.values] == ["192.0.2.1", "192.0.2.2"]
    conflict = res.issues[0]
    assert conflict["params"] == {"name": "www.example.com.", "type": "A", "ttl": 300} and conflict["line"] == 2
    dup = res.issues[1]
    assert dup["line"] == 3 and dup["params"]["content"] == "192.0.2.1"


def test_aaaa_duplicate_detected_canonically():
    res = parse("v6 AAAA 2001:DB8:0:0::1\nv6 AAAA 2001:db8::1")
    assert codes(res, "warning") == ["duplicate_value"]
    assert res.rrsets[("v6.example.com.", "AAAA")].values[0].content == "2001:db8::1"


# --- Nr. 15 -----------------------------------------------------------------------------------------------------
def test_lua_content_verbatim():
    rr = only(parse("geo 300 IN LUA A \"ifportup(443, {'192.0.2.1'})\""))
    assert rr.type == "LUA" and rr.values[0].content == "A \"ifportup(443, {'192.0.2.1'})\""


def test_invalid_lua_is_parse_error():
    res = parse('geo LUA A "ifportup(443"')
    assert codes(res) == ["parse_error"] and "Inhalt für LUA ungültig" in res.issues[0]["message"]


# --- Nr. 16 -----------------------------------------------------------------------------------------------------
def test_alias_passthrough():
    rr = only(parse("@ ALIAS lb.provider.net."))
    assert rr.values[0].content == "lb.provider.net."
    rel = only(parse("@ ALIAS lb"))
    assert rel.values[0].content == "lb.example.com."


# --- Nr. 17 -----------------------------------------------------------------------------------------------------
def test_too_many_lines_and_empty_input():
    res = parse("\n".join(["www A 192.0.2.1"] * 20_001))
    assert codes(res) == ["too_many_lines"] and res.issues[0]["params"]["max"] == 20_000
    assert codes(parse("; nur Kommentar\n\n   ; noch einer")) == ["empty_input"]
    assert codes(parse("www RRSIG A 13 3 300 20260101000000 20250101000000 1 example.com. abc=")) == [
        "dnssec_skipped", "empty_input"]


# --- Nr. 18 -----------------------------------------------------------------------------------------------------
def test_idn_owner_to_punycode():
    rr = only(parse("bücher A 192.0.2.1"))
    assert rr.name == "xn--bcher-kva.example.com."


# --- Sonstiges --------------------------------------------------------------------------------------------------
def test_missing_type_and_content():
    assert codes(parse("www 300")) == ["parse_error"]
    res = parse("www A")
    assert codes(res) == ["parse_error"] and "Inhalt fehlt" in res.issues[0]["message"]


def test_all_errors_collected_with_line_numbers():
    res = parse("a A 192.0.2.1\nb A nope\nc CH A 1.2.3.4\nd 10 A 192.0.2.4\ne A 192.0.2.5")
    assert [(i["code"], i["line"]) for i in res.issues] == [("parse_error", 2), ("class_unsupported", 3),
                                                           ("ttl_range", 4)]
    assert set(res.rrsets) == {("a.example.com.", "A"), ("e.example.com.", "A")}


def test_issue_messages_have_all_codes_and_params():
    i = issue("value_missing", name="www.example.com.", type="A", content="192.0.2.9")
    assert i["message"] == "www.example.com. A: Wert 192.0.2.9 ist nicht (mehr) vorhanden. Bitte Zone neu laden."
    assert i["params"] == {"name": "www.example.com.", "type": "A", "content": "192.0.2.9"}
    p = issue("parse_error", line=4, message="Inhalt fehlt.")
    assert p["message"] == "Zeile 4: Inhalt fehlt." and p["line"] == 4 and p["params"] == {"message": "Inhalt fehlt."}
    assert {"parse_error", "directive_unsupported", "class_unsupported", "type_unknown", "soa_forbidden",
            "dnssec_skipped", "outside_zone", "ttl_range", "owner_missing", "too_many_lines", "empty_input",
            "ttl_conflict", "duplicate_value", "value_missing", "rrset_missing", "cname_conflict", "cname_multi",
            "apex_cname", "apex_ns_delete", "lua_forbidden", "too_many_changes", "dnskey_managed",
            "scope_invalid"} <= set(ISSUE_MESSAGES)
