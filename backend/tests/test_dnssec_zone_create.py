"""DNSSEC beim Anlegen einer Zone (F4 5.7/9, Plan WS-F4-B [F7]) – ohne DB.

``create_zone`` richtet DNSSEC ueber ``dnssec_service.enable_dnssec_on_new_zone`` nur auf dem ersten Server ein, auf
dem die Zone tatsaechlich angelegt wurde (``created``); weitere angelegte Server melden ``created; dnssec-skipped``,
``synced`` (409, gemeinsame Datenbank) bleibt unberuehrt, ein DNSSEC-Fehler ergibt ``created; dnssec-error: <text>``.
PowerDNS: ``dnssec_fakes.FakePdnsClient`` (Zonen per ``POST /zones``, Cryptokey-API). Nur DNSSEC-Assertions – der
SOA-Teil ist in ``test_zones_f0.py`` abgedeckt.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from dnssec_fakes import DetachedAudits, EventRecorder, FakePdnsClient, pdns_error
from app.core.auth import get_admin_user, get_current_user
from app.models.models import AuditLog
from app.routers import zones
from app.schemas.dns import ZoneCreate
from app.services import audit as audit_service
from app.services import dnssec_service as svc
from app.services import webhook_outbox
from app.services.pdns_client import PowerDNSClient, pdns_manager

ZONE = "neu.example."
NS = ["ns1.example.com.", "ns2.example.com."]


class Env:
    def __init__(self, monkeypatch, *servers: FakePdnsClient):
        self.servers = {s.name: s for s in servers}
        monkeypatch.setattr(pdns_manager, "clients", dict(self.servers))
        monkeypatch.setattr(pdns_manager, "unloaded", {})
        self.events = EventRecorder()
        monkeypatch.setattr(webhook_outbox, "enqueue_event", self.events)
        self.detached = DetachedAudits()
        monkeypatch.setattr(audit_service, "write_audit_detached", self.detached)
        svc.reset_version_cache()
        self.admin = make_user()
        self.session = FakeSession(user_row=self.admin)

    async def create(self, **body):
        data = ZoneCreate(**{"name": "neu.example", "nameservers": NS, **body})
        return await zones.create_zone(data, self.session, self.admin)

    def audits(self, action: str | None = None) -> list[AuditLog]:
        return [o for o in self.session.added if isinstance(o, AuditLog) and (action is None or o.action == action)]


def _server(name: str, **kw) -> FakePdnsClient:
    return FakePdnsClient(name, zones=(), **kw)


# =============================================================================================
# Verteilung auf die Server
# =============================================================================================
async def test_two_created_servers_dnssec_only_on_first(monkeypatch):
    ns1, ns2 = _server("ns1"), _server("ns2")
    env = Env(monkeypatch, ns1, ns2)
    res = await env.create(enable_dnssec=True, servers=["ns1", "ns2"])
    assert res.details == {"ns1": "created", "ns2": "created; dnssec-skipped"}
    assert ns1.bodies("POST", "/cryptokeys") == [{"keytype": "csk", "active": True, "algorithm": "ECDSAP256SHA256"}]
    assert ns1.bodies("PUT", f"/zones/{ZONE}") == [{"nsec3param": "1 0 0 -", "nsec3narrow": False, "api_rectify": True}]
    assert "rectify_zone" in ns1.ops
    assert ns2.key_ids(ZONE) == [] and "add_cryptokey" not in ns2.ops and "update_zone" not in ns2.ops
    # Audits: CREATE je Server (dnssec-Details), DNSSEC_ENABLE nur fuer ns1 (source=zone_create)
    creates = env.audits("CREATE")
    assert [a.server_name for a in creates] == ["ns1", "ns2"]
    d1, d2 = (a.details["dnssec"] for a in creates)
    assert d1 == {"enabled": True, "server": "ns1", "options": {
        "nsec_mode": "nsec3", "nsec3_iterations": 0, "nsec3_salt": "-", "nsec3_optout": False, "nsec3narrow": False,
        "key_model": "csk", "algorithm": "ECDSAP256SHA256", "bits": None}}
    assert d2["server"] == "ns1" and d2["skipped"] is True
    (enable,) = env.audits("DNSSEC_ENABLE")
    assert enable.server_name == "ns1" and enable.zone_name == ZONE and enable.details["source"] == "zone_create"
    assert enable.details["keys"][0]["key_id"] == ns1.key_ids(ZONE)[0]
    # Ereignisse: zone.created vor dnssec.enabled; zone.created meldet das Anlege-Ergebnis
    assert env.events.events() == ["zone.created", "dnssec.enabled"]
    created_ev, dnssec_ev = env.events.calls
    assert created_ev["data"]["dnssec"] is True and created_ev["data"]["results"] == {"ns1": "created", "ns2": "created"}
    assert dnssec_ev["server"] == "ns1" and dnssec_ev["audit_log_id"] == enable.id
    assert "GEHEIM" not in json.dumps(dnssec_ev["data"])
    assert env.detached.entries == []


async def test_first_synced_second_created_gets_dnssec(monkeypatch):
    ns1, ns2 = _server("ns1"), _server("ns2")
    ns1.add_test_zone(ZONE)  # gemeinsame bzw. schon vorhandene Zone -> 409 -> "synced"
    env = Env(monkeypatch, ns1, ns2)
    res = await env.create(enable_dnssec=True, servers=["ns1", "ns2"])
    assert res.details == {"ns1": "synced", "ns2": "created"}
    assert ns1.key_ids(ZONE) == [] and "add_cryptokey" not in ns1.ops
    assert len(ns2.key_ids(ZONE)) == 1 and ns2.key(ZONE, ns2.key_ids(ZONE)[0])["active"] is True
    (enable,) = env.audits("DNSSEC_ENABLE")
    assert enable.server_name == "ns2"
    synced, created = env.audits("CREATE")
    assert "dnssec" not in synced.details and created.details["dnssec"]["server"] == "ns2"


async def test_only_synced_servers_no_dnssec(monkeypatch):
    ns1 = _server("ns1")
    ns1.add_test_zone(ZONE)
    env = Env(monkeypatch, ns1)
    res = await env.create(enable_dnssec=True, servers=["ns1"])
    assert res.details == {"ns1": "synced"}
    assert "add_cryptokey" not in ns1.ops and env.audits("DNSSEC_ENABLE") == []
    assert env.events.events() == ["zone.created"]


async def test_dnssec_error_keeps_zone_and_reports(monkeypatch):
    ns1 = _server("ns1", fail_on={"add_cryptokey": pdns_error(422, "kein Algorithmus", "ns1")})
    ns2 = _server("ns2")
    env = Env(monkeypatch, ns1, ns2)
    res = await env.create(enable_dnssec=True, servers=["ns1", "ns2"])
    assert res.details == {
        "ns1": "created; dnssec-error: Schlüssel anlegen (CSK): PowerDNS (ns1): kein Algorithmus",
        "ns2": "created; dnssec-skipped",
    }
    assert ZONE in ns1.zones and ZONE in ns2.zones  # Zone bleibt angelegt
    (entry,) = env.detached.entries
    assert entry.action == "DNSSEC_ENABLE" and entry.status == "error" and entry.server_name == "ns1"
    assert entry.details["source"] == "zone_create" and entry.details["rollback"] == "ok"
    assert entry.zone_name == ZONE
    assert env.audits("DNSSEC_ENABLE") == []
    assert env.events.events() == ["zone.created"]  # kein dnssec.enabled
    assert "add_cryptokey" not in ns2.ops


async def test_dnssec_error_on_nsec3_step_rolls_back_keys(monkeypatch):
    ns1 = _server("ns1", fail_on={"update_zone#1": pdns_error(422, "NSEC3 kaputt", "ns1")})
    env = Env(monkeypatch, ns1)
    res = await env.create(enable_dnssec=True, servers=["ns1"])
    assert res.details["ns1"].startswith("created; dnssec-error: NSEC/NSEC3 setzen: PowerDNS (ns1): NSEC3 kaputt")
    assert ns1.key_ids(ZONE) == []  # angelegter Schluessel wieder entfernt
    (entry,) = env.detached.entries
    assert entry.details["step"] == "NSEC/NSEC3 setzen" and entry.details["rollback"] == "ok"


async def test_dnssec_options_rsa_ksk_zsk_nsec(monkeypatch):
    ns1 = _server("ns1")
    env = Env(monkeypatch, ns1)
    res = await env.create(enable_dnssec=True, servers=["ns1"], dnssec_options={
        "key_model": "ksk_zsk", "algorithm": "RSASHA256", "bits": 3072, "nsec_mode": "nsec"})
    assert res.details == {"ns1": "created"}
    assert ns1.bodies("POST", "/cryptokeys") == [
        {"keytype": "ksk", "active": True, "algorithm": "RSASHA256", "bits": 3072},
        {"keytype": "zsk", "active": True, "algorithm": "RSASHA256", "bits": 3072},
    ]
    assert ns1.bodies("PUT", f"/zones/{ZONE}") == [{"nsec3param": "", "nsec3narrow": False, "api_rectify": True}]
    (create,) = env.audits("CREATE")
    opts = create.details["dnssec"]["options"]
    assert opts["key_model"] == "ksk_zsk" and opts["bits"] == 3072 and opts["nsec_mode"] == "nsec"
    assert "nsec3param" not in opts
    (enable,) = env.audits("DNSSEC_ENABLE")
    assert [k["keytype"] for k in enable.details["keys"]] == ["ksk", "zsk"]


async def test_rsa_default_bits_sent(monkeypatch):
    ns1 = _server("ns1")
    env = Env(monkeypatch, ns1)
    await env.create(enable_dnssec=True, servers=["ns1"], dnssec_options={"algorithm": "rsasha512"})
    assert ns1.bodies("POST", "/cryptokeys") == [
        {"keytype": "csk", "active": True, "algorithm": "RSASHA512", "bits": 2048}]


async def test_without_enable_dnssec_options_are_ignored(monkeypatch):
    ns1 = _server("ns1")
    env = Env(monkeypatch, ns1)
    res = await env.create(servers=["ns1"], dnssec_options={"algorithm": "ED25519"})
    assert res.details == {"ns1": "created"}
    assert "add_cryptokey" not in ns1.ops and "get_zone_meta" not in ns1.ops
    (create,) = env.audits("CREATE")
    assert create.details["dnssec"] is False
    assert env.events.events() == ["zone.created"]


async def test_read_only_server_skipped_dnssec_on_first_writable(monkeypatch):
    from types import SimpleNamespace

    from app.models.models import ServerConfig

    ro, rw = _server("ro"), _server("rw")
    env = Env(monkeypatch, ro, rw)
    env.session = FakeSession(user_row=env.admin, extra={ServerConfig: [
        SimpleNamespace(name="ro", allow_writes=False, is_active=True),
        SimpleNamespace(name="rw", allow_writes=True, is_active=True),
    ]})
    res = await env.create(enable_dnssec=True, servers=["ro", "rw"])
    assert res.details == {"ro": "skipped (read-only)", "rw": "created"}
    assert ro.ops == [] and len(rw.key_ids(ZONE)) == 1


async def test_master_zone_create_without_serial_bump(monkeypatch):
    ns1 = _server("ns1")
    env = Env(monkeypatch, ns1)
    res = await env.create(enable_dnssec=True, servers=["ns1"], kind="Master")
    assert res.details == {"ns1": "created"}
    # Neue Zone: kein SOA-Bump/NOTIFY aus dem DNSSEC-Dienst (nur bei Schluesselaenderungen bestehender Zonen)
    assert "notify_zone" not in ns1.ops and "update_records" not in ns1.ops
    assert env.audits("ZONE_NOTIFY") == []


# =============================================================================================
# HTTP: Validierung der Optionen und Antwort
# =============================================================================================
@pytest.fixture
def http(monkeypatch):
    ns1, ns2 = _server("ns1"), _server("ns2")
    env = Env(monkeypatch, ns1, ns2)
    app = build_app(env.session, zones)
    app.dependency_overrides[get_current_user] = lambda: env.admin
    app.dependency_overrides[get_admin_user] = lambda: env.admin
    return env, TestClient(app, raise_server_exceptions=False)


def test_http_invalid_dnssec_options_422_without_calls(http):
    env, c = http
    r = c.post("/api/v1/zones", json={"name": "neu.example", "nameservers": NS, "enable_dnssec": True,
                                      "dnssec_options": {"algorithm": "RSASHA1"}})
    assert r.status_code == 422 and "nicht unterstützt" in r.text
    r = c.post("/api/v1/zones", json={"name": "neu.example", "enable_dnssec": True,
                                      "dnssec_options": {"nsec3_iterations": 51}})
    assert r.status_code == 422 and "zwischen 0 und 50" in r.text
    assert all(s.ops == [] for s in env.servers.values())


def test_http_create_with_dnssec(http):
    env, c = http
    r = c.post("/api/v1/zones", json={"name": "neu.example", "nameservers": NS, "enable_dnssec": True,
                                      "servers": ["ns1", "ns2"],
                                      "dnssec_options": {"algorithm": "ECDSAP384SHA384", "nsec3_salt": "AB"}})
    assert r.status_code == 200, r.text
    assert r.json()["details"] == {"ns1": "created", "ns2": "created; dnssec-skipped"}
    ns1 = env.servers["ns1"]
    assert ns1.bodies("POST", "/cryptokeys") == [{"keytype": "csk", "active": True, "algorithm": "ECDSAP384SHA384"}]
    assert ns1.bodies("PUT", f"/zones/{ZONE}")[0]["nsec3param"] == "1 0 0 ab"
    assert "privatekey" not in r.text


# =============================================================================================
# pdns_client: Alt-Orchestrierung entfernt (Plan [F7])
# =============================================================================================
def test_legacy_dnssec_methods_removed_from_client():
    for name in ("enable_dnssec", "disable_dnssec", "activate_cryptokey", "deactivate_cryptokey"):
        assert not hasattr(PowerDNSClient, name), name
    for name in ("get_cryptokeys", "add_cryptokey", "get_cryptokey", "update_cryptokey", "delete_cryptokey",
                 "set_nsec3", "rectify_zone"):
        assert callable(getattr(PowerDNSClient, name)), name


def test_zones_router_has_no_legacy_dnssec_call():
    import inspect

    src = inspect.getsource(zones)
    assert ".enable_dnssec(" not in src and "_update_zone_soa_and_dnssec" not in src
    assert "enable_dnssec_on_new_zone" in src
