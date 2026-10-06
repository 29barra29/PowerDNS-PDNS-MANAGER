"""DNSSEC-Router und -Service (F4 9 Router Nr. 1–19, Serial-Erhoehung [D10]) – ohne DB.

TestClient gegen eine Mini-App nur mit dem DNSSEC-Router (``authfakes.build_app``), ``FakeSession`` als DB,
``dnssec_fakes.FakePdnsClient`` als PowerDNS, ``enqueue_event`` und ``write_audit_detached`` aufgezeichnet.
Erfolgs-Audits landen als ``AuditLog`` in ``session.added`` (``write_audit`` in der Request-Session).
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from dnssec_fakes import PRIVATE, DetachedAudits, EventRecorder, FakePdnsClient, pdns_error
from app.core.auth import get_current_user
from app.models.models import AuditLog, ServerConfig
from app.routers import dnssec as dnssec_router
from app.schemas.dnssec import DNSSECEnable
from app.services import audit as audit_service
from app.services import dnssec_service as svc
from app.services import webhook_outbox
from app.services.dnssec_parse import compute_key_tag
from app.services.pdns_client import pdns_manager

A = "/api/v1/dnssec"
Z = "example.com."
B = f"{A}/srv1/{Z}"
READ_ONLY_TEXT = "Speichern: Nein"


class Env:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.pdns = FakePdnsClient("srv1")
        self.servers = {"srv1": self.pdns}
        monkeypatch.setattr(pdns_manager, "clients", self.servers)
        monkeypatch.setattr(pdns_manager, "unloaded", {})
        self.events = EventRecorder()
        monkeypatch.setattr(webhook_outbox, "enqueue_event", self.events)
        self.detached = DetachedAudits()
        monkeypatch.setattr(audit_service, "write_audit_detached", self.detached)
        svc.reset_version_cache()
        self.user = make_user()
        self.session: FakeSession | None = None

    def use(self, pdns: FakePdnsClient) -> FakePdnsClient:
        self.pdns = pdns
        self.servers["srv1"] = pdns
        return pdns

    def add_server(self, pdns: FakePdnsClient) -> FakePdnsClient:
        self.servers[pdns.name] = pdns
        return pdns

    def client(self, *, user=None, zone_access=None, server_configs=None, audit_rows=None) -> TestClient:
        user = user or self.user
        extra = {}
        if server_configs is not None:
            extra[ServerConfig] = server_configs
        if audit_rows is not None:
            extra[AuditLog] = audit_rows
        self.session = FakeSession(user_row=user, zone_access=zone_access, extra=extra)
        app = build_app(self.session, dnssec_router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def audits(self) -> list[AuditLog]:
        return [o for o in self.session.added if isinstance(o, AuditLog)]

    def actions(self) -> list[str]:
        return [a.action for a in self.audits()]


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def detail(r):
    return r.json()["detail"]


def read_only_config(name="srv1"):
    return [SimpleNamespace(name=name, allow_writes=False, is_active=True)]


# =============================================================================================
# Nr. 1–7 Aktivieren
# =============================================================================================
def test_enable_defaults(env):
    c = env.client()
    r = c.post(f"{B}/enable", json={})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("POST", "/cryptokeys") == [{"keytype": "csk", "active": True, "algorithm": "ECDSAP256SHA256"}]
    assert env.pdns.bodies("PUT", f"/zones/{Z}") == [{"nsec3param": "1 0 0 -", "nsec3narrow": False, "api_rectify": True}]
    assert "rectify_zone" in env.pdns.ops
    assert "privatekey" not in r.text and "GEHEIM" not in r.text
    d = r.json()["details"]
    assert d["already_enabled"] is False and d["nsec3param"] == "1 0 0 -" and d["warnings"] == []
    assert d["key"]["id"] == 1 and d["key"]["key_tag"] == compute_key_tag(env.pdns.key(Z, 1)["dnskey"])
    assert d["ds_records"] == env.pdns.key(Z, 1)["ds"]
    assert d["serial_bumped"] is False and d["notified"] is False  # Native-Zone: keine Serial-Erhoehung
    assert r.json()["message"] == f"DNSSEC für Zone '{Z}' auf 'srv1' aktiviert"
    (audit,) = env.audits()
    assert audit.action == "DNSSEC_ENABLE" and audit.resource_type == "dnssec_key" and audit.zone_name == Z
    assert audit.details["keys"][0]["key_tag"] == d["key"]["key_tag"]
    assert audit.details["source"] == "dnssec_enable" and audit.details["key_model"] == "csk"
    assert env.events.events() == ["dnssec.enabled"]
    ev = env.events.calls[0]
    assert ev["audit_log_id"] == audit.id and ev["zone"] == Z and ev["server"] == "srv1"
    assert ev["data"]["keys"][0]["id"] == 1 and ev["data"]["keys"][0]["key_tag"] == d["key"]["key_tag"]
    assert ev["data"]["nsec3param"] == "1 0 0 -" and "GEHEIM" not in json.dumps(ev)


def test_enable_ksk_zsk_rsa_bits(env):
    c = env.client()
    r = c.post(f"{B}/enable", json={"key_model": "ksk_zsk", "algorithm": "rsasha256", "bits": 3072})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("POST", "/cryptokeys") == [
        {"keytype": "ksk", "active": True, "algorithm": "RSASHA256", "bits": 3072},
        {"keytype": "zsk", "active": True, "algorithm": "RSASHA256", "bits": 3072},
    ]
    assert [k["keytype"] for k in r.json()["details"]["keys"]] == ["ksk", "zsk"]


def test_enable_nsec(env):
    c = env.client()
    assert c.post(f"{B}/enable", json={"nsec_mode": "NSEC"}).status_code == 200
    assert env.pdns.bodies("PUT", f"/zones/{Z}") == [{"nsec3param": "", "nsec3narrow": False, "api_rectify": True}]


def test_enable_legacy_nsec3param_and_validation(env):
    c = env.client()
    assert c.post(f"{B}/enable", json={"nsec3param": "1 0 1 AB", "nsec3narrow": True}).status_code == 200
    assert env.pdns.bodies("PUT", f"/zones/{Z}") == [{"nsec3param": "1 0 1 ab", "nsec3narrow": True, "api_rectify": True}]
    r = c.post(f"{B}/enable", json={"nsec3param": "2 0 0 -"})
    assert r.status_code == 422 and "SHA-1" in r.text
    r = c.post(f"{B}/enable", json={"nsec3_iterations": 51})
    assert r.status_code == 422 and "zwischen 0 und 50" in r.text
    r = c.post(f"{B}/enable", json={"algorithm": "RSASHA1"})
    assert r.status_code == 422 and "wird nicht unterstützt" in r.text
    r = c.post(f"{B}/enable", json={"algorithm": "ECDSAP256SHA256", "bits": 2048})
    assert r.status_code == 422 and "nur bei RSA" in r.text
    r = c.post(f"{B}/enable", json={"nsec3_salt": "xyz"})
    assert r.status_code == 422 and "Hex-Zeichen" in r.text


def test_enable_warnings_for_nsec3_parameters(env):
    r = env.client().post(f"{B}/enable", json={"nsec3_iterations": 12, "nsec3_salt": "ab"})
    assert r.status_code == 200
    assert r.json()["details"]["warnings"] == ["nsec3_iterations_high", "nsec3_salt"]


def test_enable_idempotent_when_signed(env):
    env.pdns.add_key(Z)
    c = env.client()
    r = c.post(f"{B}/enable")
    assert r.status_code == 200, r.text
    d = r.json()["details"]
    assert d["already_enabled"] is True and d["key"] is None and [k["id"] for k in d["keys"]] == [1]
    assert "bereits aktiv" in r.json()["message"]
    assert "add_cryptokey" not in env.pdns.ops and env.pdns.writes() == []
    assert env.audits() == [] and env.events.calls == []


def test_enable_conflict_with_only_inactive_keys(env):
    env.pdns.add_key(Z, active=False)
    r = env.client().post(f"{B}/enable", json={})
    assert r.status_code == 409
    assert "existieren bereits 1 Schlüssel, aber keiner ist aktiv" in detail(r)
    assert env.pdns.writes() == [] and env.detached.entries == []


def test_enable_rollback_on_nsec3_error(env):
    env.pdns.fail_on["update_zone#1"] = pdns_error(422, "NSEC3 kaputt")
    r = env.client().post(f"{B}/enable", json={})
    assert r.status_code == 422
    assert "wieder entfernt" in detail(r) and "NSEC/NSEC3 setzen" in detail(r)
    assert "PowerDNS (srv1): NSEC3 kaputt" in detail(r)
    assert env.pdns.writes() == ["add_cryptokey", "update_zone", "delete_cryptokey", "update_zone"]
    assert env.pdns.key_ids(Z) == []
    (err,) = env.detached.entries
    assert err.action == "DNSSEC_ENABLE" and err.status == "error" and err.zone_name == Z
    assert err.details["rollback"] == "ok" and err.details["step"] == "NSEC/NSEC3 setzen"
    assert err.error_message == "PowerDNS (srv1): NSEC3 kaputt"
    assert env.audits() == [] and env.events.calls == []


def test_enable_rollback_failure_is_reported(env):
    env.pdns.fail_on["rectify_zone"] = pdns_error(500, "rectify kaputt")
    env.pdns.fail_on["delete_cryptokey"] = pdns_error(500, "delete kaputt")
    r = env.client().post(f"{B}/enable", json={})
    assert r.status_code == 500
    assert "Aufräumen fehlgeschlagen" in detail(r) and "bitte die Schlüssel der Zone prüfen" in detail(r)
    assert env.detached.entries[0].details["rollback"].startswith("failed: Schlüssel 1")


# =============================================================================================
# Nr. 8–10 Schreibschutz, PRESIGNED, Rechte
# =============================================================================================
WRITE_CALLS = [
    ("POST", f"{B}/enable", {}),
    ("POST", f"{B}/disable", None),
    ("POST", f"{B}/keys", {"keytype": "csk"}),
    ("PUT", f"{B}/keys/1", {"active": False}),
    ("DELETE", f"{B}/keys/1", None),
    ("PUT", f"{B}/nsec3", {"nsec_mode": "nsec"}),
    ("POST", f"{B}/keys/1/activate", None),
    ("POST", f"{B}/keys/1/deactivate", None),
]


@pytest.mark.parametrize("method,path,body", WRITE_CALLS)
def test_read_only_server_blocks_all_writes(env, method, path, body):
    env.pdns.add_key(Z)
    c = env.client(server_configs=read_only_config())
    r = c.request(method, path, json=body)
    assert r.status_code == 403, r.text
    assert READ_ONLY_TEXT in detail(r)
    assert env.pdns.ops == [] and env.audits() == [] and env.detached.entries == [] and env.events.calls == []


@pytest.mark.parametrize("method,path,body", WRITE_CALLS)
def test_presigned_zone_blocks_all_writes(env, method, path, body):
    env.use(FakePdnsClient("srv1", presigned=True))
    env.pdns.add_key(Z)
    r = env.client().request(method, path, json=body)
    assert r.status_code == 409, r.text
    assert "vorsigniert (PRESIGNED)" in detail(r)
    assert env.pdns.writes() == []


def test_presigned_status_not_manageable(env):
    env.use(FakePdnsClient("srv1", presigned=True))
    s = env.client().get(f"{B}/status").json()
    assert s["presigned"] is True and s["manageable"] is False and s["can_write"] is False
    assert [h["code"] for h in s["hints"]] == ["presigned_zone"]


def test_reader_sees_status_but_cannot_write(env):
    env.pdns.add_key(Z)
    reader = make_user(role="user", uid=5, username="bob")
    c = env.client(user=reader, zone_access=[(Z, "read")])
    s = c.get(f"{B}/status")
    assert s.status_code == 200, s.text
    assert s.json()["user_can_write"] is False and s.json()["can_write"] is False
    assert s.json()["server_writable"] is True
    for method, path, body in WRITE_CALLS + [("GET", f"{B}/keys/1", None)]:
        r = c.request(method, path, json=body)
        assert r.status_code == 403 and detail(r) == "Nur Lese-Zugriff auf diese Zone", (method, path)
    assert env.pdns.writes() == []
    assert c.get(f"{B}/keys").status_code == 200 and c.get(f"{B}/ds").status_code == 200


def test_user_without_zone_access_gets_403(env):
    stranger = make_user(role="user", uid=6, username="eve")
    c = env.client(user=stranger, zone_access=[])
    for path in (f"{B}/status", f"{B}/keys", f"{B}/ds"):
        r = c.get(path)
        assert r.status_code == 403 and detail(r) == "Keine Berechtigung für diese Zone"
    assert env.pdns.ops == []


def test_manager_can_write(env):
    manager = make_user(role="user", uid=7, username="max")
    c = env.client(user=manager, zone_access=[(Z, "manage")])
    assert c.get(f"{B}/status").json()["can_write"] is True
    assert c.post(f"{B}/enable", json={}).status_code == 200


def test_unknown_server_404(env):
    r = env.client().get(f"{A}/nope/{Z}/status")
    assert r.status_code == 404 and "nope" in detail(r)


def test_pdns_errors_are_readable_and_hide_urls(env):
    env.pdns.fail_on["get_cryptokeys"] = pdns_error(422, "Could not find domain")
    r = env.client().get(f"{B}/status")
    assert r.status_code == 422 and detail(r) == "PowerDNS (srv1): Could not find domain"
    env.pdns.fail_on["get_cryptokeys"] = svc.PowerDNSAPIError(
        503, "Cannot connect to PowerDNS server 'srv1' at http://pdns-intern:8081", "srv1")
    r = env.client().get(f"{B}/keys")
    assert r.status_code == 503 and "pdns-intern" not in r.text
    assert detail(r) == "PowerDNS (srv1): Verbindung zum Server fehlgeschlagen."


# =============================================================================================
# Nr. 11 Schluessel anlegen
# =============================================================================================
def test_create_key_requires_existing_dnssec(env):
    r = env.client().post(f"{B}/keys", json={"keytype": "csk"})
    assert r.status_code == 409 and "nicht eingerichtet" in detail(r)
    assert env.pdns.writes() == []


def test_create_key_limit(env):
    for _ in range(6):
        env.pdns.add_key(Z, active=False)
    r = env.client().post(f"{B}/keys", json={"keytype": "zsk"})
    assert r.status_code == 409 and "bereits 6 Schlüssel (Maximum 6)" in detail(r)


def test_create_key_validation(env):
    env.pdns.add_key(Z)
    c = env.client()
    r = c.post(f"{B}/keys", json={"keytype": "csk", "privatekey": PRIVATE})
    assert r.status_code == 422
    r = c.post(f"{B}/keys", json={"keytype": "foo"})
    assert r.status_code == 422 and "keytype muss csk, ksk oder zsk sein." in r.text
    r = c.post(f"{B}/keys", json={"keytype": "csk", "algorithm": "RSASHA256", "bits": 1024})
    assert r.status_code == 422 and "2048, 3072 oder 4096" in r.text
    assert env.pdns.writes() == []


def test_create_key_published_needs_43(env):
    env.use(FakePdnsClient("srv1", version="4.2.0"))
    env.pdns.add_key(Z)
    c = env.client()
    r = c.post(f"{B}/keys", json={"keytype": "csk", "published": False})
    assert r.status_code == 422 and "4.3 oder neuer" in detail(r) and "4.2.0" in detail(r)
    assert env.pdns.writes() == []
    r = c.post(f"{B}/keys", json={"keytype": "KSK", "algorithm": "rsasha256"})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("POST", "/cryptokeys") == [
        {"keytype": "ksk", "active": False, "algorithm": "RSASHA256", "bits": 2048}]  # ohne published (< 4.3)


def test_create_key_success(env):
    env.pdns.add_key(Z)
    c = env.client()
    r = c.post(f"{B}/keys", json={"keytype": "csk", "algorithm": "ED25519", "active": False, "published": True})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("POST", "/cryptokeys") == [
        {"keytype": "csk", "active": False, "algorithm": "ED25519", "published": True}]
    d = r.json()["details"]
    assert "privatekey" not in r.text and d["key"]["id"] == 2 and d["key"]["role"] == "sep"
    assert d["warnings"] == ["algorithm_prepublish_unsigned"]
    assert r.json()["message"] == f"Schlüssel 2 für Zone '{Z}' angelegt"
    (audit,) = env.audits()
    assert audit.action == "KEY_CREATE" and audit.details["key_id"] == 2 and audit.details["algorithm_number"] == 15
    assert audit.details["warnings"] == ["algorithm_prepublish_unsigned"]
    assert env.events.events() == ["dnssec.key_created"]
    assert env.events.calls[0]["data"]["key_tag"] == d["key"]["key_tag"]
    # gleicher Algorithmus wie ein aktiver Schluessel -> keine Warnung
    r = c.post(f"{B}/keys", json={"keytype": "csk", "active": False})
    assert r.json()["details"]["warnings"] == []


def test_create_key_pdns_error_audited(env):
    env.pdns.add_key(Z)
    env.pdns.fail_on["add_cryptokey"] = pdns_error(422, "Creating algorithm 16 is not supported")
    r = env.client().post(f"{B}/keys", json={"keytype": "csk", "algorithm": "ed448"})
    assert r.status_code == 422 and detail(r) == "PowerDNS (srv1): Creating algorithm 16 is not supported"
    (err,) = env.detached.entries
    assert err.action == "KEY_CREATE" and err.status == "error" and err.details["algorithm"] == "ED448"
    assert env.events.calls == []


def test_create_key_reloads_when_response_lacks_dnskey(env, monkeypatch):
    env.pdns.add_key(Z)
    original = env.pdns.add_cryptokey

    async def sparse(zone_id, key_data):
        full = await original(zone_id, key_data)
        return {"id": full["id"], "privatekey": PRIVATE}

    monkeypatch.setattr(env.pdns, "add_cryptokey", sparse)
    r = env.client().post(f"{B}/keys", json={"keytype": "zsk"})
    assert r.status_code == 200
    assert r.json()["details"]["key"]["dnskey"] == env.pdns.key(Z, 2)["dnskey"]
    assert "privatekey" not in r.text


# =============================================================================================
# Nr. 12 PUT keys/{id}
# =============================================================================================
def test_put_key_protection_and_force(env):
    env.pdns.add_key(Z)
    c = env.client()
    r = c.put(f"{B}/keys/1", json={"active": False})
    assert r.status_code == 409
    d = detail(r)
    assert d["code"] == "last_active_key" and d["force_possible"] is True and "Schlüssel 1" in d["message"]
    assert env.pdns.writes() == [] and env.detached.entries == []
    r = c.put(f"{B}/keys/1", json={"active": False, "force": True})
    assert r.status_code == 200, r.text
    assert r.json()["details"]["overridden"] == ["last_active_key", "last_active_sep", "last_published_sep"]
    (audit,) = env.audits()
    assert audit.action == "KEY_DEACTIVATE" and audit.details["force"] is True
    assert audit.details["overridden"] == ["last_active_key", "last_active_sep", "last_published_sep"]
    assert audit.details["before"] == {"active": True, "published": True}
    assert audit.details["after"] == {"active": False, "published": True} and audit.details["via"] == "put"
    assert env.events.events() == ["dnssec.key_deactivated"]
    assert env.pdns.bodies("PUT", "/cryptokeys/1") == [{"active": False}]


def test_put_key_unchanged_empty_unknown(env):
    env.pdns.add_key(Z)
    c = env.client()
    r = c.put(f"{B}/keys/1", json={"active": True, "published": True})
    assert r.status_code == 200 and r.json()["details"]["unchanged"] is True
    assert "update_cryptokey" not in env.pdns.ops and env.audits() == []
    r = c.put(f"{B}/keys/1", json={})
    assert r.status_code == 422 and "Bitte active und/oder published angeben." in r.text
    r = c.put(f"{B}/keys/9", json={"active": True})
    assert r.status_code == 404 and detail(r) == f"Schlüssel 9 existiert in der Zone '{Z}' auf 'srv1' nicht."
    r = c.put(f"{B}/keys/1", json={"active": True, "privatekey": "x"})
    assert r.status_code == 422


def test_put_key_both_changed_is_key_update(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z)
    r = env.client().put(f"{B}/keys/2", json={"active": False, "published": False})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("PUT", "/cryptokeys/2") == [{"active": False, "published": False}]
    (audit,) = env.audits()
    assert audit.action == "KEY_UPDATE" and audit.details["overridden"] == []
    assert env.events.events() == ["dnssec.key_deactivated", "dnssec.key_unpublished"]
    assert {c["audit_log_id"] for c in env.events.calls} == {audit.id}


def test_put_key_publish_only(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False, published=False)
    r = env.client().put(f"{B}/keys/2", json={"published": True})
    assert r.status_code == 200
    assert env.pdns.bodies("PUT", "/cryptokeys/2") == [{"active": False, "published": True}]  # active immer mit
    assert env.actions() == ["KEY_PUBLISH"] and env.events.events() == ["dnssec.key_published"]


def test_put_key_pdns_42(env):
    env.use(FakePdnsClient("srv1", version="4.2.0"))
    env.pdns.add_key(Z)
    env.pdns.add_key(Z)
    c = env.client()
    r = c.put(f"{B}/keys/2", json={"published": False})
    assert r.status_code == 422 and "4.3 oder neuer" in detail(r)
    r = c.put(f"{B}/keys/2", json={"active": False})
    assert r.status_code == 200, r.text
    assert env.pdns.bodies("PUT", "/cryptokeys/2") == [{"active": False}]


def test_put_key_pdns_error_audited(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    env.pdns.fail_on["update_cryptokey"] = pdns_error(422, "nope")
    r = env.client().put(f"{B}/keys/2", json={"active": True})
    assert r.status_code == 422 and detail(r) == "PowerDNS (srv1): nope"
    assert env.detached.actions() == ["KEY_ACTIVATE"] and env.audits() == [] and env.events.calls == []


# =============================================================================================
# Nr. 13 Kompat-POSTs activate/deactivate
# =============================================================================================
def test_legacy_activate_deactivate(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    c = env.client()
    r = c.post(f"{B}/keys/1/deactivate")
    assert r.status_code == 409 and detail(r)["code"] == "last_active_key"
    r = c.post(f"{B}/keys/2/activate")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Schlüssel 2 geändert"
    r = c.post(f"{B}/keys/1/deactivate")
    assert r.status_code == 200
    r = c.post(f"{B}/keys/2/deactivate")
    assert r.status_code == 409
    r = c.post(f"{B}/keys/2/deactivate?force=true")
    assert r.status_code == 200 and r.json()["details"]["force"] is True
    assert env.actions() == ["KEY_ACTIVATE", "KEY_DEACTIVATE", "KEY_DEACTIVATE"]
    assert all(a.details["via"] == "legacy_post" for a in env.audits())
    assert env.events.events() == ["dnssec.key_activated", "dnssec.key_deactivated", "dnssec.key_deactivated"]
    assert env.events.calls[0]["data"]["key_id"] == 2 and env.events.calls[0]["data"]["zone"] == Z


def test_legacy_activate_pdns_error_audited(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    env.pdns.fail_on["update_cryptokey"] = pdns_error(500, "boom")
    r = env.client().post(f"{B}/keys/2/activate")
    assert r.status_code == 500
    (err,) = env.detached.entries
    assert err.action == "KEY_ACTIVATE" and err.status == "error" and err.error_message == "PowerDNS (srv1): boom"
    assert err.details["via"] == "legacy_post" and err.zone_name == Z


# =============================================================================================
# Nr. 14 DELETE
# =============================================================================================
def test_delete_key(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    c = env.client()
    r = c.delete(f"{B}/keys/1")
    assert r.status_code == 409 and detail(r)["code"] == "last_active_key"
    r = c.delete(f"{B}/keys/2")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == f"Schlüssel 2 aus Zone '{Z}' gelöscht"
    (audit,) = env.audits()
    assert audit.action == "KEY_DELETE" and audit.details["was_active"] is False and audit.details["overridden"] == []
    assert audit.details["key_tag"] is not None and audit.details["algorithm"] == "ECDSAP256SHA256"
    assert env.events.events() == ["dnssec.key_deleted"]
    assert env.pdns.key_ids(Z) == [1]
    r = c.delete(f"{B}/keys/1?force=true")
    assert r.status_code == 200 and r.json()["details"]["overridden"] == [
        "last_active_key", "last_active_sep", "last_published_sep"]
    r = c.delete(f"{B}/keys/7")
    assert r.status_code == 404


def test_delete_key_pdns_error(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    env.pdns.fail_on["delete_cryptokey"] = pdns_error(422, "kann nicht")
    r = env.client().delete(f"{B}/keys/2")
    assert r.status_code == 422 and detail(r) == "PowerDNS (srv1): kann nicht"
    assert env.detached.actions() == ["KEY_DELETE"] and env.events.calls == []


# =============================================================================================
# Nr. 15 PUT nsec3
# =============================================================================================
def test_nsec3_requires_keys(env):
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec3"})
    assert r.status_code == 409 and "keine DNSSEC-Schlüssel" in detail(r)


def test_nsec3_unchanged_and_change(env):
    env.pdns.add_key(Z)
    env.pdns.meta(Z).update({"nsec3param": "1 0 0 -", "api_rectify": True})
    c = env.client()
    r = c.put(f"{B}/nsec3", json={"nsec_mode": "nsec3"})
    assert r.status_code == 200 and r.json()["details"]["unchanged"] is True
    assert env.pdns.writes() == [] and env.audits() == []
    # api_rectify aus -> nicht "unveraendert"
    env.pdns.meta(Z)["api_rectify"] = False
    r = c.put(f"{B}/nsec3", json={"nsec_mode": "nsec3"})
    assert r.json()["details"]["unchanged"] is False
    env.pdns.meta(Z).update({"nsec3param": "", "api_rectify": True})
    env.pdns.calls.clear()
    env.pdns.ops.clear()
    r = c.put(f"{B}/nsec3", json={"nsec_mode": "nsec3", "nsec3_iterations": 5, "nsec3_optout": True,
                                  "nsec3narrow": True})
    assert r.status_code == 200, r.text
    assert env.pdns.writes() == ["update_zone", "rectify_zone"]
    assert env.pdns.bodies("PUT", f"/zones/{Z}") == [{"nsec3param": "1 1 5 -", "nsec3narrow": True, "api_rectify": True}]
    d = r.json()["details"]
    assert d["warnings"] == ["nsec3_iterations_nonzero"] and d["nsec3param"] == "1 1 5 -"
    audit = env.audits()[-1]
    assert audit.action == "DNSSEC_NSEC3_UPDATE"
    assert audit.details["before"]["nsec3param"] == "" and audit.details["after"]["nsec3param"] == "1 1 5 -"
    assert env.events.events()[-1] == "dnssec.nsec3_changed"
    assert env.events.calls[-1]["data"]["nsec3param"] == "1 1 5 -"
    assert env.events.calls[-1]["data"]["before"]["nsec3param"] is None


def test_nsec3_rectify_error(env):
    env.pdns.add_key(Z)
    env.pdns.fail_on["rectify_zone"] = pdns_error(500, "rectify kaputt")
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec"})
    assert r.status_code == 500
    assert "Rectify fehlgeschlagen" in detail(r) and f"pdnsutil rectify-zone {Z}" in detail(r)
    (err,) = env.detached.entries
    assert err.action == "DNSSEC_NSEC3_UPDATE" and err.details["step"] == "rectify"


def test_nsec3_set_error(env):
    env.pdns.add_key(Z)
    env.pdns.fail_on["update_zone"] = pdns_error(422, "bad")
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec"})
    assert r.status_code == 422 and detail(r) == "PowerDNS (srv1): bad"
    assert env.detached.entries[0].details["step"] == "nsec3"


def test_nsec3_rejected_for_algorithm_without_nsec3(env):
    env.pdns.add_key(Z, algorithm="RSASHA1")
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec3"})
    assert r.status_code == 409 and "RSASHA1 (5), der kein NSEC3 unterstützt" in detail(r)
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec3", "unbekannt": 1})
    assert r.status_code == 422


# =============================================================================================
# Nr. 16 Deaktivieren
# =============================================================================================
def _three_keys(env):
    env.pdns.add_key(Z)                          # 1 aktiv
    env.pdns.add_key(Z, active=False)            # 2 inaktiv
    env.pdns.add_key(Z, "zsk", active=False)     # 3 inaktiv
    env.pdns.meta(Z)["nsec3param"] = "1 0 0 -"


def test_disable_order(env):
    _three_keys(env)
    r = env.client().post(f"{B}/disable")
    assert r.status_code == 200, r.text
    assert env.pdns.writes() == ["update_zone", "delete_cryptokey", "delete_cryptokey", "delete_cryptokey",
                                 "rectify_zone"]
    assert env.pdns.bodies("PUT", f"/zones/{Z}") == [{"nsec3param": "", "nsec3narrow": False, "api_rectify": True}]
    deleted = [c[1].rsplit("/", 1)[1] for c in env.pdns.calls if c[0] == "DELETE"]
    assert deleted == ["2", "3", "1"]
    d = r.json()["details"]
    assert [k["key_id"] for k in d["deleted_keys"]] == [2, 3, 1] and d["rectify_error"] is None
    assert d["warning"] == "Entferne die DS-Records beim Registrar, falls noch nicht geschehen."
    (audit,) = env.audits()
    assert audit.action == "DNSSEC_DISABLE" and audit.details["nsec3param_before"] == "1 0 0 -"
    assert audit.details["parent_ds"] == "not_checked"
    assert env.events.events() == ["dnssec.disabled"]
    assert env.events.calls[0]["data"]["deleted_key_ids"] == [2, 3, 1]


def test_disable_partial(env):
    _three_keys(env)
    env.pdns.fail_on["delete_cryptokey#2"] = pdns_error(500, "weg")
    r = env.client().post(f"{B}/disable", json={})
    assert r.status_code == 500
    assert detail(r).startswith("DNSSEC nur teilweise deaktiviert: Schlüssel 2 gelöscht, Schlüssel 3 fehlgeschlagen")
    (err,) = env.detached.entries
    assert err.action == "DNSSEC_DISABLE" and [k["key_id"] for k in err.details["deleted_keys"]] == [2]
    assert err.details["failed_key_id"] == 3
    assert env.audits() == [] and env.events.calls == []


def test_disable_already_and_rectify_error(env):
    c = env.client()
    r = c.post(f"{B}/disable")
    assert r.status_code == 200 and r.json()["details"]["already_disabled"] is True
    assert "war bereits deaktiviert" in r.json()["message"] and env.pdns.writes() == []
    env.pdns.add_key(Z)
    env.pdns.fail_on["rectify_zone"] = pdns_error(500, "kein rectify")
    r = c.post(f"{B}/disable")
    assert r.status_code == 200
    assert r.json()["details"]["rectify_error"] == "PowerDNS (srv1): kein rectify"


def test_disable_nsec3_error_deletes_nothing(env):
    _three_keys(env)
    env.pdns.fail_on["update_zone"] = pdns_error(422, "nein")
    r = env.client().post(f"{B}/disable")
    assert r.status_code == 422 and env.pdns.key_ids(Z) == [1, 2, 3]
    assert env.detached.entries[0].details["step"] == "nsec3"


# =============================================================================================
# Nr. 17 Status
# =============================================================================================
STATUS_FIELDS = {"zone", "server", "server_version", "zone_kind", "presigned", "manageable", "signed", "dnssec_flag",
                 "api_rectify", "nsec", "keys", "rollover", "key_history", "peers", "hints", "capabilities",
                 "server_writable", "user_can_write", "can_write"}


def test_status_fields(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    env.pdns.meta(Z).update({"nsec3param": "1 0 0 -", "api_rectify": True})
    r = env.client().get(f"{B}/status")
    assert r.status_code == 200, r.text
    s = r.json()
    assert set(s) == STATUS_FIELDS
    assert s["zone"] == Z and s["server"] == "srv1" and s["server_version"] == "4.9.4" and s["zone_kind"] == "Native"
    assert s["signed"] is True and s["dnssec_flag"] is True and s["can_write"] is True and s["peers"] == []
    assert s["nsec"] == {"mode": "nsec3", "nsec3param": "1 0 0 -", "iterations": 0, "salt": "-", "opt_out": False,
                         "narrow": False}
    k1, k2 = s["keys"]
    assert k1["key_tag"] == compute_key_tag(env.pdns.key(Z, 1)["dnskey"]) and k1["role"] == "sep"
    assert k1["ds_status"] == "current" and k2["ds_status"] == "new"
    assert k1["rollover_role"] == "old" and k2["rollover_role"] == "new"
    assert [d["digest_type"] for d in k1["ds"]] == [1, 2, 4]
    assert s["rollover"]["sep"] == {"phase": "new_prepublished", "old_key_id": 1, "new_key_id": 2,
                                    "next_action": "activate_new"}
    caps = s["capabilities"]
    assert caps["supports_published"] is True and caps["max_keys"] == 6 and caps["parent_ds_check"] is False
    assert [a["number"] for a in caps["algorithms"]] == [13, 14, 15, 16, 8, 10]
    assert [h["code"] for h in s["hints"]] == ["rollover_in_progress", "sha1_ds"]
    assert "privatekey" not in r.text


@pytest.mark.parametrize("version,published_field,expected", [
    ("4.2.0", False, False), ("4.9.4", True, True), (None, True, True), (None, False, False),
])
def test_status_supports_published(env, version, published_field, expected):
    env.use(FakePdnsClient("srv1", version=version, published_field=published_field))
    env.pdns.add_key(Z)
    s = env.client().get(f"{B}/status").json()
    assert s["capabilities"]["supports_published"] is expected
    assert s["server_version"] == version


def test_status_version_unknown_without_keys(env):
    env.use(FakePdnsClient("srv1", version=None))
    s = env.client().get(f"{B}/status").json()
    assert s["capabilities"]["supports_published"] is None and s["keys"] == []
    assert [h["code"] for h in s["hints"]] == ["version_unknown"] and s["nsec"]["mode"] is None


def test_status_version_cached(env):
    c = env.client()
    c.get(f"{B}/status")
    c.get(f"{B}/status")
    assert env.pdns.ops.count("get_server_info") == 1


def test_status_peers(env):
    env.pdns.add_key(Z)
    same = env.add_server(FakePdnsClient("srv2"))
    same.add_key(Z)
    other = env.add_server(FakePdnsClient("srv3"))
    other.add_key(Z, key_id=7)
    env.add_server(FakePdnsClient("srv4", zones=()))
    env.add_server(FakePdnsClient("srv5", fail_on={"get_zone_meta": pdns_error(503, "down", "srv5")}))
    env.add_server(FakePdnsClient("srv6", kind="Slave"))
    env.add_server(FakePdnsClient("srv7", fail_on={"get_cryptokeys": pdns_error(400, "kaputt", "srv7")}))
    c = env.client(server_configs=[SimpleNamespace(name="srv3", allow_writes=False, is_active=True)])
    s = c.get(f"{B}/status").json()
    states = {p["server"]: p["state"] for p in s["peers"]}
    assert states == {"srv2": "same_keys", "srv3": "different_keys", "srv4": "zone_missing", "srv5": "unreachable",
                      "srv6": "secondary", "srv7": "error"}
    srv3 = next(p for p in s["peers"] if p["server"] == "srv3")
    assert srv3["allow_writes"] is False and srv3["key_tags"] == [compute_key_tag(other.key(Z, 7)["dnskey"])]
    hints = {h["code"]: h for h in s["hints"]}
    assert hints["peers_divergent"]["params"] == {"servers": "srv3"}
    assert hints["peers_unreachable"]["params"] == {"servers": "srv5, srv7"}
    assert "peers_unsigned" not in hints
    s = c.get(f"{B}/status?peers=false").json()
    assert s["peers"] is None and "peers_divergent" not in {h["code"] for h in s["hints"]}


def test_status_peer_unsigned_hint(env):
    env.pdns.add_key(Z)
    env.add_server(FakePdnsClient("srv2"))
    s = env.client().get(f"{B}/status").json()
    assert s["peers"][0]["state"] == "unsigned"
    assert {h["code"] for h in s["hints"]} >= {"peers_unsigned"}


def test_status_key_history_from_audit(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    rows = [  # neueste zuerst (ORDER BY id DESC)
        (datetime(2026, 10, 5, 12, 0), "KEY_CREATE", {"key_id": 2, "active": False, "published": True}),
        (datetime(2026, 10, 4, 12, 0), "KEY_DELETE", {"key_id": 2}),
        (datetime(2026, 10, 3, 12, 0), "KEY_CREATE", {"key_id": 2, "active": True}),
        (datetime(2026, 10, 2, 12, 0), "DNSSEC_ENABLE", {"keys": [{"key_id": 1}]}),
        (datetime(2026, 9, 1, 12, 0), "DNSSEC_DISABLE", {}),
        (datetime(2026, 8, 1, 12, 0), "KEY_DEACTIVATE", {"key_id": 1}),
    ]
    s = env.client(audit_rows=rows).get(f"{B}/status").json()
    assert s["key_history"]["1"] == {"created_at": "2026-10-02T12:00:00+00:00",
                                     "activated_at": "2026-10-02T12:00:00+00:00",
                                     "deactivated_at": None, "published_at": None, "unpublished_at": None}
    assert s["key_history"]["2"] == {"created_at": "2026-10-05T12:00:00+00:00", "activated_at": None,
                                     "deactivated_at": None, "published_at": "2026-10-05T12:00:00+00:00",
                                     "unpublished_at": None}
    assert env.client(audit_rows=rows).get(f"{B}/status?history=false").json()["key_history"] == {}


# =============================================================================================
# Nr. 18 Routing
# =============================================================================================
def test_routing_with_tricky_zone_names(env):
    env.use(FakePdnsClient("srv1", zones=(Z, "keys.example.com.", "status.example.com.", "nsec3.example.com.")))
    c = env.client()
    r = c.put(f"{A}/srv1/keys.example.com./keys/5", json={"active": True})
    assert r.status_code == 404 and detail(r) == "Schlüssel 5 existiert in der Zone 'keys.example.com.' auf 'srv1' nicht."
    r = c.get(f"{A}/srv1/status.example.com./keys")
    assert r.status_code == 200 and r.json()["zone"] == "status.example.com." and r.json()["key_count"] == 0
    assert c.get(f"{A}/srv1/{Z}/keys/abc").status_code == 422
    r = c.put(f"{A}/srv1/nsec3.example.com./nsec3", json={"nsec_mode": "nsec"})
    assert r.status_code == 409 and "'nsec3.example.com.'" in detail(r)
    r = c.get(f"{A}/srv1/status.example.com./status")
    assert r.status_code == 200 and r.json()["zone"] == "status.example.com."
    env.pdns.add_key("keys.example.com.")
    r = c.get(f"{A}/srv1/keys.example.com./keys/1")
    assert r.status_code == 200 and r.json()["zone"] == "keys.example.com." and r.json()["key"]["id"] == 1


def test_route_order_matches_spec():
    paths = [(sorted(r.methods)[0], r.path) for r in dnssec_router.router.routes]
    P = "/dnssec/{server_name}/{zone_id:path}"
    assert paths == [
        ("GET", f"{P}/status"), ("GET", f"{P}/parent-ds"), ("GET", f"{P}/dnskey-check"),
        ("GET", f"{P}/ds"), ("GET", f"{P}/keys"), ("POST", f"{P}/keys"),
        ("POST", f"{P}/enable"), ("POST", f"{P}/disable"), ("PUT", f"{P}/nsec3"),
        ("POST", f"{P}/keys/{{key_id}}/activate"), ("POST", f"{P}/keys/{{key_id}}/deactivate"),
        ("GET", f"{P}/keys/{{key_id}}"), ("PUT", f"{P}/keys/{{key_id}}"), ("DELETE", f"{P}/keys/{{key_id}}"),
    ]


# =============================================================================================
# Nr. 19 nie privatekey
# =============================================================================================
def test_no_endpoint_returns_privatekey(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    c = env.client()
    responses = [c.post(f"{B}/enable", json={"key_model": "ksk_zsk"})]
    responses += [c.get(f"{B}/{p}") for p in ("status", "keys", "ds", "keys/1", "keys/2")]
    responses.append(c.post(f"{B}/keys", json={"keytype": "ksk", "active": False}))
    responses.append(c.put(f"{B}/keys/3", json={"active": True}))
    responses.append(c.post(f"{B}/keys/1/deactivate"))
    responses.append(c.delete(f"{B}/keys/1"))
    responses.append(c.put(f"{B}/nsec3", json={"nsec_mode": "nsec"}))
    responses.append(c.post(f"{B}/disable"))
    assert [r.status_code for r in responses] == [200] * len(responses), [r.text for r in responses]
    for r in responses:
        assert "privatekey" not in r.text and "GEHEIM" not in r.text
    dumped = json.dumps([e["data"] for e in env.events.calls]) + json.dumps([a.details for a in env.audits()])
    assert "GEHEIM" not in dumped and "privatekey" not in dumped


# =============================================================================================
# Serial-Erhoehung + NOTIFY nach Schluesselaenderungen [D10]
# =============================================================================================
def test_master_zone_key_change_bumps_serial_and_notifies(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    before = env.pdns.serial(Z)
    r = env.client().put(f"{B}/keys/2", json={"active": True})
    assert r.status_code == 200, r.text
    d = r.json()["details"]
    assert d["serial_bumped"] is True and d["serial"] == before + 1
    assert d["notified"] is True and d["notify_error"] is None and d["serial_error"] is None
    assert env.pdns.serial(Z) == before + 1
    assert env.pdns.writes() == ["update_cryptokey", "update_records", "notify_zone"]
    (patch,) = env.pdns.patches
    assert patch[0]["type"] == "SOA" and patch[0]["changetype"] == "REPLACE" and patch[0]["name"] == Z
    assert env.actions() == ["KEY_ACTIVATE", "UPDATE", "ZONE_NOTIFY"]
    _, soa_audit, notify_audit = env.audits()
    assert soa_audit.resource_type == "record" and soa_audit.zone_name == Z and soa_audit.server_name == "srv1"
    sd = soa_audit.details
    assert sd["version"] == 2 and sd["source"] == "dnssec" and sd["trigger"] == "KEY_ACTIVATE"
    change = sd["changes"][0]
    assert change["type"] == "SOA" and f" {before} " in change["before"]["records"][0]["content"]
    assert f" {before + 1} " in change["after"]["records"][0]["content"] and sd["after_source"] == "reread"
    assert sd["primary_outcome"] == "ok" and sd["fanout"] == {"srv1": "saved"}
    assert notify_audit.resource_type == "zone" and notify_audit.details["source"] == "dnssec"
    assert notify_audit.details["server"] == "srv1"
    assert env.events.calls[0]["data"]["serial_bumped"] is True


def test_native_zone_no_serial_bump(env):
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    before = env.pdns.serial(Z)
    r = env.client().put(f"{B}/keys/2", json={"active": True, "bump_serial": True})
    assert r.status_code == 200
    assert r.json()["details"]["serial_bumped"] is False and r.json()["details"]["notified"] is False
    assert env.pdns.serial(Z) == before and "update_records" not in env.pdns.ops and "notify_zone" not in env.pdns.ops
    assert env.actions() == ["KEY_ACTIVATE"]


def test_master_zone_bump_serial_false(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    env.pdns.add_key(Z)
    env.pdns.add_key(Z, active=False)
    r = env.client().delete(f"{B}/keys/2?bump_serial=false")
    assert r.status_code == 200 and r.json()["details"]["serial_bumped"] is False
    assert env.pdns.writes() == ["delete_cryptokey"]


def test_master_zone_notify_error_does_not_fail(env):
    env.use(FakePdnsClient("srv1", kind="Producer", fail_on={"notify_zone": pdns_error(422, "no secondaries")}))
    env.pdns.add_key(Z)
    r = env.client().put(f"{B}/nsec3", json={"nsec_mode": "nsec"})
    assert r.status_code == 200, r.text
    d = r.json()["details"]
    assert d["serial_bumped"] is True and d["notified"] is False
    assert d["notify_error"] == "PowerDNS (srv1): no secondaries"
    assert env.actions() == ["DNSSEC_NSEC3_UPDATE", "UPDATE"]
    (err,) = env.detached.entries
    assert err.action == "ZONE_NOTIFY" and err.status == "error" and err.details["status_code"] == 422


def test_master_zone_serial_error_skips_notify(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    env.pdns.add_key(Z)
    env.pdns.fail_on_patch = pdns_error(422, "SOA kaputt")
    r = env.client().post(f"{B}/disable", json={"bump_serial": None})
    assert r.status_code == 200, r.text
    d = r.json()["details"]
    assert d["serial_bumped"] is False and d["serial_error"] == "PowerDNS (srv1): SOA kaputt" and d["notified"] is False
    assert "notify_zone" not in env.pdns.ops
    assert env.detached.actions() == ["UPDATE"] and env.actions() == ["DNSSEC_DISABLE"]


def test_master_zone_enable_and_create_bump(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    before = env.pdns.serial(Z)
    c = env.client()
    assert c.post(f"{B}/enable", json={}).json()["details"]["serial_bumped"] is True
    assert c.post(f"{B}/keys", json={"keytype": "zsk"}).json()["details"]["serial"] == before + 2
    assert c.post(f"{B}/enable", json={}).json()["details"]["serial_bumped"] is False  # unveraendert


# =============================================================================================
# Service: DNSSEC beim Zonenanlegen (fuer F4-B)
# =============================================================================================
async def test_enable_on_new_zone_success(env):
    session = FakeSession(user_row=env.user)
    opts = DNSSECEnable(algorithm="RSASHA256")
    err = await svc.enable_dnssec_on_new_zone(session, env.pdns, "srv1", Z, opts, env.user)
    assert err is None
    assert env.pdns.bodies("POST", "/cryptokeys") == [
        {"keytype": "csk", "active": True, "algorithm": "RSASHA256", "bits": 2048}]
    (audit,) = [o for o in session.added if isinstance(o, AuditLog)]
    assert audit.action == "DNSSEC_ENABLE" and audit.details["source"] == "zone_create"
    assert audit.zone_name == Z and audit.details["keys"][0]["key_id"] == 1
    assert env.events.events() == ["dnssec.enabled"] and env.events.calls[0]["audit_log_id"] == audit.id
    assert "get_server_info" not in env.pdns.ops  # kein Versions-Abruf beim Zonenanlegen


async def test_enable_on_new_zone_errors(env):
    session = FakeSession(user_row=env.user)
    env.pdns.fail_on["add_cryptokey"] = pdns_error(422, "kein Algorithmus")
    err = await svc.enable_dnssec_on_new_zone(session, env.pdns, "srv1", Z, DNSSECEnable(), env.user)
    assert err == "Schlüssel anlegen (CSK): PowerDNS (srv1): kein Algorithmus"
    (entry,) = env.detached.entries
    assert entry.action == "DNSSEC_ENABLE" and entry.details["source"] == "zone_create"
    assert entry.details["rollback"] == "ok" and env.events.calls == []
    env.pdns.fail_on.clear()
    env.pdns.add_key(Z, active=False)
    err = await svc.enable_dnssec_on_new_zone(session, env.pdns, "srv1", Z, DNSSECEnable(), env.user)
    assert "aber keiner ist aktiv" in err
    env.pdns.fail_on["get_zone_meta"] = pdns_error(404, "Could not find domain")
    err = await svc.enable_dnssec_on_new_zone(session, env.pdns, "srv1", Z, DNSSECEnable(), env.user)
    assert err == "PowerDNS (srv1): Could not find domain"


async def test_after_key_change_reads_kind_when_missing(env):
    env.use(FakePdnsClient("srv1", kind="Master"))
    session = FakeSession(user_row=env.user)
    out = await svc.after_key_change(session, env.pdns, Z, bump_serial=None, user=env.user, trigger="TEST")
    assert out["serial_bumped"] is True and out["notified"] is True
    out = await svc.after_key_change(session, env.pdns, Z, bump_serial=False, user=env.user)
    assert out == svc.no_follow_up()


def test_schema_reexports_and_zone_create_options():
    from app.schemas import dns as dns_schemas
    from app.schemas import dnssec as dnssec_schemas

    assert dns_schemas.DNSSECEnable is dnssec_schemas.DNSSECEnable
    assert dns_schemas.CryptoKeyResponse is dnssec_schemas.CryptoKeyResponse
    z = dns_schemas.ZoneCreate(name="example.com", enable_dnssec=True, dnssec_options={"algorithm": "ED25519"})
    assert isinstance(z.dnssec_options, dnssec_schemas.DNSSECEnable)
    assert z.dnssec_options.algorithm == "ED25519" and z.dnssec_options.effective_nsec3param() == "1 0 0 -"
    with pytest.raises(Exception):
        dns_schemas.ZoneCreate(name="example.com", dnssec_options={"algorithm": "RSASHA1"})
    legacy = dnssec_schemas.DNSSECEnable(nsec3param="")
    assert legacy.nsec_mode == "nsec" and legacy.effective_nsec3param() == ""
    assert dnssec_schemas.DNSSECEnable(foo=1).key_model == "csk"  # unbekannte Felder ignoriert
