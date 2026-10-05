"""Reine DNSSEC-Logik (F4 9, Logik Nr. 1–9) – ohne DB, ohne PowerDNS."""
import pytest

from app.services import dnssec_logic as L
from app.services.dnssec_logic import KeyView


def kv(id_, keytype="csk", *, active=True, published=True, flags=None, alg=13):
    if flags is None:
        flags = 256 if keytype == "zsk" else 257
    return KeyView(id=id_, keytype=keytype, flags=flags, active=active, published=published, algorithm_number=alg)


# --- Nr. 1 normalize_algorithm -------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("ecdsa256", "ECDSAP256SHA256"), ("ECDSAP256SHA256", "ECDSAP256SHA256"), (13, "ECDSAP256SHA256"),
    ("13", "ECDSAP256SHA256"), ("Ed25519", "ED25519"), ("ecdsa384", "ECDSAP384SHA384"), ("rsasha512", "RSASHA512"),
    (" 8 ", "RSASHA256"), ("ED448", "ED448"),
])
def test_normalize_algorithm_ok(value, expected):
    assert L.normalize_algorithm(value) == expected


@pytest.mark.parametrize("value", ["RSASHA1", "5", "foo", "", None, True])
def test_normalize_algorithm_rejected(value):
    with pytest.raises(ValueError) as e:
        L.normalize_algorithm(value)
    assert "wird nicht unterstützt" in str(e.value) and "ECDSAP256SHA256" in str(e.value)


def test_algorithm_number_and_label():
    assert L.algorithm_number("ECDSAP256SHA256") == 13
    assert L.algorithm_number("RSASHA1-NSEC3-SHA1") == 7
    assert L.algorithm_number("rsasha1") == 5
    assert L.algorithm_number("8") == 8 and L.algorithm_number(15) == 15
    assert L.algorithm_number("unbekannt") is None and L.algorithm_number(None) is None
    assert L.algorithm_label(13) == "ECDSAP256SHA256 (13)"
    assert L.algorithm_label(5) == "RSASHA1 (5)"


# --- Nr. 2 validate_bits ---------------------------------------------------------------------------
def test_validate_bits():
    assert L.validate_bits("RSASHA256", None) == 2048
    assert L.validate_bits("RSASHA512", 3072) == 3072
    with pytest.raises(ValueError, match="2048, 3072 oder 4096"):
        L.validate_bits("RSASHA256", 1024)
    with pytest.raises(ValueError, match="nur bei RSA"):
        L.validate_bits("ECDSAP256SHA256", 256)
    assert L.validate_bits("ECDSAP256SHA256", None) is None


# --- Nr. 3 parse_nsec3param ------------------------------------------------------------------------
def test_parse_nsec3param_ok():
    assert L.parse_nsec3param("1 0 0 -") == L.Nsec3Params(False, 0, "-")
    assert L.parse_nsec3param("1 0 1 ab") == L.Nsec3Params(False, 1, "ab")
    assert L.parse_nsec3param("1 1 5 AB") == L.Nsec3Params(True, 5, "ab")
    assert L.parse_nsec3param("") is None and L.parse_nsec3param(None) is None and L.parse_nsec3param("  ") is None
    assert L.Nsec3Params(True, 3, "-").to_param() == "1 1 3 -"


@pytest.mark.parametrize("value,needle", [
    ("2 0 0 -", "SHA-1"), ("1 2 0 -", "Flags"), ("1 0 51 -", "zwischen 0 und 50"), ("1 0 0 abc", "Salt"),
    ("1 0 0", "Format"), ("1 0 x -", "zwischen 0 und 50"),
])
def test_parse_nsec3param_rejected(value, needle):
    with pytest.raises(ValueError) as e:
        L.parse_nsec3param(value)
    assert needle in str(e.value)


def test_lax_parse_nsec3param_reads_anything_plausible():
    assert L.lax_parse_nsec3param("1 0 150 CAFE") == L.Nsec3Params(False, 150, "cafe")
    assert L.lax_parse_nsec3param("murks") is None and L.lax_parse_nsec3param("") is None


# --- Nr. 4 normalize_salt ----------------------------------------------------------------------------
def test_normalize_salt():
    assert L.normalize_salt("") == "-" and L.normalize_salt("-") == "-" and L.normalize_salt(None) == "-"
    assert L.normalize_salt("AB") == "ab"
    assert L.normalize_salt("a" * 510) == "a" * 510
    for bad in ("a" * 512, "abc", "zz", "a b"):
        with pytest.raises(ValueError, match="Hex-Zeichen"):
            L.normalize_salt(bad)


def test_validate_iterations():
    assert L.validate_iterations(0) == 0 and L.validate_iterations("7") == 7 and L.validate_iterations(50) == 50
    for bad in (-1, 51, "x", True, None, 1.5):
        with pytest.raises(ValueError, match="zwischen 0 und 50"):
            L.validate_iterations(bad)


# --- Nr. 5 Version / published ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("4.9.4", (4, 9, 4)), ("4.3.0-alpha1", (4, 3, 0)), ("5.0.1", (5, 0, 1)), ("4.9.4-1+deb12", (4, 9, 4)),
    ("4.8", (4, 8, 0)), ("", None), (None, None), ("git", None),
])
def test_parse_pdns_version(raw, expected):
    assert L.parse_pdns_version(raw) == expected


def test_supports_published():
    assert L.supports_published((4, 2, 0), []) is False
    assert L.supports_published((4, 3, 0), []) is True
    assert L.supports_published(None, []) is None
    assert L.supports_published(None, [{"id": 1, "published": True}]) is True
    assert L.supports_published(None, [{"id": 1, "active": True}]) is False


def test_key_view_from_pdns_uses_dnskey_flags():
    v = L.key_view_from_pdns({"id": 3, "keytype": "csk", "active": True, "dnskey": "256 3 13 AAAA",
                              "algorithm": "ECDSAP256SHA256"})
    assert v.flags == 256 and not v.is_sep and v.algorithm_number == 13 and v.published is None and v.published_eff
    v = L.key_view_from_pdns({"id": 4, "keytype": "KSK", "active": False, "published": False,
                              "algorithm": "RSASHA256"})
    assert v.flags is None and v.is_sep and v.algorithm_number == 8 and v.published is False


# --- Nr. 6 check_key_change -------------------------------------------------------------------------------
def test_check_key_change_matrix():
    single = [kv(1)]
    assert L.check_key_change(single, 1, active=False) == ["last_active_key", "last_active_sep", "last_published_sep"]
    assert L.check_key_change(single, 1, delete=True) == ["last_active_key", "last_active_sep", "last_published_sep"]
    two_active = [kv(1), kv(2)]
    assert L.check_key_change(two_active, 1, active=False) == []
    rollover = [kv(1), kv(2, active=False)]
    assert L.check_key_change(rollover, 1, active=False) == ["last_active_key", "last_active_sep",
                                                              "last_published_sep"]
    ksk_zsk = [kv(1, "ksk"), kv(2, "zsk")]
    assert L.check_key_change(ksk_zsk, 2, active=False) == []
    assert L.check_key_change(ksk_zsk, 2, delete=True) == []
    assert L.check_key_change(ksk_zsk, 1, active=False) == ["last_active_sep", "last_published_sep"]
    assert L.check_key_change(single, 1, published=False) == ["last_published_sep"]
    with_pub_replacement = [kv(1), kv(2)]
    assert L.check_key_change(with_pub_replacement, 1, published=False) == []
    assert L.check_key_change(rollover, 2, active=True) == []
    assert L.check_key_change(rollover, 2, published=True) == []
    assert L.check_key_change(rollover, 2, delete=True) == []


# --- Nr. 7 derive_rollover --------------------------------------------------------------------------------
def test_derive_rollover_phases():
    r = L.derive_rollover([kv(1)])
    assert r["sep"] == {"phase": "idle", "old_key_id": 1, "new_key_id": None, "next_action": "start"}
    assert r["zsk"] is None and r["algorithm_rollover"] is False
    r = L.derive_rollover([kv(1), kv(2, active=False)])
    assert r["sep"]["phase"] == "new_prepublished" and r["sep"]["old_key_id"] == 1 and r["sep"]["new_key_id"] == 2
    assert r["sep"]["next_action"] == "activate_new"
    r = L.derive_rollover([kv(1, active=False), kv(2)])
    assert r["sep"] == {"phase": "old_retired", "old_key_id": 1, "new_key_id": 2, "next_action": "delete_old"}
    r = L.derive_rollover([kv(1), kv(2)])
    assert r["sep"] == {"phase": "both_active", "old_key_id": 1, "new_key_id": 2, "next_action": "deactivate_old"}
    r = L.derive_rollover([kv(1, active=False)])
    assert r["sep"]["phase"] == "no_active" and r["sep"]["next_action"] is None
    r = L.derive_rollover([kv(1), kv(2), kv(3)])
    assert r["sep"]["phase"] == "complex" and r["sep"]["next_action"] == "manual"
    # inaktiver neuer Schluessel ohne Veroeffentlichung -> complex
    assert L.derive_rollover([kv(1), kv(2, active=False, published=False)])["sep"]["phase"] == "complex"


def test_derive_rollover_algorithm_and_zsk_track():
    r = L.derive_rollover([kv(1, alg=8), kv(2, alg=13, active=False)])
    assert r["algorithm_rollover"] is True and r["sep"]["phase"] == "complex"
    r = L.derive_rollover([kv(1), kv(2, active=True, published=False)])
    assert r["algorithm_rollover"] is True
    r = L.derive_rollover([kv(1, "ksk"), kv(2, "zsk"), kv(3, "zsk", active=False)])
    assert r["sep"]["phase"] == "idle" and r["zsk"]["phase"] == "new_prepublished"
    assert L.rollover_roles(r) == {2: "old", 3: "new"}
    assert L.rollover_roles(L.derive_rollover([kv(1)])) == {}


# --- Nr. 8 ds_status_map ------------------------------------------------------------------------------------
def test_ds_status_map_categories():
    keys = [
        kv(1, active=False, published=True),       # retired (id < max aktiver SEP)
        kv(2),                                      # current
        kv(3, active=True, published=False),        # unpublished_active
        kv(4, active=False, published=True),        # new
        kv(5, "zsk"),                               # not_sep
    ]
    assert L.ds_status_map(keys) == {1: "retired", 2: "current", 3: "unpublished_active", 4: "new", 5: "not_sep"}
    assert L.ds_status_map([kv(1, active=False)]) == {1: "inactive"}
    assert L.ds_status_map([kv(1), kv(5, active=False, published=False)]) == {1: "current", 5: "inactive"}


# --- Nr. 9 build_hints ----------------------------------------------------------------------------------------
def _hints(keys=None, meta=None, version="4.9.4", supports=True, peers=None, writable=True, raw_keys=None):
    keys = [kv(1)] if keys is None else keys
    meta = {"kind": "Native", "api_rectify": True, "nsec3param": "1 0 0 -"} if meta is None else meta
    nsec = L.nsec_info(meta, keys)
    rollover = L.derive_rollover(keys)
    return L.build_hints(keys=keys, meta=meta, nsec=nsec, supports_published=supports, version=version,
                         server="srv1", server_writable=writable, peers=peers, rollover=rollover, raw_keys=raw_keys)


def codes(hints):
    return [h["code"] for h in hints]


def test_build_hints_clean_zone_has_none():
    assert _hints() == []


def test_build_hints_iterations_and_salt():
    assert "nsec3_iterations_nonzero" not in codes(_hints(meta={"nsec3param": "1 0 0 -", "api_rectify": True}))
    h = _hints(meta={"nsec3param": "1 1 5 ab", "api_rectify": True})
    assert codes(h) == ["nsec3_iterations_nonzero", "nsec3_salt", "nsec3_optout"]
    assert h[0]["params"] == {"iterations": 5} and h[0]["level"] == "warning"
    h = _hints(meta={"nsec3param": "1 0 20 -", "api_rectify": True})
    assert codes(h) == ["nsec3_iterations_high"] and h[0]["level"] == "danger"


def test_build_hints_versions_and_state():
    assert codes(_hints(version="4.2.1", supports=False)) == ["published_unsupported"]
    assert _hints(version="4.2.1", supports=False)[0]["params"] == {"version": "4.2.1"}
    assert codes(_hints(version=None, supports=None)) == ["version_unknown"]
    assert codes(_hints(keys=[kv(1, active=False)])) == ["keys_inactive_only"]
    assert codes(_hints(meta={"presigned": True, "api_rectify": True})) == ["presigned_zone"]
    assert codes(_hints(meta={"kind": "Master", "api_rectify": False, "nsec3param": ""})) == [
        "api_rectify_off", "secondaries_serial"]
    assert codes(_hints(meta={"kind": "Slave", "api_rectify": True})) == ["secondary_zone_signing"]
    assert codes(_hints(writable=False)) == ["server_read_only"]
    assert _hints(writable=False)[0]["params"] == {"server": "srv1"}


def test_build_hints_keys():
    assert codes(_hints(keys=[kv(1, "ksk", active=False), kv(2, "zsk")])) == ["no_active_sep"]
    assert codes(_hints(keys=[kv(1), kv(2)])) == ["multiple_active_sep", "rollover_in_progress"]
    h = _hints(keys=[kv(1), kv(2, active=False)])
    assert h == [{"code": "rollover_in_progress", "level": "info", "params": {"track": "sep",
                                                                              "phase": "new_prepublished"}}]
    h = _hints(keys=[kv(1, alg=5)])
    assert codes(h) == ["deprecated_algorithm"] and h[0]["params"] == {"algorithm": "RSASHA1 (5)"}
    assert codes(_hints(keys=[kv(1), kv(2, published=False)])) == [
        "algorithm_rollover", "multiple_active_sep", "active_unpublished"]
    raw = [{"id": 1, "ds": ["1 13 1 abcd", "1 13 2 abcd"]}]
    assert codes(_hints(raw_keys=raw)) == ["sha1_ds"]


def test_build_hints_peers_sorted_by_level():
    peers = [
        {"server": "b", "state": "different_keys"}, {"server": "a", "state": "different_keys"},
        {"server": "c", "state": "unsigned"}, {"server": "d", "state": "unreachable"},
        {"server": "e", "state": "error"}, {"server": "f", "state": "same_keys"},
    ]
    h = _hints(peers=peers, version=None, supports=None)
    assert codes(h) == ["peers_divergent", "peers_unsigned", "peers_unreachable", "version_unknown"]
    assert h[0]["params"] == {"servers": "a, b"} and h[2]["params"] == {"servers": "d, e"}
    # unsignierte Zone: unsignierte Peers sind kein Problem
    assert "peers_unsigned" not in codes(_hints(keys=[], peers=peers))


# --- SOA-Helfer [D10] ------------------------------------------------------------------------------------------
def test_bump_soa_content():
    new, old, n = L.bump_soa_content("ns1.example.com. hostmaster.example.com. 2026100501 10800 3600 604800 3600")
    assert (old, n) == (2026100501, 2026100502)
    assert new == "ns1.example.com. hostmaster.example.com. 2026100502 10800 3600 604800 3600"
    assert L.bump_soa_content("a. b. 4294967295 1 2 3 4")[2] == 1  # Ueberlauf ueberspringt 0
    with pytest.raises(ValueError):
        L.bump_soa_content("kaputt")
    assert L.soa_serial("a. b. 7 1 2 3 4") == 7 and L.soa_serial("x") is None
    assert L.is_primary_kind("Master") and L.is_primary_kind("producer") and not L.is_primary_kind("Native")
    assert not L.is_primary_kind(None)


def test_nsec_info_and_warnings():
    assert L.nsec_info({"nsec3param": "1 1 3 ab", "nsec3narrow": True}, [kv(1)]) == {
        "mode": "nsec3", "nsec3param": "1 1 3 ab", "iterations": 3, "salt": "ab", "opt_out": True, "narrow": True}
    assert L.nsec_info({}, [kv(1)])["mode"] == "nsec"
    assert L.nsec_info({"nsec3param": "1 0 0 -"}, [])["mode"] is None
    assert L.nsec_warnings(L.Nsec3Params(False, 0, "-")) == []
    assert L.nsec_warnings(L.Nsec3Params(False, 11, "ab")) == ["nsec3_iterations_high", "nsec3_salt"]
    assert L.nsec_warnings(None) == []
