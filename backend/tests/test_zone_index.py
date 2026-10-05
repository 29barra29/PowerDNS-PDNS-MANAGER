"""Tests fuer services/zone_index.py (F9 9)."""
import pytest

from app.services import fanout, zone_index
from app.services.pdns_client import PowerDNSAPIError
from fakes.pdns import FakeDB, fake_pdns, make_zone, server_config  # noqa: F401 - Fixture


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    zone_index.invalidate()
    clock = {"t": 1000.0}
    monkeypatch.setattr(zone_index, "_now", lambda: clock["t"])
    yield clock
    zone_index.invalidate()


@pytest.fixture
def servers(fake_pdns):
    fake_pdns.ns1.add_zone(make_zone("example.com."))
    fake_pdns.ns1.add_zone(make_zone("home.example.com."))
    fake_pdns.ns1.add_zone(make_zone("2.0.192.in-addr.arpa."))
    fake_pdns.ns2.add_zone(make_zone("example.com."))
    fake_pdns.ns2.add_zone(make_zone("other.org."))
    return fake_pdns


async def test_cache_hit_within_ttl(servers, _clean_cache):
    db = FakeDB()
    await zone_index.writable_zone_map(db)
    _clean_cache["t"] += 59
    zmap = await zone_index.writable_zone_map(db)
    assert servers.ns1.count("GET", "/zones") == 1 and servers.ns2.count("GET", "/zones") == 1
    assert zmap["ns2"] == frozenset({"example.com.", "other.org."})
    _clean_cache["t"] += 2  # TTL abgelaufen
    await zone_index.writable_zone_map(db)
    assert servers.ns1.count("GET", "/zones") == 2


async def test_invalidate_forces_reload(servers):
    db = FakeDB()
    await zone_index.writable_zone_map(db)
    zone_index.invalidate("ns1")
    await zone_index.writable_zone_map(db)
    assert servers.ns1.count("GET", "/zones") == 2 and servers.ns2.count("GET", "/zones") == 1
    zone_index.invalidate()
    await zone_index.writable_zone_map(db)
    assert servers.ns2.count("GET", "/zones") == 2


async def test_list_timeout_is_used(servers):
    await zone_index.writable_zone_map(FakeDB())
    assert [c[4] for c in servers.ns1.calls if c[1] == "/zones"] == [zone_index.LIST_TIMEOUT]


async def test_server_error_uses_stale_cache_then_drops(servers, _clean_cache):
    db = FakeDB()
    await zone_index.writable_zone_map(db)
    orig = servers.ns2._request

    async def failing(method, endpoint, json_data=None, params=None, timeout=30.0):
        if endpoint == "/zones":
            raise PowerDNSAPIError(503, "Cannot connect", "ns2")
        return await orig(method, endpoint, json_data, params, timeout)

    servers.ns2._request = failing
    _clean_cache["t"] += 120  # Cache abgelaufen, aber < STALE_MAX
    zmap = await zone_index.writable_zone_map(db)
    assert zmap["ns2"] == frozenset({"example.com.", "other.org."})
    _clean_cache["t"] += zone_index.STALE_MAX  # zu alt -> Server wird ignoriert
    zmap = await zone_index.writable_zone_map(db)
    assert "ns2" not in zmap and "ns1" in zmap


async def test_only_writable_servers(servers):
    db = FakeDB([server_config("ns1"), server_config("ns2", allow_writes=False)])
    zmap = await zone_index.writable_zone_map(db)
    assert list(zmap) == ["ns1"]
    assert servers.ns2.count("GET", "/zones") == 0
    assert await zone_index.find_zone(db, "x.other.org") is None


async def test_find_zone_longest_match_and_servers(servers):
    db = FakeDB()
    m = await zone_index.find_zone(db, "router.HOME.example.com")
    assert m == zone_index.ZoneMatch("home.example.com.", ("ns1",), ())
    m = await zone_index.find_zone(db, "www.example.com.")
    assert m.zone == "example.com." and m.servers == ("ns1", "ns2")
    assert await zone_index.find_zone(db, "nothing.invalid.") is None


async def test_find_zone_arpa_only(servers):
    db = FakeDB()
    m = await zone_index.find_zone(db, "5.2.0.192.in-addr.arpa.", arpa_only=True)
    assert m.zone == "2.0.192.in-addr.arpa." and m.servers == ("ns1",)
    assert await zone_index.find_zone(db, "www.example.com.", arpa_only=True) is None
    assert await zone_index.all_zone_names(db, arpa_only=True) == {"2.0.192.in-addr.arpa."}
    assert await zone_index.all_zone_names(db) == {"example.com.", "home.example.com.",
                                                   "2.0.192.in-addr.arpa.", "other.org."}


async def test_unloaded_servers_reported(servers, monkeypatch):
    monkeypatch.setattr(servers.manager, "unloaded", {"ns3": "api key unreadable"})
    m = await zone_index.find_zone(FakeDB(), "www.example.com.")
    assert m.unloaded == ("ns3",)
    assert zone_index.unloaded_servers() == {"ns3": "api key unreadable"}


async def test_uses_fanout_module_attribute(servers, monkeypatch):
    async def only_ns2(db):
        return ["ns2"]

    monkeypatch.setattr(fanout, "writable_server_names", only_ns2)
    assert list(await zone_index.writable_zone_map(FakeDB())) == ["ns2"]


async def test_empty_index(fake_pdns):
    assert await zone_index.all_zone_names(FakeDB()) == set()
    assert await zone_index.find_zone(FakeDB(), "www.example.com.") is None
