"""Reine DynDNS-Helfer (F9 9 test_dyndns_parse): Hostnamen, IPs, Credentials, Query-Geheimnisse, Cross-Site."""
from __future__ import annotations

import base64
import ipaddress
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, QueryParams

from app.core.config import settings
from app.core.request_origin import is_cross_site_browser_request
from app.services import dyndns
from app.services.dyndns import DynDnsError, normalize_hostname, parse_ips


def _req(headers=None, query=""):
    return SimpleNamespace(headers=Headers(headers or {}), query_params=QueryParams(query))


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


# ---------------------------------------------------------------------------------------------------------------------
# normalize_hostname
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("Home.Example.COM", "home.example.com."),
    ("home.example.com.", "home.example.com."),
    ("  home.example.com  ", "home.example.com."),
    ("bücher.example", "xn--bcher-kva.example."),
    ("a-b.c-d.example", "a-b.c-d.example."),
])
def test_normalize_hostname_ok(raw, expected):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "a..b", "localhost", "-a.example.com", "a-.example.com", "a_b.example.com",
                                 "x" * 64 + ".example.com", ".".join(["a" * 60] * 5)])
def test_normalize_hostname_invalid(raw):
    with pytest.raises(ValueError):
        normalize_hostname(raw)


def test_normalize_hostname_wildcard():
    with pytest.raises(ValueError, match="wildcard"):
        normalize_hostname("*.example.com")


# ---------------------------------------------------------------------------------------------------------------------
# parse_ips / check_ip_allowed
# ---------------------------------------------------------------------------------------------------------------------
def test_parse_v4():
    ips = parse_ips("203.0.113.5", None, None, "198.51.100.9", allow_private=False)
    assert str(ips.v4) == "203.0.113.5" and ips.v6 is None and ips.source == "param"


def test_parse_v4_and_v6_comma():
    ips = parse_ips("203.0.113.5,2001:db8::5", None, None, None, allow_private=False)
    assert ips.values == ["203.0.113.5", "2001:db8::5"]


def test_parse_separate_params():
    ips = parse_ips(None, "203.0.113.5", "2001:DB8:0:0::5", None, allow_private=False)
    assert ips.values == ["203.0.113.5", "2001:db8::5"]


def test_placeholders_fall_back_to_client_ip():
    ips = parse_ips("<ipaddr>,<ip6addr>", None, None, "198.51.100.9", allow_private=False)
    assert str(ips.v4) == "198.51.100.9" and ips.source == "client_ip"


def test_leading_comma_v6_only():
    ips = parse_ips(",2001:db8::1", None, None, "198.51.100.9", allow_private=False)
    assert ips.v4 is None and str(ips.v6) == "2001:db8::1" and ips.source == "param"


def test_two_different_v4_is_badip():
    with pytest.raises(DynDnsError, match="Mehrere IPv4"):
        parse_ips("203.0.113.5,203.0.113.6", None, None, None, allow_private=False)


def test_same_v4_twice_is_ok():
    assert str(parse_ips("203.0.113.5", "203.0.113.5", None, None, allow_private=False).v4) == "203.0.113.5"


def test_too_many_values():
    with pytest.raises(DynDnsError):
        parse_ips("203.0.113.5,203.0.113.5,203.0.113.5,203.0.113.5,203.0.113.5", None, None, None,
                  allow_private=False)


@pytest.mark.parametrize("ip", ["fe80::1", "127.0.0.1", "224.0.0.1", "0.0.0.0", "::", "::1", "255.255.255.255",
                                "240.0.0.1", "169.254.1.1"])
def test_forbidden_ips(ip):
    with pytest.raises(DynDnsError) as ei:
        parse_ips(ip, None, None, None, allow_private=True)
    assert ei.value.code == "badip"


@pytest.mark.parametrize("ip", ["10.0.0.1", "172.16.5.4", "192.168.1.1", "100.64.0.1", "fd00::1"])
def test_private_only_with_switch(ip):
    with pytest.raises(DynDnsError, match="privat"):
        parse_ips(ip, None, None, None, allow_private=False)
    assert parse_ips(ip, None, None, None, allow_private=True).values == [str(ipaddress.ip_address(ip))]


@pytest.mark.parametrize("ip", ["203.0.113.5", "2001:db8::5", "192.0.2.1", "198.51.100.7"])
def test_documentation_nets_allowed(ip):
    assert parse_ips(ip, None, None, None, allow_private=False).values == [ip]


def test_ipv4_mapped_becomes_v4():
    ips = parse_ips("::ffff:203.0.113.5", None, None, None, allow_private=False)
    assert str(ips.v4) == "203.0.113.5" and ips.v6 is None


def test_invalid_ip_text():
    with pytest.raises(DynDnsError, match="Ungueltige IP-Adresse: 1.2.3"):
        parse_ips("1.2.3", None, None, None, allow_private=False)


def test_private_client_ip_mentions_proxy_hint():
    with pytest.raises(DynDnsError, match="TRUST_PROXY_HEADERS") as ei:
        parse_ips(None, None, None, "172.18.0.1", allow_private=False)
    assert "172.18.0.1" in ei.value.detail


def test_missing_client_ip():
    with pytest.raises(DynDnsError, match="Client-IP unbekannt"):
        parse_ips(None, None, None, None, allow_private=False)


def test_scope_id_rejected():
    with pytest.raises(DynDnsError):
        dyndns.check_ip_allowed(ipaddress.ip_address("2001:db8::1%eth0"), allow_private=True)


# ---------------------------------------------------------------------------------------------------------------------
# Credentials und Query
# ---------------------------------------------------------------------------------------------------------------------
TOKEN = dyndns.TOKEN_PREFIX + "x" * 43


def test_basic_password():
    assert dyndns.extract_credentials(_req(_basic("dyndns", TOKEN))) == TOKEN


def test_basic_token_in_user_field():
    assert dyndns.extract_credentials(_req(_basic(TOKEN, ""))) == TOKEN
    assert dyndns.extract_credentials(_req(_basic(TOKEN, "irgendwas"))) == TOKEN


def test_basic_without_prefix_returns_password():
    assert dyndns.extract_credentials(_req(_basic("user", "geheim"))) == "geheim"


def test_bearer():
    assert dyndns.extract_credentials(_req({"Authorization": f"Bearer {TOKEN}"})) == TOKEN


@pytest.mark.parametrize("hdr", [None, "Basic !!!kein-base64", "Basic " + base64.b64encode(b"ohne-doppelpunkt").decode(),
                                 "Digest abc", "Bearer "])
def test_broken_or_missing_credentials(hdr):
    assert dyndns.extract_credentials(_req({"Authorization": hdr} if hdr else {})) is None


def test_basic_latin1_fallback():
    raw = base64.b64encode("b\xfcro:".encode("latin-1") + TOKEN.encode()).decode()
    assert dyndns.extract_credentials(_req({"Authorization": f"Basic {raw}"})) == TOKEN


def test_query_secret_values():
    assert dyndns.query_secret_values(_req(query="hostname=a.example.com&myip=203.0.113.5")) is None
    assert dyndns.query_secret_values(_req(query="hostname=a&password=geheim")) == ["geheim"]
    assert dyndns.query_secret_values(_req(query="hostname=a&PASS=")) == [""]
    assert dyndns.query_secret_values(_req(query=f"hostname=a&x={TOKEN}")) == [TOKEN]
    assert dyndns.query_secret_values(_req(query="api_key=1&token=2")) == ["1", "2"]


# ---------------------------------------------------------------------------------------------------------------------
# Cross-Site-Erkennung (Ergaenzung zu test_request_origin.py)
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("headers, expected", [
    ({"Sec-Fetch-Site": "cross-site"}, True),
    ({"Sec-Fetch-Site": "same-site"}, True),
    ({"Sec-Fetch-Site": "none"}, False),
    ({"Sec-Fetch-Site": "same-origin"}, False),
    ({}, False),
    ({"Origin": "https://evil.example", "Host": "dns.example.com"}, True),
    ({"Origin": "null", "Host": "dns.example.com"}, True),
    ({"Origin": "https://dns.example.com", "Host": "dns.example.com"}, False),
])
def test_cross_site(headers, expected, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    assert is_cross_site_browser_request(_req(headers)) is expected


# ---------------------------------------------------------------------------------------------------------------------
# Sonstiges
# ---------------------------------------------------------------------------------------------------------------------
def test_worst_order():
    assert dyndns.worst(["good", "nochg"]) == "good"
    assert dyndns.worst(["nochg", "nohost", "911"]) == "911"
    assert dyndns.worst(["badip", "dnserr"]) == "dnserr"
    assert dyndns.worst([]) == "nochg"


def test_token_format():
    plain = dyndns.generate_token()
    assert plain.startswith(dyndns.TOKEN_PREFIX) and len(plain) >= 50
    assert len(dyndns.token_prefix(plain)) == 16
    assert dyndns.hash_token(plain) != plain and len(dyndns.hash_token(plain)) == 64


def test_rate_state_helpers():
    dyndns.reset_state_for_tests()
    try:
        for _ in range(dyndns.TOKEN_LIMIT):
            assert dyndns.token_rate_state(9) == (None, 0)
        code, retry = dyndns.token_rate_state(9)
        assert code == "911" and retry >= 1
        for _ in range(dyndns.IP_REQUEST_LIMIT):
            assert dyndns.ip_lock_retry_after("203.0.113.0") is None
        assert dyndns.ip_lock_retry_after("203.0.113.0") >= 1
        assert dyndns.ip_lock_retry_after("203.0.113.1") is None
    finally:
        dyndns.reset_state_for_tests()
