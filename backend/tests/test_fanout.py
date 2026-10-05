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
