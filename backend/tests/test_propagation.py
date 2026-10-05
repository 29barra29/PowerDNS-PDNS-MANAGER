"""Propagations-Check (F12 9.1 P1-P13) plus Bauplan-Faelle [D9] getrennte Backends und [D14] Referenz-Timeout.

Ohne Datenbank und ohne Internet: Panel-Server sind ``FakePowerDNSClient`` (eigener Zonenstand je Server), DNS-Abfragen
werden gepatcht – ausser in P12, das einen lokalen UDP-DNS-Server auf 127.0.0.1 benutzt.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.services import propagation as prop
from app.services import rrsets
from app.services.pdns_client import PowerDNSAPIError, pdns_manager
from authfakes import FakeSession, make_user
from fakes.pdns import FakePowerDNSClient, make_zone, rr

ZONE = "example.com."
REF_SERIAL = 2026100501
REF_EDITED = 2026100503


@pytest.fixture(autouse=True)
def _reset():
    prop.reset_for_tests()
    yield
    prop.reset_for_tests()


def zone_json(*, serial=REF_SERIAL, edited=REF_EDITED, kind="Native", extra=(), notified=None, www="192.0.2.10"):
    z = make_zone(ZONE, [
        rr(ZONE, "NS", "ns1.example.com.", "ns.extern.net."),
        rr("ns1.example.com.", "A", "192.0.2.53"),
        rr("www.example.com.", "A", www),
        *extra,
    ], serial=serial, kind=kind)
    z["edited_serial"] = edited
    if notified is not None:
        z["notified_serial"] = notified
    return z


def servers(monkeypatch, **zones):
    """``servers(monkeypatch, ns1=zone, ns2=zone_or_None)`` -> dict Name -> FakePowerDNSClient."""
    clients = {}
    for name, z in zones.items():
        clients[name] = FakePowerDNSClient(name, [z] if z is not None else [])
    monkeypatch.setattr(pdns_manager, "clients", clients)
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    return clients


class DnsFake:
    """Ersetzt ``propagation.query_dns`` und ``propagation.resolve_ns_addresses``."""

    def __init__(self, monkeypatch, serial=REF_EDITED, *, aa=True, records=None, ns_ips=None, delay=0.0,
                 per_ip=None):
        self.serial = serial
        self.aa = aa
        self.records = records or {}  # (name, TYPE) -> [DNS-Texte]
        self.ns_ips = ns_ips if ns_ips is not None else {"ns.extern.net.": ["198.51.100.7"]}
        self.delay = delay
        self.per_ip = per_ip or {}  # ip -> serial
        self.queries: list[tuple] = []
        self.resolved: list[str] = []
        self.cancelled = 0
        monkeypatch.setattr(prop, "query_dns", self.query)
        monkeypatch.setattr(prop, "resolve_ns_addresses", self.resolve)

    async def query(self, name, rdtype, server, timeout=3.0, *, recursion=False, origin=None, port=53):
        self.queries.append((name, rdtype, server, recursion, time.monotonic()))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            if self.delay and asyncio.current_task().cancelling():
                self.cancelled += 1
        if rdtype == "SOA":
            return prop.DnsAnswer(rcode="NOERROR", aa=self.aa, soa_serial=self.per_ip.get(server, self.serial),
                                  ttl=300, latency_ms=3)
        texts = self.records.get((name, rdtype), [])
        vals = sorted(rrsets.content_key(rdtype, t, origin or name) for t in texts)
        return prop.DnsAnswer(rcode="NOERROR", aa=self.aa, values=vals, ttl=60, latency_ms=3)

    async def resolve(self, ns_name, *, ipv6, timeout):
        self.resolved.append(ns_name)
        ips = self.ns_ips.get(ns_name, [])
        return (list(ips), None) if ips else ([], "ns_unresolvable")


class Forbidden:
    def __init__(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("externe DNS-Abfrage trotz deaktivierter Einstellung")

        monkeypatch.setattr(prop, "query_dns", boom)
        monkeypatch.setattr(prop, "resolve_ns_addresses", boom)


ON = prop.PropagationSettings(enabled=True, check_authoritative=True, ipv6=False, resolvers=("1.1.1.1",))
OFF = prop.PropagationSettings()


async def check(settings=OFF, *, server="ns1", record=None, rtype=None, content=False, writable=None, user=1,
                admin=True):
    return await prop.check_zone(user_id=user, is_admin=admin, server_name=server, zone_norm=ZONE,
                                 record_fqdn=record, rtype=rtype, compare_content_flag=content, settings=settings,
                                 writable_servers=writable)


def by(res, source, target=None):
    rows = [s for s in res.sources if s.source == source and (target is None or s.target == target)]
    assert len(rows) == 1, [s.model_dump() for s in res.sources]
    return rows[0]


# ------------------------------------------------------------------------------------------------ P1-P4 (rein)
def test_p1_serial_relation():
    assert prop.serial_relation(5, 5) == "equal"
    assert prop.serial_relation(100, 101) == "behind"
    assert prop.serial_relation(101, 100) == "ahead"
    assert prop.serial_relation(4294967295, 1) == "behind"
    assert prop.serial_relation(1, 4294967295) == "ahead"


def test_p2_validate_resolver_list():
    assert prop.validate_resolver_list(["1.1.1.1", " 8.8.8.8 ", "", "1.1.1.1", "2606:4700:4700:0::1111", "10.0.0.53"]) \
        == ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111", "10.0.0.53"]
    with pytest.raises(ValueError) as e:
        prop.validate_resolver_list(["dns.google"])
    assert str(e.value) == "Ungültige Resolver-Adresse: dns.google (nur IPv4/IPv6-Adressen erlaubt)."
    for bad in ("127.0.0.1", "224.0.0.1", "fe80::1", "0.0.0.0", "::ffff:127.0.0.1"):
        with pytest.raises(ValueError) as e:
            prop.validate_resolver_list([bad])
        assert str(e.value).startswith("Loopback-, Multicast-, Link-Local- und unspezifische Adressen"), bad
    with pytest.raises(ValueError) as e:
        prop.validate_resolver_list([f"192.0.2.{i}" for i in range(1, 12)])
    assert str(e.value) == "Höchstens 10 Resolver erlaubt."
    assert prop.validate_resolver_list([f"192.0.2.{i}" for i in range(1, 11)] + ["192.0.2.1"])[-1] == "192.0.2.10"


def test_p3_resolve_record_name():
    assert prop.resolve_record_name("@", ZONE) == ZONE
    assert prop.resolve_record_name("", ZONE) == ZONE
    assert prop.resolve_record_name("www", ZONE) == "www.example.com."
    assert prop.resolve_record_name("www.example.com.", ZONE) == "www.example.com."
    assert prop.resolve_record_name("WWW.Example.COM.", ZONE) == "www.example.com."
    with pytest.raises(ValueError) as e:
        prop.resolve_record_name("www.other.org.", ZONE)
    assert str(e.value) == "Der Name www.other.org. liegt nicht in der Zone example.com."
    with pytest.raises(ValueError):
        prop.resolve_record_name("evilexample.com.", ZONE)
    with pytest.raises(ValueError):
        prop.resolve_record_name("a..b", ZONE)


def test_p4_target_allowed():
    assert prop.target_allowed("2001:db8::1", False) == "ipv6_disabled"
    assert prop.target_allowed("2001:db8::1", True) is None
    assert prop.target_allowed("127.0.0.1", False) == "address_not_allowed"
    assert prop.target_allowed("169.254.1.1", False) == "address_not_allowed"
    assert prop.target_allowed("240.0.0.1", False) == "address_not_allowed"
    assert prop.target_allowed("::1", True) == "address_not_allowed"
    assert prop.target_allowed("kein-ip", False) == "address_not_allowed"
    assert prop.target_allowed("10.1.2.3", False) is None
    assert prop.target_allowed("fd00::53", True) is None


# ------------------------------------------------------------------------------------------------ P5/P6
async def test_p5_all_in_sync(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json())
    dns_ = DnsFake(monkeypatch)
    res = await check(ON)
    assert res.expected_serial == REF_EDITED and res.reference_serial_raw == REF_SERIAL
    assert res.sources[0].source == "ns1" and res.sources[0].is_reference
    assert [s.kind for s in res.sources] == ["panel-api", "panel-api", "authoritative", "authoritative", "resolver"]
    assert all(s.status == "ok" for s in res.sources), [s.model_dump() for s in res.sources]
    assert res.summary.in_sync and res.summary.total == 5 and not res.timed_out
    glue = by(res, "ns1.example.com.")
    assert glue.target == "192.0.2.53" and "ns_from_glue" in glue.notes and glue.authoritative is True
    ext = by(res, "ns.extern.net.")
    assert ext.target == "198.51.100.7" and ext.notes == []
    assert dns_.resolved == ["ns.extern.net."]
    resolver = by(res, "1.1.1.1")
    assert resolver.label == "Cloudflare" and resolver.serial == REF_EDITED
    # autoritative Ziele ohne RD, Resolver mit RD
    assert {q[2]: q[3] for q in dns_.queries} == {"192.0.2.53": False, "198.51.100.7": False, "1.1.1.1": True}
    assert res.nameservers == ["ns.extern.net.", "ns1.example.com."]
    assert res.external.enabled and res.external.authoritative and res.external.resolvers == ["1.1.1.1"]
    assert "A" in res.comparable_types and "SOA" not in res.comparable_types


async def test_p6_external_disabled(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json())
    Forbidden(monkeypatch)
    res = await check(OFF)
    assert {s.kind for s in res.sources} == {"panel-api"}
    assert res.external.enabled is False and res.external.resolvers == []
    assert res.summary.in_sync


async def test_p6_authoritative_off_only_resolvers(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    dns_ = DnsFake(monkeypatch)
    res = await check(prop.PropagationSettings(enabled=True, check_authoritative=False, resolvers=("9.9.9.9",)))
    assert [s.kind for s in res.sources] == ["panel-api", "resolver"]
    assert dns_.resolved == [] and res.external.authoritative is False


# ------------------------------------------------------------------------------------------------ P7 Peers
async def test_p7_peer_zone_missing_and_timeout(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json(), ns2=None, ns3=zone_json())
    c["ns3"].fail_on_get = PowerDNSAPIError(504, "Timeout connecting to PowerDNS server 'ns3'", "ns3",
                                            transport_error=True)
    res = await check()
    missing, slow = by(res, "ns2"), by(res, "ns3")
    assert missing.status == "skipped" and missing.error_code == "zone_missing"
    assert slow.status == "timeout" and slow.error_code == "timeout" and slow.error == "Zeitüberschreitung"
    assert res.summary.skipped == 1 and res.summary.failed == 1 and not res.summary.in_sync


async def test_p7_peer_api_error_text_only_for_admin(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json(), ns2=zone_json())
    c["ns2"].fail_on_get = PowerDNSAPIError(500, json.dumps({"error": "Backend kaputt http://intern:8081"}), "ns2")
    admin = by(await check(admin=True), "ns2")
    prop.invalidate_caches()
    user = by(await check(admin=False), "ns2")
    assert admin.error == "PowerDNS-API-Fehler: Backend kaputt http://intern:8081"
    assert user.error == "PowerDNS-API-Fehler" and user.status == "error"


async def test_p7_strict_serial_when_zone_on_one_writable_server(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(edited=REF_EDITED - 1))
    res = await check(writable={"ns1"})  # ns2 ist read-only -> kein getrenntes Backend
    peer = by(res, "ns2")
    assert peer.status == "mismatch" and peer.match is False and peer.serial_relation == "behind"
    assert "separate_backend" not in peer.notes and res.separate_backends is False


async def test_p7_separate_backend_different_content_mismatch(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(edited=7, serial=7, www="192.0.2.99"))
    res = await check(writable={"ns1", "ns2"})
    peer = by(res, "ns2")
    assert peer.status == "mismatch" and "separate_backend" in peer.notes
    assert peer.content_match is False and peer.content_diff_count == 1
    assert peer.content_diff_sample == ["www.example.com. A"]
    assert res.separate_backends is True and not res.summary.in_sync


async def test_p7_content_flag_same_content(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(edited=7, serial=7))
    res = await check(content=True, writable={"ns1"})
    peer = by(res, "ns2")
    assert peer.status == "ok" and peer.content_match is True and peer.content_diff_count == 0
    assert "content_same_serial_differs" in peer.notes
    assert res.content_compared and res.sources[0].content_match is True


async def test_p7_content_flag_different_content(monkeypatch):
    extra = [rr("a.example.com.", "TXT", '"x"'), rr("b.example.com.", "MX", "10 mx.example.com.")]
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(extra=extra))
    res = await check(content=True)
    peer = by(res, "ns2")
    assert peer.status == "mismatch" and peer.content_match is False and peer.content_diff_count == 2
    assert peer.content_diff_sample == ["a.example.com. TXT", "b.example.com. MX"]


async def test_p7_notify_pending(monkeypatch):
    servers(monkeypatch, ns1=zone_json(kind="Master", notified=REF_EDITED - 2))
    res = await check()
    assert "notify_pending" in res.sources[0].notes


async def test_p7_slave_peer_strict_even_with_separate_backends(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(), ns3=zone_json(kind="Slave", edited=REF_EDITED - 1))
    res = await check(writable={"ns1", "ns2", "ns3"})
    slave = by(res, "ns3")
    assert slave.status == "mismatch" and "secondary_lagging" in slave.notes and "separate_backend" not in slave.notes


async def test_p7_unloaded_server_is_skipped(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    monkeypatch.setattr(pdns_manager, "unloaded", {"kaputt": "api key unreadable"})
    row = by(await check(), "kaputt")
    assert row.status == "skipped" and row.error_code == "not_loaded"


# ------------------------------------------------------------------------------------------------ [D9]
async def test_d9_two_writable_servers_same_content_different_serial(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json(), ns2=zone_json(serial=2026100700, edited=2026100701))
    res = await check(writable={"ns1", "ns2"})
    peer = by(res, "ns2")
    assert peer.status == "ok" and "separate_backend" in peer.notes
    assert peer.match is False and peer.serial == 2026100701  # Serial nur Info
    assert peer.content_match is True and peer.content_diff_count == 0
    assert res.separate_backends is True and res.summary.in_sync is True
    # Peer: erst Metadaten, dann (Serial abweichend) die volle Zone fuer den Fingerprint
    gets = [p for m, ep, _j, p, _t in c["ns2"].calls if m == "GET"]
    assert gets == [{"rrsets": "false"}, {}]


async def test_d9_same_serial_needs_no_full_zone(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json(), ns2=zone_json())
    res = await check(writable={"ns1", "ns2"})
    assert by(res, "ns2").status == "ok"
    assert [p for m, _ep, _j, p, _t in c["ns2"].calls if m == "GET"] == [{"rrsets": "false"}]


async def test_d9_ns_serial_of_second_panel_server_matches(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(serial=2026100700, edited=2026100701))
    DnsFake(monkeypatch, per_ip={"192.0.2.53": 2026100701, "198.51.100.7": REF_EDITED, "1.1.1.1": 2026100700})
    res = await check(ON, writable={"ns1", "ns2"})
    ns_row = by(res, "ns1.example.com.")
    assert ns_row.status == "ok" and ns_row.match is True and ns_row.serial == 2026100701
    assert by(res, "1.1.1.1").status == "ok"  # entspricht dem gespeicherten serial von ns2
    assert res.summary.in_sync


async def test_d9_unknown_serial_still_mismatch_and_strict_without_separate(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(serial=2026100700, edited=2026100701))
    DnsFake(monkeypatch, per_ip={"192.0.2.53": 2026100701, "198.51.100.7": 5})
    res = await check(ON, writable={"ns1", "ns2"})
    assert by(res, "ns.extern.net.").status == "mismatch"
    prop.invalidate_caches()
    res = await check(ON, writable={"ns1"}, user=2)  # ns2 read-only: nur die Referenz-Serial zaehlt
    strict = by(res, "ns1.example.com.")
    assert strict.status == "mismatch" and strict.match is False and strict.serial_relation == "ahead"
    assert "secondary_lagging" in by(res, "ns.extern.net.").notes  # 5 liegt hinter der Referenz


async def test_d9_record_match_decides_for_dns_rows(monkeypatch):
    servers(monkeypatch, ns1=zone_json(), ns2=zone_json(serial=2026100700, edited=2026100701))
    DnsFake(monkeypatch, per_ip={"192.0.2.53": 99, "198.51.100.7": 99},
            records={("www.example.com.", "A"): ["192.0.2.10"]})
    res = await check(ON, record="www.example.com.", rtype="A", writable={"ns1", "ns2"})
    ns_row = by(res, "ns1.example.com.")
    assert ns_row.match is False and ns_row.record_match is True and ns_row.status == "ok"


# ------------------------------------------------------------------------------------------------ P8/P9
async def test_p8_reference_missing_cancels_tasks(monkeypatch):
    c = servers(monkeypatch, ns1=None, ns2=zone_json())
    dns_ = DnsFake(monkeypatch, delay=5.0)
    real = c["ns1"]._request

    async def slow_missing(*a, **k):
        await asyncio.sleep(0.05)
        return await real(*a, **k)

    c["ns1"]._request = slow_missing
    t0 = time.monotonic()
    with pytest.raises(prop.PropagationReferenceError) as e:
        await check(ON)
    assert e.value.kind == "zone_missing" and time.monotonic() - t0 < 2
    assert dns_.cancelled == 1  # Resolver-Abfrage lief und wurde abgebrochen


@pytest.mark.parametrize("status,kind", [(503, "unreachable"), (504, "timeout"), (500, "api_error"),
                                         (422, "zone_missing")])
async def test_p8_reference_error_classification(monkeypatch, status, kind):
    c = servers(monkeypatch, ns1=zone_json())
    detail = "Could not find domain 'example.com.'" if status == 422 else "x"
    c["ns1"].fail_on_get = PowerDNSAPIError(status, detail, "ns1")
    with pytest.raises(prop.PropagationReferenceError) as e:
        await check()
    assert e.value.kind == kind


async def test_p9_deadline(monkeypatch):
    monkeypatch.setattr(prop, "TOTAL_TIMEOUT", 0.3)
    servers(monkeypatch, ns1=zone_json())
    dns_ = DnsFake(monkeypatch, delay=5.0)
    t0 = time.monotonic()
    res = await check(ON)
    assert time.monotonic() - t0 < 1.5
    assert res.timed_out is True
    dns_rows = [s for s in res.sources if s.kind != "panel-api"]
    assert dns_rows and all(s.status == "timeout" and s.error_code == "deadline" for s in dns_rows)
    assert dns_.cancelled >= 1


# ------------------------------------------------------------------------------------------------ [D14]
async def test_d14_slow_reference_is_bounded(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json(), ns2=zone_json())
    DnsFake(monkeypatch)
    real = c["ns1"]._request

    async def six_seconds(*a, **k):
        await asyncio.sleep(6.0)
        return await real(*a, **k)

    c["ns1"]._request = six_seconds
    t0 = time.monotonic()
    with pytest.raises(prop.PropagationReferenceError) as e:
        await check(ON)
    elapsed = time.monotonic() - t0
    assert e.value.kind == "timeout"
    assert elapsed <= 8.5 and elapsed < prop.REFERENCE_TIMEOUT + 1.0


async def test_d14_late_reference_leaves_budget_for_nameservers(monkeypatch):
    # verkleinerte Zeitachse: Gesamt 2 s, Referenz antwortet kurz vor ihrem Limit (0.9 von 1.0 s)
    monkeypatch.setattr(prop, "TOTAL_TIMEOUT", 2.0)
    monkeypatch.setattr(prop, "REFERENCE_TIMEOUT", 1.0)
    monkeypatch.setattr(prop, "NS_MIN_BUDGET", 0.8)
    monkeypatch.setattr(prop, "NS_RESOLVE_TIMEOUT", 0.6)
    c = servers(monkeypatch, ns1=zone_json())
    dns_ = DnsFake(monkeypatch)
    real_resolve = dns_.resolve

    async def slow_resolve(ns_name, *, ipv6, timeout):
        await asyncio.sleep(0.4)
        return await real_resolve(ns_name, ipv6=ipv6, timeout=timeout)

    monkeypatch.setattr(prop, "resolve_ns_addresses", slow_resolve)
    real = c["ns1"]._request

    async def late(*a, **k):
        await asyncio.sleep(0.9)
        return await real(*a, **k)

    c["ns1"]._request = late
    t0 = time.monotonic()
    res = await check(ON)
    assert time.monotonic() - t0 < 2.5
    auth = [s for s in res.sources if s.kind == "authoritative"]
    assert len(auth) == 2 and all(s.error_code != "deadline" and s.status == "ok" for s in auth), auth
    # Glue-Ziel wurde sofort gefragt, nicht erst nach der Aufloesung von ns.extern.net.
    starts = {q[2]: q[4] for q in dns_.queries if q[1] == "SOA"}
    assert starts["192.0.2.53"] < starts["198.51.100.7"] - 0.3


async def test_d14_reference_budget_reserves_ns_time(monkeypatch):
    c = servers(monkeypatch, ns1=zone_json())
    DnsFake(monkeypatch)
    await check(ON)
    timeouts = [t for m, ep, _j, p, t in c["ns1"].calls if m == "GET"]
    assert timeouts and timeouts[0] <= prop.REFERENCE_TIMEOUT
    assert prop.REFERENCE_TIMEOUT + prop.NS_MIN_BUDGET <= prop.TOTAL_TIMEOUT


# ------------------------------------------------------------------------------------------------ P10 Records
async def test_p10_record_compare(monkeypatch):
    extra = [
        rr("v6.example.com.", "AAAA", "2001:DB8:0:0::1", "2001:db8::2", disabled=["2001:db8::2"]),
        rr("t.example.com.", "TXT", '"foo bar"'),
        rr("lua.example.com.", "LUA", 'A "ifportup(443, {\'192.0.2.1\'})"'),
    ]
    servers(monkeypatch, ns1=zone_json(extra=extra), ns2=zone_json(extra=extra))
    DnsFake(monkeypatch, records={("v6.example.com.", "AAAA"): ["2001:db8::1"], ("t.example.com.", "TXT"): ['"foo bar"'],
                                  ("lua.example.com.", "A"): ["192.0.2.1"]})
    res = await check(ON, record="v6.example.com.", rtype="AAAA")
    assert res.record.expected_values == ["2001:db8::1"] and res.record.comparable
    assert by(res, "ns2").record_match is True
    assert by(res, "1.1.1.1").record_match is True and by(res, "1.1.1.1").status == "ok"

    res = await check(ON, record="t.example.com.", rtype="TXT", user=2)
    assert by(res, "ns.extern.net.").record_match is True

    res = await check(ON, record="lua.example.com.", rtype="A", user=3)
    assert res.record.comparable is False
    row = by(res, "1.1.1.1")
    assert row.record_match is None and "lua_not_comparable" in row.notes and row.status == "ok"


async def test_p10_record_mismatch_and_query_failure(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    fake = DnsFake(monkeypatch, records={("www.example.com.", "A"): ["192.0.2.11"]})
    res = await check(ON, record="www.example.com.", rtype="A")
    row = by(res, "1.1.1.1")
    assert row.record_match is False and row.status == "mismatch" and row.record_values == ["192.0.2.11"]

    async def failing(name, rdtype, server, timeout=3.0, **kw):
        if rdtype == "SOA":
            return await fake.query(name, rdtype, server, timeout, **kw)
        return prop.DnsAnswer(error_code="timeout")

    monkeypatch.setattr(prop, "query_dns", failing)
    prop.invalidate_caches()
    res = await check(ON, record="www.example.com.", rtype="A", user=2)
    row = by(res, "1.1.1.1")
    assert row.record_match is None and "record_query_failed" in row.notes and row.status == "ok"


async def test_p10_dns_row_classification(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    answers = {
        "192.0.2.53": prop.DnsAnswer(rcode="REFUSED", aa=False),
        "198.51.100.7": prop.DnsAnswer(rcode="NOERROR", aa=False, soa_serial=REF_EDITED),
        "1.1.1.1": prop.DnsAnswer(rcode="NOERROR", aa=False, soa_serial=REF_EDITED - 1, ttl=1240),
    }

    async def q(name, rdtype, server, timeout=3.0, **kw):
        return answers[server]

    async def resolve(ns, *, ipv6, timeout):
        return ["198.51.100.7"], None

    monkeypatch.setattr(prop, "query_dns", q)
    monkeypatch.setattr(prop, "resolve_ns_addresses", resolve)
    res = await check(ON)
    assert by(res, "ns1.example.com.").error_code == "refused"
    lame = by(res, "ns.extern.net.")
    assert lame.status == "error" and lame.error_code == "not_authoritative" and lame.serial == REF_EDITED
    cache = by(res, "1.1.1.1")
    assert cache.status == "mismatch" and "resolver_cache" in cache.notes and cache.ttl == 1240
    assert res.summary.failed == 2 and res.summary.mismatch == 1


async def test_p10_ns_unresolvable_ipv6_and_truncation(monkeypatch):
    many = [rr(ZONE, "NS", *[f"ns{i}.example.com." for i in range(15)], "gone.extern.net.")]
    many += [rr(f"ns{i}.example.com.", "A", f"192.0.2.{100 + i}") for i in range(15)]
    many += [rr("ns0.example.com.", "AAAA", "2001:db8::53")]
    z = make_zone(ZONE, many, serial=REF_EDITED)
    servers(monkeypatch, ns1=z)
    DnsFake(monkeypatch, ns_ips={})
    res = await check(ON)
    assert by(res, "gone.extern.net.").error_code == "ns_unresolvable"
    v6 = by(res, "ns0.example.com.", "2001:db8::53")
    assert v6.status == "skipped" and v6.error_code == "ipv6_disabled"
    auth_targets = [s for s in res.sources if s.kind == "authoritative" and s.target]
    assert len(auth_targets) == prop.MAX_NS_TARGETS
    assert "truncated_targets" in res.sources[0].notes


# ------------------------------------------------------------------------------------------------ P11
async def test_p11_rate_limit_and_cache(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    for i in range(prop.RATE_MAX):
        prop.invalidate_caches()
        assert (await check(user=42)).cached is False
    prop.invalidate_caches()
    with pytest.raises(prop.PropagationRateLimited) as e:
        await check(user=42)
    assert 0 < e.value.retry_after <= 60
    # anderer Nutzer: eigenes Kontingent; identischer Check danach kommt aus dem Cache und zaehlt nicht
    first = await check(user=7)
    again = await check(user=42)
    assert first.cached is False and again.cached is True and again.checked_at == first.checked_at


async def test_p11_cache_key_includes_settings_and_record(monkeypatch):
    servers(monkeypatch, ns1=zone_json())
    DnsFake(monkeypatch)
    a = await check(OFF)
    b = await check(ON)
    c = await check(OFF, record="www.example.com.", rtype="A")
    assert not a.cached and not b.cached and not c.cached
    assert (await check(OFF)).cached


# ------------------------------------------------------------------------------------------------ P12 echter DNS-Pfad
class _UdpDns(asyncio.DatagramProtocol):
    def __init__(self, mode):
        self.mode = mode

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        q = dns.message.from_wire(data)
        if self.mode == "silent":
            return
        r = dns.message.make_response(q)
        qn = q.question[0]
        if self.mode == "refused":
            r.set_rcode(dns.rcode.REFUSED)
        else:
            r.flags |= dns.flags.AA
            t = dns.rdatatype.to_text(qn.rdtype)
            if t == "SOA":
                r.answer.append(dns.rrset.from_text(qn.name, 300, "IN", "SOA",
                                                    "ns1.example.com. h.example.com. 42 3600 600 86400 300"))
            elif t == "AAAA":
                r.answer.append(dns.rrset.from_text(qn.name, 60, "IN", "AAAA", "2001:db8:0:0::1"))
            elif t == "DNSKEY":
                r.answer.append(dns.rrset.from_text(
                    qn.name, 60, "IN", "DNSKEY",
                    "257 3 13 mdsswUyr3DPW132mOi8V9xESWE8jTo0dxCjjnopKl+GqJxpVXckHAeF+KkxLbxILfDLUT0rAK9iUzy1L53eKGQ=="))
        self.transport.sendto(r.to_wire(), addr)


@pytest.fixture
async def udp_dns():
    loop = asyncio.get_running_loop()
    servers_ = []

    async def start(mode):
        tr, _ = await loop.create_datagram_endpoint(lambda: _UdpDns(mode), local_addr=("127.0.0.1", 0))
        servers_.append(tr)
        return tr.get_extra_info("sockname")[1]

    yield start
    for tr in servers_:
        tr.close()


async def test_p12_query_dns_real_udp(udp_dns):
    port = await udp_dns("ok")
    a = await prop.query_dns("example.com.", "SOA", "127.0.0.1", 1.0, port=port)
    assert a.error_code is None and a.soa_serial == 42 and a.rcode == "NOERROR" and a.aa is True and a.ttl == 300
    v6 = await prop.query_dns("v6.example.com.", "AAAA", "127.0.0.1", 1.0, port=port, origin=ZONE)
    assert v6.values == ["2001:db8::1"]
    key = await prop.query_dns("example.com.", "DNSKEY", "127.0.0.1", 1.0, port=port)
    assert key.values and key.key_tags and all(isinstance(k, int) for k in key.key_tags)

    refused = await prop.query_dns("example.com.", "SOA", "127.0.0.1", 1.0, port=await udp_dns("refused"))
    assert refused.rcode == "REFUSED" and refused.soa_serial is None and refused.error_code is None

    t0 = time.monotonic()
    silent = await prop.query_dns("example.com.", "SOA", "127.0.0.1", 0.3, port=await udp_dns("silent"))
    assert silent.error_code == "timeout" and time.monotonic() - t0 < 1.5


async def test_p12_query_dns_network_error():
    # Port ohne Empfaenger: ICMP "port unreachable" -> Netzwerkfehler oder Zeitueberschreitung, nie eine Exception
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    a = await prop.query_dns("example.com.", "SOA", "127.0.0.1", 0.3, port=port)
    assert a.error_code in ("network_error", "timeout")


# ------------------------------------------------------------------------------------------------ Einstellungen
class _SettingsDB:
    """Minimale Session fuer load_settings/save_settings (system_settings gepatcht)."""

    def __init__(self, monkeypatch, values=None):
        self.values = dict(values or {})
        from app.services import system_settings as ss

        async def get_settings(db, keys):
            return {k: self.values[k] for k in keys if k in self.values}

        async def set_settings(db, mapping):
            for k, v in mapping.items():
                self.values[k] = ("true" if v else "false") if isinstance(v, bool) else v

        monkeypatch.setattr(ss, "get_settings", get_settings)
        monkeypatch.setattr(ss, "set_settings", set_settings)


async def test_settings_defaults_invalid_json_and_save(monkeypatch, caplog):
    db = _SettingsDB(monkeypatch)
    cfg = await prop.load_settings(None)
    assert cfg == prop.PropagationSettings(False, True, False, ("1.1.1.1", "8.8.8.8", "9.9.9.9"))
    db.values["propagation_resolvers"] = "{kaputt"
    assert (await prop.load_settings(None)).resolvers == tuple(prop.DEFAULT_RESOLVERS)
    db.values["propagation_resolvers"] = json.dumps(["10.0.0.53", "127.0.0.1", "dns.google"])
    assert (await prop.load_settings(None)).resolvers == ("10.0.0.53",)

    changed = await prop.save_settings(None, enabled=True, resolvers=["2606:4700:4700:0::1111", "10.0.0.53"])
    assert changed == {"enabled": {"from": False, "to": True},
                       "resolvers": {"from": ["10.0.0.53"], "to": ["2606:4700:4700::1111", "10.0.0.53"]}}
    assert db.values["propagation_enabled"] == "true"
    assert json.loads(db.values["propagation_resolvers"]) == ["2606:4700:4700::1111", "10.0.0.53"]
    assert await prop.save_settings(None, enabled=True, ipv6=False) == {}
    with pytest.raises(ValueError):
        await prop.save_settings(None, resolvers=["dns.google"])


# ------------------------------------------------------------------------------------------------ P13 Endpoint
@pytest.fixture
def api(monkeypatch):
    from app import main

    state = {"user": make_user(role="admin"), "session": FakeSession(), "calls": []}

    async def user_dep():
        return state["user"]

    async def db_dep():
        yield state["session"]

    async def load_settings(db):
        return OFF

    async def check_zone(**kw):
        state["calls"].append(kw)
        if "raise" in state:
            raise state["raise"]
        return _response()

    main.app.dependency_overrides[get_current_user] = user_dep
    main.app.dependency_overrides[get_db] = db_dep
    monkeypatch.setattr(prop, "load_settings", load_settings)
    monkeypatch.setattr(prop, "check_zone", check_zone)
    monkeypatch.setattr(pdns_manager, "clients", {"srv": FakePowerDNSClient("srv")})
    monkeypatch.setattr(pdns_manager, "unloaded", {"weg": "api key unreadable"})
    state["client"] = TestClient(main.app, raise_server_exceptions=False)
    yield state
    main.app.dependency_overrides.pop(get_current_user, None)
    main.app.dependency_overrides.pop(get_db, None)


def _response():
    from app.schemas.propagation import PropagationExternal, PropagationResponse, PropagationSummary

    return PropagationResponse(
        zone=ZONE, server="srv", checked_at="2026-10-05T00:00:00Z", cached=False, duration_ms=1, timed_out=False,
        expected_serial=1, reference_serial_raw=1, nameservers=[], content_compared=False,
        external=PropagationExternal(enabled=False, authoritative=False, resolvers=[], ipv6=False),
        comparable_types=list(prop.COMPARABLE_TYPES), sources=[],
        summary=PropagationSummary(total=0, ok=0, mismatch=0, failed=0, skipped=0, in_sync=True),
    )


URL = "/api/v1/zones/srv/example.com./propagation"


def test_p13_endpoint_reaches_handler_with_record(api):
    r = api["client"].get(URL, params={"name": "www", "type": "a", "content": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["zone"] == ZONE and "text/html" not in r.headers["content-type"]
    kw = api["calls"][-1]
    assert kw["record_fqdn"] == "www.example.com." and kw["rtype"] == "A" and kw["compare_content_flag"] is True
    assert kw["server_name"] == "srv" and kw["zone_norm"] == ZONE and kw["is_admin"] is True
    assert kw["writable_servers"] == ["srv"]
    # ohne Record
    assert api["client"].get(URL).status_code == 200
    assert api["calls"][-1]["record_fqdn"] is None and api["calls"][-1]["rtype"] is None


def test_p13_validation_errors(api):
    c = api["client"]
    r = c.get(URL, params={"name": "www"})
    assert r.status_code == 422 and r.json()["detail"] == "Bei einem Record-Vergleich sind Name und Typ erforderlich."
    assert c.get(URL, params={"type": "A"}).status_code == 422
    r = c.get(URL, params={"name": "x", "type": "RRSIG"})
    assert r.status_code == 422 and r.json()["detail"] == "Der Typ RRSIG kann nicht verglichen werden."
    r = c.get(URL, params={"name": "www.other.org.", "type": "A"})
    assert r.status_code == 422 and "liegt nicht in der Zone" in r.json()["detail"]
    assert api["calls"] == []


def test_p13_unknown_and_unloaded_server(api):
    r = api["client"].get("/api/v1/zones/nope/example.com./propagation")
    assert r.status_code == 404 and r.json()["detail"] == "Server 'nope' ist nicht konfiguriert."
    r = api["client"].get("/api/v1/zones/weg/example.com./propagation")
    assert r.status_code == 503 and "nicht geladen" in r.json()["detail"]


def test_p13_rate_limited(api):
    api["raise"] = prop.PropagationRateLimited(23)
    r = api["client"].get(URL)
    assert r.status_code == 429 and r.headers["retry-after"] == "23"
    assert r.json()["detail"] == "Zu viele Propagations-Prüfungen – bitte in 23 Sekunden erneut versuchen."


@pytest.mark.parametrize("kind,status,text", [
    ("zone_missing", 404, "Zone example.com. wurde auf Server srv nicht gefunden."),
    ("unreachable", 503, "Referenz-Server srv ist nicht erreichbar."),
    ("timeout", 504, "Referenz-Server srv hat nicht rechtzeitig geantwortet."),
])
def test_p13_reference_errors(api, kind, status, text):
    api["raise"] = prop.PropagationReferenceError(kind, None)
    r = api["client"].get(URL)
    assert r.status_code == status and r.json()["detail"] == text


def test_p13_reference_api_error_detail_only_for_admin(api):
    exc = PowerDNSAPIError(500, json.dumps({"error": "intern kaputt"}), "srv")
    api["raise"] = prop.PropagationReferenceError("api_error", exc)
    r = api["client"].get(URL)
    assert r.status_code == 502 and r.json()["detail"] == "Referenz-Server srv meldet einen Fehler: intern kaputt"
    api["user"] = make_user(role="user", uid=5, username="u")
    api["session"] = FakeSession(zone_access=[(ZONE, "read")])
    r = api["client"].get(URL)
    assert r.status_code == 502 and r.json()["detail"] == "Referenz-Server srv meldet einen Fehler."
    assert api["calls"][-1]["is_admin"] is False


def test_p13_acl_read_access_required(api):
    api["user"] = make_user(role="user", uid=5, username="u")
    api["session"] = FakeSession(zone_access=[("andere.example.", "manage")])
    r = api["client"].get(URL)
    assert r.status_code == 403 and r.json()["detail"] == "Keine Berechtigung für diese Zone"
    api["session"] = FakeSession(zone_access=[(ZONE, "read")])
    assert api["client"].get(URL).status_code == 200


def test_p13_route_is_ordered_before_zones_router():
    from app import main
    from app.routers import propagation as prop_router
    from app.routers import zones as zones_router

    # ein _IncludedRouter je Modul in Discovery-Reihenfolge (FastAPI 0.141)
    included = [getattr(r, "original_router", None) for r in main.app.routes]
    assert included.index(prop_router.router) < included.index(zones_router.router)
    assert prop_router.ROUTER_ORDER == 55
