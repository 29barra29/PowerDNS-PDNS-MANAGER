"""DNSSEC Teil B (WS-F4-C): Elternzonen-DS, DNSKEY auf den Nameservern, 409 ``parent_ds_present``, L6.

- Echter DNS-Pfad wie F12 P12: lokale UDP-DNS-Server auf 127.0.0.x (gleicher Port, ``dnssec_parent.DNS_PORT``);
  ``propagation.target_allowed`` laesst dafuer nur 127.0.0.0/8 zusaetzlich zu (der SSRF-Schutz selbst ist eigens
  geprueft).
- Router-Tests ohne DB: Mini-App nur mit dem DNSSEC-Router, ``FakeSession``, ``dnssec_fakes.FakePdnsClient``;
  Einstellungen, ``query_dns`` und die NS-Aufloesung sind gepatcht.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from dnssec_fakes import DetachedAudits, EventRecorder, FakePdnsClient
from fakes.pdns import make_zone, rr
from app.core.auth import get_current_user
from app.models.models import AuditLog
from app.routers import dnssec as dnssec_router
from app.services import audit as audit_service
from app.services import dnssec_parent as dp
from app.services import dnssec_service as svc
from app.services import propagation as prop
from app.services import webhook_outbox
from app.services.dnssec_parse import compute_key_tag
from app.services.pdns_client import pdns_manager

A = "/api/v1/dnssec"
Z = "example.com."
B = f"{A}/srv1/{Z}"
UNKNOWN_DS = "12345 13 2 " + "ab" * 32


@pytest.fixture(autouse=True)
def _reset():
    prop.reset_for_tests()
    yield
    prop.reset_for_tests()


def _keys(zone: str = Z):
    """Zwei SEP-Schluessel (alt aktiv, neu vorveroeffentlicht) und ein ZSK aus den deterministischen Testvektoren."""
    pdns = FakePdnsClient("srv1", zones=(zone,))
    old = pdns.add_key(zone, "ksk")
    new = pdns.add_key(zone, "ksk", active=False)
    zsk = pdns.add_key(zone, "zsk")
    return pdns, old, new, zsk


# =============================================================================================
# Reine Helfer
# =============================================================================================
def test_own_sep_ds_uses_pdns_lines_and_computed_digests():
    _, old, new, zsk = _keys()
    own = dp.own_sep_ds(Z, [old, new, zsk])
    assert set(own) == {old["id"], new["id"]}  # ZSK ohne DS
    for key in (old, new):
        info = own[key["id"]]
        assert info["key_tag"] == compute_key_tag(key["dnskey"])
        assert {dp.ds_tuple(line) for line in key["ds"]} <= info["tuples"]
        assert {t[2] for t in info["tuples"]} == {1, 2, 4}
    # ohne ds-Feld (aeltere API-Antworten) reicht der DNSKEY
    bare = {k: v for k, v in old.items() if k != "ds"}
    assert dp.own_sep_ds(Z, [bare])[old["id"]]["tuples"] == own[old["id"]]["tuples"]
    assert dp.ds_tuple("Muell") is None
    assert dp.own_sep_ds(Z, [{"id": 9, "keytype": "ksk", "dnskey": "kaputt"}])[9]["tuples"] == set()


def test_match_parent_ds_visible_missing_unknown():
    _, old, new, _zsk = _keys()
    old_sha256 = next(x for x in old["ds"] if x.split()[2] == "2")
    rows = [
        {"resolver": "192.0.2.1", "status": "ok", "ds": [old_sha256.upper()]},  # Gross-/Kleinschreibung egal
        {"resolver": "192.0.2.2", "status": "ok", "ds": [UNKNOWN_DS]},
        {"resolver": "192.0.2.3", "status": "nodata", "ds": []},
        {"resolver": "192.0.2.4", "status": "timeout", "ds": []},
    ]
    m = dp.match_parent_ds(Z, [old, new], rows)
    assert m["keys"][str(old["id"])]["visible_on"] == ["192.0.2.1"]
    assert m["keys"][str(old["id"])]["missing_on"] == ["192.0.2.2", "192.0.2.3"]  # Timeout zaehlt nicht
    assert m["keys"][str(new["id"])]["visible_on"] == []
    assert m["unknown_tags"] == [12345] and m["any_visible"] is True
    report = {"keys": m["keys"]}
    assert dp.visible_resolvers(report) == ["192.0.2.1"]
    assert dp.visible_resolvers({"keys": {"1": {"visible_on": ["1.1.1.1"]}}}) == ["Cloudflare (1.1.1.1)"]


def test_settings_gates():
    S = prop.PropagationSettings
    assert dp.parent_check_enabled(S()) is False
    assert dp.parent_check_enabled(S(enabled=True)) is True
    assert dp.parent_check_enabled(S(enabled=True, resolvers=())) is False
    assert dp.dnskey_check_enabled(S(enabled=True)) is True
    assert dp.dnskey_check_enabled(S(enabled=True, check_authoritative=False)) is False


def test_published_key_tags():
    _, old, new, zsk = _keys()
    hidden = dict(zsk, published=False)
    assert dp.published_key_tags([old, new, hidden]) == sorted({compute_key_tag(old["dnskey"]),
                                                                 compute_key_tag(new["dnskey"])})


def test_evaluate_ns():
    tag = 4711
    ok = {"ip": "a", "status": "ok", "key_tags": [tag, 1], "error": None}
    ok2 = {"ip": "b", "status": "ok", "key_tags": [1], "error": None}
    skipped = {"ip": "c", "status": "skipped", "key_tags": [], "error": "IPv6-Abfragen deaktiviert"}
    assert dp._evaluate_ns([ok], {tag})["ok"] is True
    r = dp._evaluate_ns([ok, ok2], {tag})
    assert r["ok"] is False and r["serves_key_tags"] == [1] and r["missing_tags"] == [tag]
    assert dp._evaluate_ns([ok, skipped], {tag})["ok"] is True  # nicht abgefragte Adresse blockiert nicht
    assert dp._evaluate_ns([skipped], {tag})["ok"] is False
    empty = dp._evaluate_ns([], {tag})
    assert empty["ok"] is False and empty["error"]


# =============================================================================================
# Echter DNS-Pfad (lokale UDP-Server)
# =============================================================================================
class _Dns(asyncio.DatagramProtocol):
    def __init__(self, handler):
        self.handler = handler

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        q = dns.message.from_wire(data)
        r = self.handler(q)
        if r is not None:
            self.transport.sendto(r.to_wire(), addr)


def answer(rtype: str | None = None, lines=(), *, rcode=dns.rcode.NOERROR, aa=True, rd_seen: list | None = None):
    def handler(q):
        if rd_seen is not None:
            rd_seen.append(bool(q.flags & dns.flags.RD))
        r = dns.message.make_response(q)
        r.set_rcode(rcode)
        if aa:
            r.flags |= dns.flags.AA
        qn = q.question[0]
        if rtype and lines and dns.rdatatype.to_text(qn.rdtype) == rtype:
            r.answer.append(dns.rrset.from_text_list(qn.name, 300, "IN", rtype, list(lines)))
        return r
    return handler


def silent(_q):
    return None


@pytest.fixture
async def dns_servers(monkeypatch):
    """``await start({ip: handler})`` -> gemeinsamer Port; setzt ``DNS_PORT`` und erlaubt 127.0.0.0/8."""
    loop = asyncio.get_running_loop()
    transports = []
    orig = prop.target_allowed
    monkeypatch.setattr(prop, "target_allowed", lambda ip, v6: None if str(ip).startswith("127.") else orig(ip, v6))

    async def start(handlers: dict):
        port = 0
        for ip, handler in handlers.items():
            tr, _ = await loop.create_datagram_endpoint(lambda h=handler: _Dns(h), local_addr=(ip, port))
            transports.append(tr)
            port = tr.get_extra_info("sockname")[1]
        monkeypatch.setattr(dp, "DNS_PORT", port)
        return port

    yield start
    for tr in transports:
        tr.close()


async def test_query_parent_ds_real_udp(dns_servers):
    _, old, new, _zsk = _keys()
    rd: list = []
    await dns_servers({
        "127.0.0.2": answer("DS", [old["ds"][1]], aa=False, rd_seen=rd),
        "127.0.0.3": answer(),  # NODATA
        "127.0.0.4": answer("DS", [UNKNOWN_DS], aa=False),
        "127.0.0.5": answer(rcode=dns.rcode.NXDOMAIN),
        "127.0.0.6": answer(rcode=dns.rcode.SERVFAIL, aa=False),
        "127.0.0.7": silent,
        "127.0.0.8": answer(rcode=dns.rcode.REFUSED, aa=False),
    })
    ips = ["127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6", "127.0.0.7", "127.0.0.8", "2001:db8::53"]
    rows = await dp.query_parent_ds(Z, ips, 0.3, ipv6=False, total=2.0)
    by = {r["resolver"]: r for r in rows}
    assert [r["resolver"] for r in rows] == ips
    assert by["127.0.0.2"]["status"] == "ok" and by["127.0.0.2"]["key_tags"] == [compute_key_tag(old["dnskey"])]
    assert rd == [True]  # Resolver -> Rekursion gewuenscht
    assert by["127.0.0.3"]["status"] == "nodata" and by["127.0.0.3"]["ds"] == []
    assert by["127.0.0.4"]["key_tags"] == [12345]
    assert by["127.0.0.5"]["status"] == "nxdomain"
    assert by["127.0.0.6"]["status"] == "servfail" and by["127.0.0.6"]["error"]
    assert by["127.0.0.7"]["status"] == "timeout"
    assert by["127.0.0.8"]["status"] == "error" and "REFUSED" in by["127.0.0.8"]["error"]
    assert by["2001:db8::53"]["status"] == "skipped" and "IPv6" in by["2001:db8::53"]["error"]
    m = dp.match_parent_ds(Z, [old, new], rows)
    assert m["keys"][str(old["id"])]["visible_on"] == ["127.0.0.2"]
    assert set(m["keys"][str(old["id"])]["missing_on"]) == {"127.0.0.3", "127.0.0.4", "127.0.0.5"}
    assert m["unknown_tags"] == [12345]


async def test_query_parent_ds_total_budget(dns_servers):
    await dns_servers({"127.0.0.2": silent})
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    rows = await dp.query_parent_ds(Z, ["127.0.0.2"], 3.0, total=0.4)
    assert rows[0]["status"] == "timeout" and loop.time() - t0 < 1.5


async def test_ssrf_loopback_never_queried(monkeypatch):
    called = []

    async def q(*a, **kw):
        called.append(a)
        return prop.DnsAnswer(rcode="NOERROR", values=[])

    monkeypatch.setattr(prop, "query_dns", q)
    rows = await dp.query_parent_ds(Z, ["127.0.0.1", "169.254.1.1"], 0.2)
    assert [r["status"] for r in rows] == ["skipped", "skipped"] and called == []


def _ns_zone(**glue):
    rrs = [rr(Z, "NS", "ns1.example.com.", "ns2.example.com.", "ns.extern.net.")]
    rrs += [rr(name, "A", *ips) for name, ips in glue.items()]
    return make_zone(Z, rrs)


async def test_check_dnskey_on_ns_real_udp(dns_servers, monkeypatch):
    """DNSKEY-Fall: ns1 liefert alt+neu, ns2 nur den alten Schluessel, der externe NS antwortet nicht autoritativ."""
    _, old, new, _zsk = _keys()
    rd: list = []
    await dns_servers({
        "127.0.0.2": answer("DNSKEY", [old["dnskey"], new["dnskey"]], rd_seen=rd),
        "127.0.0.3": answer("DNSKEY", [old["dnskey"]]),
        "127.0.0.4": answer("DNSKEY", [old["dnskey"], new["dnskey"]], aa=False),
    })
    resolved = []

    async def resolve(ns, *, ipv6, timeout):
        resolved.append(ns)
        return (["127.0.0.4"], None) if ns == "ns.extern.net." else ([], "ns_unresolvable")

    monkeypatch.setattr(prop, "resolve_ns_addresses", resolve)
    zone = _ns_zone(**{"ns1.example.com.": ["127.0.0.2"], "ns2.example.com.": ["127.0.0.3"]})
    new_tag, old_tag = compute_key_tag(new["dnskey"]), compute_key_tag(old["dnskey"])
    settings = prop.PropagationSettings(enabled=True)
    res, truncated = await dp.check_dnskey_on_ns(Z, zone, [new_tag], settings, timeout=1.0)
    assert truncated is False and resolved == ["ns.extern.net."]  # Glue zuerst, nur externe NS aufloesen
    assert rd and not any(rd)  # autoritative Ziele ohne RD
    ns1, ns2, ext = res["ns1.example.com."], res["ns2.example.com."], res["ns.extern.net."]
    assert ns1["ok"] is True and set(ns1["serves_key_tags"]) == {old_tag, new_tag}
    assert ns1["addresses"][0] == {"ip": "127.0.0.2", "status": "ok", "key_tags": sorted({old_tag, new_tag}),
                                   "error": None}
    assert ns2["ok"] is False and ns2["missing_tags"] == [new_tag] and ns2["serves_key_tags"] == [old_tag]
    assert ext["ok"] is False and ext["addresses"][0]["status"] == "not_authoritative"

    report = await dp.dnskey_report(Z, zone, [new_tag], settings)
    assert report["enabled"] is True and report["all_ok"] is False and report["expected_tags"] == [new_tag]


async def test_dnskey_report_all_ok_and_disabled(dns_servers, monkeypatch):
    _, old, new, _zsk = _keys()
    await dns_servers({"127.0.0.2": answer("DNSKEY", [old["dnskey"], new["dnskey"]]),
                       "127.0.0.3": answer("DNSKEY", [new["dnskey"], old["dnskey"]])})

    async def resolve(ns, *, ipv6, timeout):
        return [], "ns_unresolvable"

    monkeypatch.setattr(prop, "resolve_ns_addresses", resolve)
    zone = make_zone(Z, [rr(Z, "NS", "ns1.example.com.", "ns2.example.com."),
                         rr("ns1.example.com.", "A", "127.0.0.2"), rr("ns2.example.com.", "A", "127.0.0.3")])
    tags = [compute_key_tag(new["dnskey"])]
    ok = await dp.dnskey_report(Z, zone, tags, prop.PropagationSettings(enabled=True))
    assert ok["all_ok"] is True and set(ok["nameservers"]) == {"ns1.example.com.", "ns2.example.com."}
    off = await dp.dnskey_report(Z, zone, tags, prop.PropagationSettings(enabled=True, check_authoritative=False))
    assert off == dp.empty_dnskey_report(Z, tags)

    # nicht aufloesbarer NS -> Zeile mit Fehler, nie ok
    zone2 = make_zone(Z, [rr(Z, "NS", "ns.extern.net.")])
    bad = await dp.dnskey_report(Z, zone2, tags, prop.PropagationSettings(enabled=True))
    row = bad["nameservers"]["ns.extern.net."]
    assert bad["all_ok"] is False and row["addresses"][0]["status"] == "ns_unresolvable" and row["error"]


async def test_dnskey_targets_capped(monkeypatch):
    names = [f"ns{i}.extern.net." for i in range(prop.MAX_NS_TARGETS + 2)]
    zone = make_zone(Z, [rr(Z, "NS", *names)])
    by_ns = {n: f"192.0.2.{i + 1}" for i, n in enumerate(sorted(names))}

    async def resolve(ns, *, ipv6, timeout):
        return [by_ns[ns]], None

    async def q(name, rdtype, server, timeout=3.0, **kw):
        return prop.DnsAnswer(rcode="NOERROR", aa=True, values=["x"], key_tags=[1])

    monkeypatch.setattr(prop, "resolve_ns_addresses", resolve)
    monkeypatch.setattr(prop, "query_dns", q)
    res, truncated = await dp.check_dnskey_on_ns(Z, zone, [1], prop.PropagationSettings(enabled=True))
    assert truncated is True
    assert sum(1 for r in res.values() if r["ok"]) == prop.MAX_NS_TARGETS
    cut = [r for r in res.values() if not r["ok"]]
    assert len(cut) == 2 and all("zu viele" in r["error"] for r in cut)


# =============================================================================================
# L6: DB-Verbindung vor dem Warten freigeben
# =============================================================================================
class _TxSession:
    def __init__(self, in_tx=True):
        self.tx = in_tx
        self.commits = 0

    def in_transaction(self):
        return self.tx

    async def commit(self):
        self.commits += 1
        self.tx = False


async def test_release_db_and_dns_slot():
    await prop.release_db(None)
    s = _TxSession(in_tx=False)
    await prop.release_db(s)
    assert s.commits == 0
    s = _TxSession()
    async with prop.dns_slot(s):
        assert s.commits == 1
        assert prop._global_semaphore()._value == prop.GLOBAL_CONCURRENCY - 1
    assert prop._global_semaphore()._value == prop.GLOBAL_CONCURRENCY


async def test_check_zone_releases_db_before_waiting(monkeypatch):
    s = _TxSession()
    seen = []

    async def run_check(run):
        seen.append(s.commits)
        return prop.PropagationResponse.model_construct(cached=False)

    monkeypatch.setattr(prop, "_run_check", run_check)
    sem = prop._global_semaphore()
    for _ in range(prop.GLOBAL_CONCURRENCY):
        await sem.acquire()  # alle Plaetze belegt: der Check muss warten
    task = asyncio.ensure_future(prop.check_zone(
        user_id=1, is_admin=True, server_name="srv1", zone_norm=Z, record_fqdn=None, rtype=None,
        compare_content_flag=False, settings=prop.PropagationSettings(), db=s))
    await asyncio.sleep(0.05)
    assert s.commits == 1 and not task.done()  # Verbindung frei, waehrend auf die Semaphore gewartet wird
    sem.release()
    await asyncio.wait_for(task, 2)
    assert seen == [1]
    for _ in range(prop.GLOBAL_CONCURRENCY - 1):
        sem.release()


# =============================================================================================
# Router
# =============================================================================================
class Env:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.pdns, self.old, self.new, self.zsk = _keys()
        self.pdns.zones[Z]["rrsets"].append(rr("ns1.example.com.", "A", "192.0.2.53"))
        monkeypatch.setattr(pdns_manager, "clients", {"srv1": self.pdns})
        monkeypatch.setattr(pdns_manager, "unloaded", {})
        self.events = EventRecorder()
        monkeypatch.setattr(webhook_outbox, "enqueue_event", self.events)
        monkeypatch.setattr(audit_service, "write_audit_detached", DetachedAudits())
        svc.reset_version_cache()
        self.settings = prop.PropagationSettings()

        async def load_settings(db):
            return self.settings

        monkeypatch.setattr(prop, "load_settings", load_settings)
        monkeypatch.setattr(prop, "query_dns", self.query)
        monkeypatch.setattr(prop, "resolve_ns_addresses", self.resolve)
        self.answers: dict = {}
        self.queries: list = []
        self.session: FakeSession | None = None
        self.user = make_user()

    def enable(self, **kw):
        self.settings = prop.PropagationSettings(enabled=True, **kw)

    async def query(self, name, rdtype, server, timeout=3.0, *, recursion=False, origin=None, port=53):
        self.queries.append(SimpleNamespace(name=name, rdtype=rdtype, server=server, recursion=recursion,
                                            commits=self.session.commits if self.session else None))
        default = prop.DnsAnswer(rcode="NOERROR", aa=True, values=[], key_tags=[] if rdtype == "DNSKEY" else None)
        return self.answers.get((server, rdtype), default)

    async def resolve(self, ns, *, ipv6, timeout):
        return [], "ns_unresolvable"

    def ds_answer(self, server, *lines):
        self.answers[(server, "DS")] = prop.DnsAnswer(rcode="NOERROR", aa=False, values=[x.lower() for x in lines])

    def client(self, *, user=None, zone_access=None) -> TestClient:
        user = user or self.user
        self.session = FakeSession(user_row=user, zone_access=zone_access)
        app = build_app(self.session, dnssec_router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def audits(self, action):
        return [o for o in self.session.added if isinstance(o, AuditLog) and o.action == action]


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def tag(key):
    return compute_key_tag(key["dnskey"])


def test_route_order_with_part_b():
    paths = [(sorted(r.methods)[0], r.path) for r in dnssec_router.router.routes]
    P = "/dnssec/{server_name}/{zone_id:path}"
    assert paths[:4] == [("GET", f"{P}/status"), ("GET", f"{P}/parent-ds"), ("GET", f"{P}/dnskey-check"),
                         ("GET", f"{P}/ds")]
    assert len(paths) == 14


def test_status_capabilities_follow_settings(env):
    c = env.client()
    caps = c.get(f"{B}/status").json()["capabilities"]
    assert caps["parent_ds_check"] is False and caps["dnskey_check"] is False
    env.enable()
    caps = c.get(f"{B}/status").json()["capabilities"]
    assert caps["parent_ds_check"] is True and caps["dnskey_check"] is True
    env.enable(check_authoritative=False, resolvers=())
    caps = c.get(f"{B}/status").json()["capabilities"]
    assert caps["parent_ds_check"] is False and caps["dnskey_check"] is False


def test_parent_ds_disabled_queries_nothing(env):
    r = env.client().get(f"{B}/parent-ds")
    assert r.status_code == 200
    assert r.json() == {"zone": Z, "enabled": False, "resolvers": [], "keys": {}, "unknown_tags": [],
                        "any_visible": False, "checked_at": None}
    assert env.queries == []


def test_parent_ds_report(env):
    env.enable(resolvers=("1.1.1.1", "8.8.8.8", "9.9.9.9"))
    env.ds_answer("1.1.1.1", env.old["ds"][1])
    env.ds_answer("9.9.9.9", UNKNOWN_DS)
    c = env.client()
    r = c.get(f"{B}/parent-ds")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["enabled"] is True and d["any_visible"] is True and d["checked_at"]
    rows = {x["resolver"]: x for x in d["resolvers"]}
    assert rows["1.1.1.1"]["label"] == "Cloudflare" and rows["1.1.1.1"]["key_tags"] == [tag(env.old)]
    assert rows["8.8.8.8"]["status"] == "nodata"
    assert d["keys"][str(env.old["id"])] == {"key_tag": tag(env.old), "visible_on": ["1.1.1.1"],
                                             "missing_on": ["8.8.8.8", "9.9.9.9"]}
    assert d["keys"][str(env.new["id"])]["visible_on"] == [] and str(env.zsk["id"]) not in d["keys"]
    assert d["unknown_tags"] == [12345]
    assert all(q.rdtype == "DS" and q.recursion and q.name == Z for q in env.queries)
    assert all(q.commits >= 1 for q in env.queries)  # L6: Session vor der Netzphase freigegeben
    assert "privatekey" not in r.text


def test_part_b_access_and_errors(env):
    env.enable()
    stranger = make_user(role="user", uid=5, username="eve")
    assert env.client(user=stranger).get(f"{B}/parent-ds").status_code == 403
    assert env.client(user=stranger).get(f"{B}/dnskey-check").status_code == 403
    assert env.queries == []
    reader = make_user(role="user", uid=6, username="bob")
    assert env.client(user=reader, zone_access=[(Z, "read")]).get(f"{B}/parent-ds").status_code == 200
    c = env.client()
    assert c.get(f"{A}/nope/{Z}/parent-ds").status_code == 404
    r = c.get(f"{A}/srv1/missing.example./dnskey-check")
    assert r.status_code == 404 and "PowerDNS (srv1)" in r.json()["detail"]


def test_dnskey_check_disabled_returns_expected_tags(env):
    r = env.client().get(f"{B}/dnskey-check")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is False and d["nameservers"] == {} and d["all_ok"] is False
    assert d["expected_tags"] == sorted({tag(env.old), tag(env.new), tag(env.zsk)})
    assert env.queries == []


def test_dnskey_check_against_glue(env):
    env.enable()
    env.answers[("192.0.2.53", "DNSKEY")] = prop.DnsAnswer(rcode="NOERROR", aa=True, values=["x"],
                                                           key_tags=[tag(env.old), tag(env.zsk)])
    c = env.client()
    r = c.get(f"{B}/dnskey-check", params={"key_tag": tag(env.new)})
    assert r.status_code == 200, r.text
    d = r.json()
    ns = d["nameservers"]["ns1.example.com."]
    assert d["expected_tags"] == [tag(env.new)] and d["all_ok"] is False
    assert ns["ok"] is False and ns["missing_tags"] == [tag(env.new)]
    assert [(q.server, q.rdtype, q.recursion) for q in env.queries] == [("192.0.2.53", "DNSKEY", False)]
    assert env.queries[0].commits >= 1

    env.answers[("192.0.2.53", "DNSKEY")].key_tags = [tag(env.old), tag(env.new)]
    prop.reset_for_tests()
    d = c.get(f"{B}/dnskey-check", params={"key_tag": [tag(env.new), tag(env.old)]}).json()
    assert d["all_ok"] is True and d["nameservers"]["ns1.example.com."]["ok"] is True


def test_dnskey_check_validates_key_tags(env):
    c = env.client()
    assert c.get(f"{B}/dnskey-check", params={"key_tag": 70000}).status_code == 422
    assert c.get(f"{B}/dnskey-check", params={"key_tag": list(range(13))}).status_code == 422
    assert c.get(f"{B}/dnskey-check", params={"key_tag": "abc"}).status_code == 422


def test_part_b_rate_limit(env, monkeypatch):
    env.enable()
    monkeypatch.setattr(prop, "RATE_MAX", 1)
    c = env.client()
    assert c.get(f"{B}/parent-ds").status_code == 200
    r = c.get(f"{B}/dnskey-check")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "DNS-Prüfungen" in r.json()["detail"]


# ------------------------------------------------------------------ Deaktivieren (Spec 3.7 Nr. 2)
def test_disable_blocked_while_parent_publishes_ds(env):
    env.enable(resolvers=("1.1.1.1", "8.8.8.8"))
    env.ds_answer("1.1.1.1", env.old["ds"][1])
    c = env.client()
    r = c.post(f"{B}/disable", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "parent_ds_present" and detail["force_possible"] is True
    assert "Cloudflare (1.1.1.1)" in detail["message"] and Z in detail["message"]
    assert len(env.pdns.keys[Z]) == 3 and env.audits("DNSSEC_DISABLE") == [] and env.events.events() == []


def test_disable_force_records_present_forced(env):
    env.enable(resolvers=("1.1.1.1",))
    env.ds_answer("1.1.1.1", env.old["ds"][2])
    c = env.client()
    r = c.post(f"{B}/disable", json={"force": True})
    assert r.status_code == 200, r.text
    assert env.pdns.keys[Z] == []
    (audit,) = env.audits("DNSSEC_DISABLE")
    assert audit.details["parent_ds"] == "present_forced" and audit.details["force"] is True


@pytest.mark.parametrize("setup, expected", [
    ("off", "not_checked"), ("nodata", "none"), ("unknown_only", "none"), ("timeout", "not_checked"),
])
def test_disable_parent_ds_audit_value(env, setup, expected):
    if setup != "off":
        env.enable(resolvers=("8.8.8.8",))
    if setup == "unknown_only":
        env.ds_answer("8.8.8.8", UNKNOWN_DS)
    if setup == "timeout":
        env.answers[("8.8.8.8", "DS")] = prop.DnsAnswer(error_code="timeout")
    c = env.client()
    r = c.post(f"{B}/disable", json={})
    assert r.status_code == 200, r.text
    (audit,) = env.audits("DNSSEC_DISABLE")
    assert audit.details["parent_ds"] == expected
    assert (env.queries == []) == (setup == "off")


def test_disable_rate_limited(env, monkeypatch):
    env.enable()
    monkeypatch.setattr(prop, "RATE_MAX", 1)
    prop.consume_rate(env.user.id)  # Kontingent verbraucht (z. B. durch den Propagations-Tab)
    c = env.client()
    assert c.post(f"{B}/disable", json={}).status_code == 429
    assert len(env.pdns.keys[Z]) == 3
    r = c.post(f"{B}/disable", json={"force": True})
    assert r.status_code == 200
    assert env.audits("DNSSEC_DISABLE")[0].details["parent_ds"] == "not_checked"


def test_disable_without_sep_keys_skips_check(env):
    env.enable()
    env.pdns.keys[Z] = [k for k in env.pdns.keys[Z] if k["keytype"] == "zsk"]
    r = env.client().post(f"{B}/disable", json={})
    assert r.status_code == 200 and env.queries == []
