"""F0-Zonenfixes (WS-F2F3): SOA nach dem Anlegen (rname, Serial [D13]) und ``allow_writes`` bei ``POST /zones``
mit explizit gewaehlten Servern. Ohne Datenbank (``authfakes``).
"""
from datetime import datetime

import pytest
from fastapi import HTTPException

from authfakes import FakePDNS, FakeSession, make_user
from app.models.models import ServerConfig
from app.routers import zones
from app.schemas.dns import ZoneCreate
from app.services import audit as audit_service
from app.services import webhook_outbox
from app.services.pdns_client import pdns_manager

TODAY = datetime(2026, 10, 5)
DEFAULT_SOA = "a.misconfigured.dns.server.invalid. hostmaster.example.com. 0 10800 3600 604800 3600"


# --- SOA-Inhalt -------------------------------------------------------------------------------------------------
def test_soa_rname_is_hostmaster_even_with_two_nameservers():
    soa = zones.build_created_zone_soa(DEFAULT_SOA, "example.com.", ["ns1.example.com", "ns2.example.com."],
                                       today=TODAY)
    assert soa == "ns1.example.com. hostmaster.example.com. 2026100501 10800 3600 604800 3600"
    assert "ns2.example.com" not in soa


def test_soa_keeps_existing_higher_serial_and_timers():
    current = "ns0.example.net. hostmaster.example.net. 2026100517 7200 900 1209600 300"
    soa = zones.build_created_zone_soa(current, "Example.COM.", ["ns1.example.com."], today=TODAY)
    assert soa == "ns1.example.com. hostmaster.example.com. 2026100517 7200 900 1209600 300"
    # auch ein Serial aus einem anderen Schema (z. B. Unix-Zeit) bleibt unveraendert
    current = "x. y. 1759600000 10800 3600 604800 3600"
    assert zones.build_created_zone_soa(current, "example.com.", ["ns1."], today=TODAY).split()[2] == "1759600000"


def test_soa_zero_or_unreadable_serial_becomes_today01():
    assert zones.build_created_zone_soa(DEFAULT_SOA, "example.com", ["ns1.example.com."],
                                        today=TODAY).split()[2] == "2026100501"
    bad = zones.build_created_zone_soa("kaputt", "example.com", ["ns1.example.com."], today=TODAY)
    assert bad == "ns1.example.com. hostmaster.example.com. 2026100501 10800 3600 604800 3600"
    assert zones.build_created_zone_soa(None, "example.com", ["ns1."], today=TODAY).split()[2] == "2026100501"


def test_soa_without_nameservers_is_left_alone():
    assert zones.build_created_zone_soa(DEFAULT_SOA, "example.com.", [], today=TODAY) is None
    assert zones.build_created_zone_soa(DEFAULT_SOA, "example.com.", ["  "], today=TODAY) is None


class _SoaClient:
    def __init__(self, soa_content: str, ttl: int = 3600):
        self.zone = {"name": "example.com.", "rrsets": [
            {"name": "example.com.", "type": "SOA", "ttl": ttl, "records": [{"content": soa_content}]},
            {"name": "example.com.", "type": "NS", "ttl": 3600, "records": [{"content": "ns1.example.com."}]},
        ]}
        self.writes = []
        self.dnssec = []

    async def get_zone(self, zone_id, **kw):
        return self.zone

    async def add_record(self, **kw):
        self.writes.append(kw)

    async def enable_dnssec(self, zone_id):
        self.dnssec.append(zone_id)


async def test_update_zone_soa_writes_only_mname_rname():
    client = _SoaClient("a.misconfigured.dns.server.invalid. hostmaster.example.com. 2026100503 10800 3600 604800 3600",
                        ttl=1800)
    data = ZoneCreate(name="example.com", nameservers=["ns1.example.com.", "ns2.example.com."], enable_dnssec=True)
    await zones._update_zone_soa_and_dnssec(client, "srv1", "example.com.", data)
    (w,) = client.writes
    assert w["record_type"] == "SOA" and w["ttl"] == 1800 and w["name"] == "example.com."
    assert w["content"] == ["ns1.example.com. hostmaster.example.com. 2026100503 10800 3600 604800 3600"]
    assert client.dnssec == ["example.com."]  # zones.py:139 bleibt client.enable_dnssec (Plan [F7])


async def test_update_zone_soa_skips_write_when_unchanged():
    client = _SoaClient("ns1.example.com. hostmaster.example.com. 2026100503 10800 3600 604800 3600")
    data = ZoneCreate(name="example.com", nameservers=["ns1.example.com."])
    await zones._update_zone_soa_and_dnssec(client, "srv1", "example.com.", data)
    assert client.writes == [] and client.dnssec == []


# --- allow_writes bei POST /zones mit explizitem servers ---------------------------------------------------------
@pytest.fixture
def servers(monkeypatch):
    fakes = {"rw": FakePDNS("rw"), "ro": FakePDNS("ro"), "env": FakePDNS("env")}
    monkeypatch.setattr(pdns_manager, "clients", dict(fakes))
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    events = []

    async def enqueue(db, event, **kw):
        events.append((event, kw))
        return 1

    async def detached(*a, **k):
        return None

    monkeypatch.setattr(webhook_outbox, "enqueue_event", enqueue)
    monkeypatch.setattr(audit_service, "write_audit_detached", detached)
    configs = [
        ServerConfig(id=1, name="rw", url="http://rw:8081", api_key="k", is_active=True, allow_writes=True),
        ServerConfig(id=2, name="ro", url="http://ro:8081", api_key="k", is_active=True, allow_writes=False),
    ]  # "env" hat keine DB-Zeile -> gilt als schreibbar (nur per PDNS_SERVERS bekannt)
    return fakes, FakeSession(extra={ServerConfig: configs}), events


async def test_create_zone_explicit_read_only_server_is_skipped(servers):
    fakes, db, events = servers
    data = ZoneCreate(name="neu.example", nameservers=["ns1.example.com."], servers=["ro", "rw", "env"])
    res = await zones.create_zone(data, db, make_user())
    assert res.details == {"ro": "skipped (read-only)", "rw": "created", "env": "created"}
    assert fakes["ro"].calls.get("create_zone") is None
    assert fakes["rw"].calls.get("create_zone") == 1 and fakes["env"].calls.get("create_zone") == 1
    assert [e for e, _ in events] == ["zone.created"]


async def test_create_zone_only_read_only_servers_is_403(servers):
    fakes, db, events = servers
    with pytest.raises(HTTPException) as e:
        await zones.create_zone(ZoneCreate(name="neu.example", servers=["ro"]), db, make_user())
    assert e.value.status_code == 403 and "'ro'" in e.value.detail
    assert fakes["ro"].calls == {} and events == []

