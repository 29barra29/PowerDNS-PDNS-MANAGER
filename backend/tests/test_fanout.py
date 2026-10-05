"""Tests fuer services/pdns_client.py (B.11) und services/fanout.py (B.5) [D1, D3, D4, D5]."""
import json

import httpx
import pytest

from app.services import pdns_client
from app.services.pdns_client import (
    PowerDNSAPIError,
    PowerDNSClient,
    PowerDNSManager,
    pdns_error_text,
)


# =============================================================== pdns_client
@pytest.fixture
def mock_transport(monkeypatch):
    """Ersetzt httpx.AsyncClient in pdns_client durch einen Client mit MockTransport."""
    real = httpx.AsyncClient
    state = {"handler": None, "requests": []}

    def handler(req):
        state["requests"].append(req)
        return state["handler"](req)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(pdns_client.httpx, "AsyncClient", factory)
    return state


@pytest.mark.parametrize("exc_cls", [httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError])
async def test_pdns_client_transport_error(mock_transport, exc_cls):
    """F1 9.4-13: Transportfehler nach dem Verbindungsaufbau -> PowerDNSAPIError(502), transport_error."""
    def h(req):
        raise exc_cls("weg", request=req)

    mock_transport["handler"] = h
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")
    with pytest.raises(PowerDNSAPIError) as ei:
        await c.update_records("example.com.", [{"name": "a.example.com.", "type": "A", "changetype": "DELETE"}])
    e = ei.value
    assert e.status_code == 502
    assert e.transport_error is True
    assert e.server == "ns1"
    assert "Verbindungsfehler zu PowerDNS-Server 'ns1'" in e.detail
    assert exc_cls.__name__ in e.detail
    assert "bitte Zone neu laden" in e.detail


async def test_pdns_client_timeout_is_transport_error(mock_transport):
    def h(req):
        raise httpx.ReadTimeout("zu langsam", request=req)

    mock_transport["handler"] = h
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")
    with pytest.raises(PowerDNSAPIError) as ei:
        await c.get_zone("example.com.", timeout=1.0)
    assert ei.value.status_code == 504 and ei.value.transport_error is True


async def test_pdns_client_http_error_keeps_status_and_body(mock_transport):
    mock_transport["handler"] = lambda req: httpx.Response(422, json={"error": "RRset a.example.com. IN CNAME: Conflicts"})
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")
    with pytest.raises(PowerDNSAPIError) as ei:
        await c.update_records("example.com.", [])
    e = ei.value
    assert e.status_code == 422 and e.transport_error is False
    assert e.pdns_message == "RRset a.example.com. IN CNAME: Conflicts"
    assert pdns_error_text(e) == "PowerDNS (ns1): RRset a.example.com. IN CNAME: Conflicts"


def test_pdns_message_fallbacks():
    assert PowerDNSAPIError(500, "  roher Text  ", "s").pdns_message == "roher Text"
    assert PowerDNSAPIError(500, json.dumps({"x": 1}), "s").pdns_message == '{"x": 1}'
    assert PowerDNSAPIError(500, "x" * 900, "s").pdns_message == "x" * 500
    assert PowerDNSAPIError(500, None, "s").pdns_message == "None"
    assert PowerDNSAPIError(404, json.dumps({"error": "Could not find domain 'x.'"}), "a").pdns_message == "Could not find domain 'x.'"


async def test_read_helpers_send_expected_params(mock_transport):
    zone = {
        "name": "example.com.",
        "rrsets": [
            {"name": "www.example.com.", "type": "A", "ttl": 60, "records": [{"content": "192.0.2.1", "disabled": False}]},
            {"name": "www.example.com.", "type": "AAAA", "ttl": 60, "records": [{"content": "2001:db8::1", "disabled": False}]},
            {"name": "other.example.com.", "type": "A", "ttl": 60, "records": [{"content": "192.0.2.2", "disabled": False}]},
        ],
    }
    mock_transport["handler"] = lambda req: httpx.Response(200, json=zone)
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")

    await c.get_zone_meta("example.com.")
    assert mock_transport["requests"][-1].url.params["rrsets"] == "false"

    await c.get_zone_rrset("example.com.", "WWW.example.com", "a")
    p = mock_transport["requests"][-1].url.params
    assert p["rrset_name"] == "www.example.com." and p["rrset_type"] == "A"

    rr = await c.get_rrsets("example.com.", "www.example.com.", "A")
    assert [r["type"] for r in rr] == ["A"]  # aeltere PowerDNS liefern alles -> lokal gefiltert
    rr_all = await c.get_rrsets("example.com.", "WWW.EXAMPLE.COM")
    assert sorted(r["type"] for r in rr_all) == ["A", "AAAA"]
    assert "rrset_type" not in mock_transport["requests"][-1].url.params


async def test_notify_zone_returns_dict(mock_transport):
    mock_transport["handler"] = lambda req: httpx.Response(200, json={"result": "Notification queued"})
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")
    assert await c.notify_zone("example.com.") == {"result": "Notification queued"}
    req = mock_transport["requests"][-1]
    assert req.method == "PUT" and req.url.path.endswith("/zones/example.com./notify")
    mock_transport["handler"] = lambda req: httpx.Response(204)
    assert await c.notify_zone("example.com.") == {}
    mock_transport["handler"] = lambda req: httpx.Response(200, text="ok", headers={"content-type": "text/plain"})
    assert await c.notify_zone("example.com.") == {"result": "ok"}


async def test_cryptokey_and_nsec3_helpers(mock_transport):
    mock_transport["handler"] = lambda req: httpx.Response(204)
    c = PowerDNSClient("ns1", "http://pdns.invalid:8081", "k")
    await c.update_cryptokey("example.com.", 7, {"active": False, "published": True})
    req = mock_transport["requests"][-1]
    assert req.method == "PUT" and req.url.path.endswith("/zones/example.com./cryptokeys/7")
    assert json.loads(req.content) == {"active": False, "published": True}
    await c.set_nsec3("example.com.", "1 0 0 -", True)
    body = json.loads(mock_transport["requests"][-1].content)
    assert body == {"nsec3param": "1 0 0 -", "nsec3narrow": True, "api_rectify": True}
    await c.set_nsec3("example.com.", "", True)
    body = json.loads(mock_transport["requests"][-1].content)
    assert body["nsec3narrow"] is False and body["nsec3param"] == ""


async def test_timeout_parameters_are_passed(monkeypatch):
    seen = []

    async def fake_request(self, method, endpoint, json_data=None, params=None, timeout=30.0):
        seen.append((method, endpoint, timeout))
        return {}

    monkeypatch.setattr(PowerDNSClient, "_request", fake_request)
    c = PowerDNSClient("ns1", "http://x", "k")
    await c.get_zone("z.", timeout=3.0)
    await c.update_records("z.", [], timeout=4.0)
    await c.get_cryptokeys("z.", timeout=5.0)
    await c.get_config(timeout=6.0)
    await c.notify_zone("z.")
    await c.get_rrsets("z.", "a.z.")
    assert [t for _, _, t in seen] == [3.0, 4.0, 5.0, 6.0, 10.0, 10.0]


def test_manager_unloaded_bookkeeping():
    m = PowerDNSManager.__new__(PowerDNSManager)
    m.clients = {}
    m.unloaded = {}
    m.add_server("a", "http://a", "k")
    m.mark_unloaded("a", "api key unreadable")
    assert "a" not in m.clients and m.unloaded == {"a": "api key unreadable"}
    m.update_server("a", "http://a", "k2")
    assert "a" in m.clients and m.unloaded == {}
    m.mark_unloaded("b", "api key empty")
    m.remove_server("b")
    assert m.unloaded == {}


class _Cfg:
    def __init__(self, name, api_key, is_active=True, url="http://pdns"):
        self.name, self.api_key, self.is_active, self.url = name, api_key, is_active, url


@pytest.mark.wave_integration
def test_load_from_db_configs_skips_unreadable_and_empty():
    """Braucht core.secrets (W0-SECRETS, paralleler Workstream der Welle 0a)."""
    from app.core.secrets import UNREADABLE

    m = PowerDNSManager.__new__(PowerDNSManager)
    m.clients = {"ns1": PowerDNSClient("ns1", "http://env", "envkey")}
    m.unloaded = {}
    skipped = m.load_from_db_configs([
        _Cfg("ns1", UNREADABLE),
        _Cfg("ns2", "   "),
        _Cfg("ns3", "geheim"),
        _Cfg("ns4", UNREADABLE, is_active=False),
    ])
    assert skipped == ["ns1", "ns2"]
    assert "ns1" not in m.clients  # DB ist fuehrend, Env-Client gleichen Namens wird entfernt
    assert m.unloaded == {"ns1": "api key unreadable", "ns2": "api key empty"}
    assert m.clients["ns3"].api_key == "geheim"
    assert "ns4" not in m.unloaded


# =============================================================== fanout
from fakes.pdns import (  # noqa: E402
    FakeDB,
    FakePowerDNSClient,
    fake_db,  # noqa: F401 - Fixture
    fake_pdns,  # noqa: F401 - Fixture
    make_zone,
    rr,
    server_config,
)
from app.services import fanout  # noqa: E402
from app.services.fanout import (  # noqa: E402
    STATUS_NO_CHANGES,
    STATUS_NO_MATCH,
    STATUS_UNCLEAR,
    STATUS_ZONE_MISSING,
    apply_rrsets,
    create_builder,
    delete_builder,
    update_builder,
)

Z = "example.com."
WWW = "www.example.com."


@pytest.fixture(autouse=True)
def _no_reread_delay(monkeypatch):
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)


@pytest.fixture
def two_backends(fake_pdns):
    """ns1 (Primary) und ns2 (Peer) mit getrennten Zonenstaenden; ns2 hat einen eigenen Zusatzwert."""
    fake_pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", ttl=300),
                                          rr("mail.example.com.", "A", "192.0.2.25")]))
    fake_pdns.ns2.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", "198.51.100.99", ttl=300,
                                            comments=[{"content": "peer", "account": "ops"}])]))
    return fake_pdns


# ---------------------------------------------------------------- Ziele / Schreibbarkeit
async def test_targets_primary_first_and_read_only_info(fake_pdns, monkeypatch):
    ns3 = FakePowerDNSClient("ns3")
    monkeypatch.setitem(fake_pdns.manager.clients, "ns3", ns3)
    db = FakeDB([server_config("ns1"), server_config("ns2", allow_writes=False), server_config("ns3")])
    targets, info = await fanout.writable_targets_for_zone(db, Z, "ns3")
    assert [n for n, _ in targets] == ["ns3", "ns1"]
    assert info == {"ns2": "read-only"}


async def test_targets_primary_read_only_or_unknown(fake_pdns):
    db = FakeDB([server_config("ns1", allow_writes=False)])
    assert await fanout.writable_targets_for_zone(db, Z, "ns1") == ([], {"ns1": "read-only"})
    targets, info = await fanout.writable_targets_for_zone(FakeDB(), Z, "nsX")
    assert targets == [] and "nsX" in info["nsX"]


async def test_targets_db_error_treats_all_writable(fake_pdns):
    db = FakeDB([server_config("ns2", allow_writes=False)])
    db.fail_execute = RuntimeError("db weg")
    targets, info = await fanout.writable_targets_for_zone(db, Z, "ns1")
    assert [n for n, _ in targets] == ["ns1", "ns2"] and info == {}


async def test_targets_inactive_config_rows_are_ignored(fake_pdns):
    db = FakeDB([server_config("ns2", allow_writes=False, is_active=False)])
    targets, _ = await fanout.writable_targets_for_zone(db, Z, "ns1")
    assert [n for n, _ in targets] == ["ns1", "ns2"]


async def test_targets_report_unloaded_servers(fake_pdns, monkeypatch):
    """[D4] konfigurierter, aber nicht geladener Server -> info 'not loaded: <grund>'."""
    monkeypatch.setattr(fake_pdns.manager, "unloaded", {"ns3": "api key unreadable", "ns4": "api key empty"})
    db = FakeDB([server_config("ns1"), server_config("ns2"), server_config("ns3"),
                 server_config("ns4", allow_writes=False), server_config("ns5")])
    targets, info = await fanout.writable_targets_for_zone(db, Z, "ns1")
    assert [n for n, _ in targets] == ["ns1", "ns2"]
    assert info == {"ns3": "not loaded: api key unreadable", "ns5": "not loaded: unknown"}


async def test_targets_unloaded_primary(fake_pdns, monkeypatch):
    monkeypatch.setattr(fake_pdns.manager, "unloaded", {"ns9": "api key unreadable"})
    targets, info = await fanout.writable_targets_for_zone(FakeDB(), Z, "ns9")
    assert targets == [] and info == {"ns9": "not loaded: api key unreadable"}


async def test_unloaded_without_db_rows_still_reported(fake_pdns, monkeypatch):
    monkeypatch.setattr(fake_pdns.manager, "unloaded", {"ns3": "api key unreadable"})
    db = FakeDB()
    db.fail_execute = RuntimeError("db weg")
    _, info = await fanout.writable_targets_for_zone(db, Z, "ns1")
    assert info == {"ns3": "not loaded: api key unreadable"}


async def test_allow_writes_helpers(fake_pdns):
    db = FakeDB([server_config("ns1"), server_config("ns2", allow_writes=False)])
    assert await fanout.allow_writes_map(db) == {"ns1": True, "ns2": False}
    assert await fanout.writable_server_names(db) == ["ns1"]
    assert await fanout.is_server_writable(db, "ns1") is True
    assert await fanout.is_server_writable(db, "ns2") is False
    assert await fanout.is_server_writable(db, "nsX") is False
    assert await fanout.is_server_writable(FakeDB(), "ns2") is True  # env-only
    broken = FakeDB()
    broken.fail_execute = RuntimeError("x")
    assert await fanout.is_server_writable(broken, "ns2") is True
    assert await fanout.writable_server_names(broken) == ["ns1", "ns2"]


@pytest.mark.parametrize("status,detail,expected", [
    (404, "anything", True),
    (422, '{"error": "Could not find domain \'x.\'"}', True),
    (422, "No such zone", True),
    (422, "RRset www.example.com. IN CNAME: Conflicts with pre-existing RRset", False),
    (502, "Verbindungsfehler ... (ReadError) – Ergebnis unklar", False),
    (500, "zone not found in backend", False),
])
def test_zone_not_found_for(status, detail, expected):
    exc = PowerDNSAPIError(status, detail, "ns1")
    assert fanout.zone_not_found_for(exc) is expected
    assert fanout.zone_not_found(exc) is expected


def test_read_only_error_and_summary():
    e = fanout.read_only_error("ns2")
    assert e.status_code == 403 and "'ns2' ist auf 'Speichern: Nein' gesetzt" in e.detail
    assert fanout.summarize_results({"ns1": "saved"}, {"ns2": "read-only", "ns1": "x"}) == {
        "ns1": "saved", "ns2": "skipped (read-only)"}


# ---------------------------------------------------------------- Betriebsart B: Peer-Erhalt [D1]
async def test_create_keeps_peer_extra_value(two_backends, fake_db):
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]),
                             read_rrset=(WWW, "A"))
    assert res.summary == {"ns1": "saved", "ns2": "saved"}
    assert res.primary_success and res.primary_outcome == "ok" and res.any_success
    assert two_backends.ns1.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2"]
    assert two_backends.ns2.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2", "198.51.100.99"]
    # Kommentare des Peers bleiben (kein comments-Key gesendet)
    assert two_backends.ns2.rrset(Z, WWW, "A")["comments"] == [{"content": "peer", "account": "ops"}]
    # je Server gesendete RRsets unterscheiden sich
    assert len(res.per_server_rrsets["ns2"][0]["records"]) == 3
    assert len(res.per_server_rrsets["ns1"][0]["records"]) == 2


async def test_update_keeps_peer_extra_value(two_backends, fake_db):
    res = await apply_rrsets(fake_db, "ns1", Z, build=update_builder(WWW, "A", "192.0.2.1", "192.0.2.3", 600))
    assert res.summary == {"ns1": "saved", "ns2": "saved"}
    assert two_backends.ns1.values(Z, WWW, "A") == ["192.0.2.3"]
    assert two_backends.ns2.values(Z, WWW, "A") == ["192.0.2.3", "198.51.100.99"]
    assert two_backends.ns2.rrset(Z, WWW, "A")["ttl"] == 600


async def test_update_missing_value_on_peer_is_skipped(two_backends, fake_db):
    two_backends.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.50", ttl=300)]))
    res = await apply_rrsets(fake_db, "ns1", Z, build=update_builder(WWW, "A", "192.0.2.50", "192.0.2.51", 300))
    assert res.summary == {"ns1": "saved", "ns2": STATUS_NO_MATCH}
    assert two_backends.ns2.values(Z, WWW, "A") == ["192.0.2.1", "198.51.100.99"]
    assert two_backends.ns2.count("PATCH") == 0


async def test_update_missing_value_on_primary_stops(two_backends, fake_db):
    res = await apply_rrsets(fake_db, "ns1", Z, build=update_builder(WWW, "A", "203.0.113.1", "203.0.113.2", 300))
    assert res.results == {"ns1": STATUS_NO_MATCH}
    assert res.primary_success is False and res.primary_error is None and res.primary_status == STATUS_NO_MATCH
    assert two_backends.ns2.count("GET") == 0


async def test_delete_value_keeps_peer_extra_value(two_backends, fake_db):
    res = await apply_rrsets(fake_db, "ns1", Z, build=delete_builder(WWW, "A", "192.0.2.1"), success_status="deleted")
    assert res.summary == {"ns1": "deleted", "ns2": "deleted"}
    assert two_backends.ns1.rrset(Z, WWW, "A") is None  # letzter Wert -> RRset weg
    assert two_backends.ns2.values(Z, WWW, "A") == ["198.51.100.99"]
    assert two_backends.ns2.rrset(Z, WWW, "A")["comments"] == [{"content": "peer", "account": "ops"}]
    assert res.per_server_rrsets["ns1"][0]["changetype"] == "DELETE"
    assert res.per_server_rrsets["ns2"][0]["changetype"] == "REPLACE"


async def test_delete_whole_rrset_and_missing_value(two_backends, fake_db):
    res = await apply_rrsets(fake_db, "ns1", Z, build=delete_builder("mail.example.com.", "A", "203.0.113.9"),
                             success_status="deleted")
    assert res.results == {"ns1": STATUS_NO_MATCH}
    res = await apply_rrsets(fake_db, "ns1", Z, build=delete_builder(WWW, "A"), success_status="deleted")
    assert res.summary == {"ns1": "deleted", "ns2": "deleted"}
    assert two_backends.ns2.rrset(Z, WWW, "A") is None


async def test_peer_without_zone_is_skipped(fake_pdns, fake_db):
    fake_pdns.ns1.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1")]))
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 60, [{"content": "192.0.2.7"}]))
    assert res.summary == {"ns1": "saved", "ns2": STATUS_ZONE_MISSING}
    assert res.errors == {} and res.all_4xx is False


async def test_builder_empty_list_means_no_changes(two_backends, fake_db):
    calls = []

    def build(server, zone):
        calls.append(server)
        return [] if server == "ns1" else [{"name": WWW, "type": "A", "ttl": 300, "changetype": "REPLACE",
                                            "records": [{"content": "192.0.2.1", "disabled": False}]}]

    res = await apply_rrsets(fake_db, "ns1", Z, build=build)
    assert res.summary == {"ns1": STATUS_NO_CHANGES, "ns2": "saved"}
    assert res.primary_success and res.primary_outcome == "ok" and calls == ["ns1", "ns2"]
    assert two_backends.ns1.count("PATCH") == 0


async def test_builder_exception_peer_vs_primary(two_backends, fake_db):
    def bad_peer(server, zone):
        if server == "ns2":
            raise RuntimeError("kaputt")
        return []

    res = await apply_rrsets(fake_db, "ns1", Z, build=bad_peer)
    assert res.results["ns2"] == "error: kaputt"

    def bad_primary(server, zone):
        raise RuntimeError("primary kaputt")

    with pytest.raises(RuntimeError):
        await apply_rrsets(fake_db, "ns1", Z, build=bad_primary)


async def test_read_rrset_uses_filtered_read(two_backends, fake_db):
    await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.9"}]),
                       read_rrset=(WWW, "A"))
    gets = [c for c in two_backends.ns1.calls if c[0] == "GET"]
    assert gets and gets[0][3] == {"rrset_name": WWW, "rrset_type": "A"}


async def test_old_powerdns_without_filter_still_works(fake_pdns, fake_db):
    old = FakePowerDNSClient("ns1", [make_zone(Z, [rr(WWW, "A", "192.0.2.1"), rr("x.example.com.", "A", "192.0.2.5")])],
                             filter_rrsets=False)
    fake_pdns.manager.clients["ns1"] = old
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]),
                             read_rrset=(WWW, "A"))
    assert res.results["ns1"] == "saved"
    assert old.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2"]
    assert old.values(Z, "x.example.com.", "A") == ["192.0.2.5"]


# ---------------------------------------------------------------- Betriebsart A
async def test_mode_a_same_rrsets_everywhere(two_backends, fake_db):
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE",
                "records": [{"content": "203.0.113.5", "disabled": False}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload)
    assert res.summary == {"ns1": "saved", "ns2": "saved"}
    assert two_backends.ns1.values(Z, WWW, "A") == two_backends.ns2.values(Z, WWW, "A") == ["203.0.113.5"]
    assert two_backends.ns1.count("GET") == 0  # A liest nicht
    assert res.per_server_rrsets == {"ns1": payload, "ns2": payload}


def test_mode_arguments_validated():
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(apply_rrsets(FakeDB(), "ns1", Z))
    with pytest.raises(ValueError):
        import asyncio
        asyncio.run(apply_rrsets(FakeDB(), "ns1", Z, [], build=lambda s, z: []))


async def test_primary_error_blocks_peer_writes(two_backends, fake_db):
    two_backends.ns1.fail_on_patch = PowerDNSAPIError(422, '{"error": "RRset www: Conflicts"}', "ns1")
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload)
    assert res.results == {"ns1": 'error: {"error": "RRset www: Conflicts"}'}
    assert res.primary_success is False and res.primary_outcome == "failed"
    assert res.primary_error.status_code == 422 and res.all_4xx is True and res.any_success is False
    assert two_backends.ns2.count("PATCH") == 0


async def test_primary_zone_missing_blocks_peers(fake_pdns, fake_db):
    fake_pdns.ns2.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1")]))
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 60, [{"content": "192.0.2.2"}]))
    assert res.results == {"ns1": STATUS_ZONE_MISSING}
    assert res.primary_error.status_code == 404 and res.primary_success is False
    assert fake_pdns.ns2.count("PATCH") == 0


async def test_primary_read_error_blocks_peers(two_backends, fake_db):
    two_backends.ns1.fail_on_get = PowerDNSAPIError(503, "Cannot connect", "ns1")
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 60, [{"content": "192.0.2.2"}]))
    assert res.results == {"ns1": "error: Cannot connect"}
    assert res.all_4xx is False and two_backends.ns2.count("GET") == 0


# ---------------------------------------------------------------- require_primary=False / targets [D5]
async def test_require_primary_false_writes_peers(two_backends, fake_db):
    two_backends.ns1.fail_on_patch = PowerDNSAPIError(503, "Cannot connect", "ns1")
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload, require_primary=False)
    assert res.summary == {"ns1": "error: Cannot connect", "ns2": "saved"}
    assert res.primary_success is False and res.any_success is True and res.all_4xx is False
    assert two_backends.ns2.values(Z, WWW, "A") == ["203.0.113.5"]


async def test_explicit_targets_limit_writes(fake_pdns, fake_db, monkeypatch):
    ns3 = FakePowerDNSClient("ns3", [make_zone(Z)])
    monkeypatch.setitem(fake_pdns.manager.clients, "ns3", ns3)
    for c in (fake_pdns.ns1, fake_pdns.ns2):
        c.add_zone(make_zone(Z))
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns2", Z, payload, targets=[("ns3", ns3), ("ns2", fake_pdns.ns2)],
                             require_primary=False, info={"ns9": "not loaded: api key unreadable"})
    assert list(res.results) == ["ns2", "ns3"]  # Primary zuerst
    assert fake_pdns.ns1.count("PATCH") == 0
    assert res.summary["ns9"] == "skipped (not loaded: api key unreadable)"


async def test_targets_without_primary(fake_pdns, fake_db):
    fake_pdns.ns2.add_zone(make_zone(Z))
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload, targets=[("ns2", fake_pdns.ns2)])
    assert res.results == {} and fake_pdns.ns2.count("PATCH") == 0
    res = await apply_rrsets(fake_db, "ns1", Z, payload, targets=[("ns2", fake_pdns.ns2)], require_primary=False)
    assert res.results == {"ns2": "saved"} and res.primary_success is False


async def test_empty_targets_read_only_primary(fake_pdns):
    db = FakeDB([server_config("ns1", allow_writes=False)])
    res = await apply_rrsets(db, "ns1", Z, [{"name": WWW, "type": "A", "changetype": "DELETE"}])
    assert res.results == {} and res.info == {"ns1": "read-only"} and res.primary_success is False
    assert res.summary == {"ns1": "skipped (read-only)"}


async def test_unloaded_server_in_every_result(two_backends, monkeypatch):
    """[D4] Nicht geladener Server erscheint als 'skipped (not loaded: …)'."""
    monkeypatch.setattr(two_backends.manager, "unloaded", {"ns3": "api key unreadable"})
    db = FakeDB([server_config("ns1"), server_config("ns2"), server_config("ns3")])
    res = await apply_rrsets(db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]))
    assert res.summary == {"ns1": "saved", "ns2": "saved", "ns3": "skipped (not loaded: api key unreadable)"}


# ---------------------------------------------------------------- Timeout nach PATCH [D3]
@pytest.mark.parametrize("status", [504, 502])
async def test_timeout_after_patch_verified_mode_b(two_backends, fake_db, status):
    two_backends.ns1.timeout_after_patch = True
    two_backends.ns1.timeout_status = status
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]))
    assert res.primary_outcome == "verified_after_timeout"
    assert res.primary_success is True and res.primary_error is None
    assert res.summary == {"ns1": "saved", "ns2": "saved"}  # Peers werden geschrieben
    assert two_backends.ns2.values(Z, WWW, "A") == ["192.0.2.1", "192.0.2.2", "198.51.100.99"]
    rereads = [c for c in two_backends.ns1.calls if c[0] == "GET" and c[4] == fanout.REREAD_TIMEOUT]
    assert rereads, "Nachpruefung mit frischem 5-s-Timeout"


async def test_timeout_before_patch_reread_equals_before_failed(two_backends, fake_db):
    two_backends.ns1.timeout_before_patch = True
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]))
    assert res.primary_outcome == "failed"
    assert res.primary_success is False and res.primary_error.status_code == 504
    assert res.results == {"ns1": "error: Timeout connecting to PowerDNS server 'ns1'"}
    assert two_backends.ns2.count("PATCH") == 0 and two_backends.ns2.count("GET") == 0
    assert two_backends.ns1.values(Z, WWW, "A") == ["192.0.2.1"]


async def test_mode_a_timeout_after_patch_verified(two_backends, fake_db):
    two_backends.ns1.timeout_after_patch = True
    two_backends.ns1.timeout_status = 502
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE",
                "records": [{"content": "203.0.113.5", "disabled": False}]},
               {"name": "mail.example.com.", "type": "A", "changetype": "DELETE"}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload)
    assert res.primary_outcome == "verified_after_timeout" and res.summary == {"ns1": "saved", "ns2": "saved"}


async def test_mode_a_not_applied_without_before_state_is_unknown(two_backends, fake_db):
    two_backends.ns1.timeout_before_patch = True
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload)
    assert res.primary_outcome == "unknown"
    assert res.results == {"ns1": STATUS_UNCLEAR} and res.primary_success is False
    assert two_backends.ns2.count("PATCH") == 0


async def test_mode_a_not_applied_with_before_state_failed(two_backends, fake_db):
    from app.services.rrsets import rrset_snapshot

    two_backends.ns1.timeout_before_patch = True
    before = {(WWW, "A"): rrset_snapshot(two_backends.ns1.zone(Z), WWW, "A")}
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload, before_state=before)
    assert res.primary_outcome == "failed" and two_backends.ns2.count("PATCH") == 0


async def test_state_differs_from_expected_and_before_is_unknown(two_backends, fake_db):
    two_backends.ns1.timeout_before_patch = True
    stale_before = {(WWW, "A"): {"ttl": 300, "records": [{"content": "10.9.9.9", "disabled": False}]}}
    payload = [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE", "records": [{"content": "203.0.113.5"}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload, before_state=stale_before)
    assert res.primary_outcome == "unknown" and res.results["ns1"] == STATUS_UNCLEAR


async def test_reread_retries_then_verifies(two_backends, fake_db):
    two_backends.ns1.timeout_after_patch = True
    two_backends.ns1.fail_reads = 0

    orig_patch = two_backends.ns1._patch

    def patch_then_break_reads(zid, zone, rrsets):
        try:
            return orig_patch(zid, zone, rrsets)
        finally:
            two_backends.ns1.fail_reads = 2  # die ersten 2 Re-Reads scheitern

    two_backends.ns1._patch = patch_then_break_reads
    res = await apply_rrsets(fake_db, "ns1", Z, [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE",
                                                  "records": [{"content": "203.0.113.5"}]}])
    assert res.primary_outcome == "verified_after_timeout"


async def test_reread_fails_three_times_unknown(two_backends, fake_db):
    orig_patch = two_backends.ns1._patch

    def patch_then_break_reads(zid, zone, rrsets):
        try:
            return orig_patch(zid, zone, rrsets)
        finally:
            two_backends.ns1.fail_reads = 3

    two_backends.ns1._patch = patch_then_break_reads
    two_backends.ns1.timeout_after_patch = True
    res = await apply_rrsets(fake_db, "ns1", Z, [{"name": WWW, "type": "A", "ttl": 60, "changetype": "REPLACE",
                                                  "records": [{"content": "203.0.113.5"}]}])
    assert res.primary_outcome == "unknown" and two_backends.ns2.count("PATCH") == 0


async def test_expected_after_override(two_backends, fake_db):
    """Aufrufer kann den erwarteten Endzustand vorgeben (z. B. von PowerDNS normalisierte Inhalte)."""
    two_backends.ns1.timeout_after_patch = True
    two_backends.ns1.normalize_content = lambda t, c: c.lower()
    payload = [{"name": WWW, "type": "TXT", "ttl": 60, "changetype": "REPLACE", "records": [{"content": '"ABC"'}]}]
    expected = [{"name": WWW, "type": "TXT", "ttl": 60, "changetype": "REPLACE", "records": [{"content": '"abc"'}]}]
    res = await apply_rrsets(fake_db, "ns1", Z, payload, expected_after=expected)
    assert res.primary_outcome == "verified_after_timeout"


async def test_large_change_set_rereads_whole_zone(two_backends, fake_db, monkeypatch):
    monkeypatch.setattr(fanout, "REREAD_ZONE_THRESHOLD", 1)
    two_backends.ns1.timeout_after_patch = True
    payload = [{"name": f"h{i}.example.com.", "type": "A", "ttl": 60, "changetype": "REPLACE",
                "records": [{"content": f"192.0.2.{i}"}]} for i in range(1, 4)]
    res = await apply_rrsets(fake_db, "ns1", Z, payload)
    assert res.primary_outcome == "verified_after_timeout"
    rereads = [c for c in two_backends.ns1.calls if c[0] == "GET" and c[4] == fanout.REREAD_TIMEOUT]
    assert len(rereads) == 1 and rereads[0][3] == {}


async def test_peer_timeout_is_plain_error(two_backends, fake_db):
    two_backends.ns2.timeout_after_patch = True
    res = await apply_rrsets(fake_db, "ns1", Z, build=create_builder(WWW, "A", 300, [{"content": "192.0.2.2"}]))
    assert res.results["ns2"].startswith("error: Timeout")
    assert res.primary_outcome == "ok" and res.all_4xx is False


async def test_real_delete_record_semantics_unchanged(two_backends):
    """Der Fake laesst die echte delete_record-Logik des Clients laufen."""
    await two_backends.ns2.delete_record(Z, WWW, "A", content="198.51.100.99")
    assert two_backends.ns2.values(Z, WWW, "A") == ["192.0.2.1"]
    assert two_backends.ns2.rrset(Z, WWW, "A")["comments"] == [{"content": "peer", "account": "ops"}]
