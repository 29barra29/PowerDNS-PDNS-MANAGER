"""PTR-Pflege an den Record-Endpunkten (F11 9 test_records_ptr, Bauplan B.6a [D7]).

``wave_integration``: die Einhaengepunkte in ``routers/records.py`` (create/update/delete/bulk) baut WS-F1 in
Welle 2 (``ptr.sync_for_changes`` mit den Audit-v2-``changes``, Flag ``manage_ptr`` ueber ``resolve_manage_ptr``,
Antwort ``details.ptr``). Die Tests pruefen nur beobachtbares Verhalten: PTR-Zustand in der Reverse-Zone der
In-Memory-PowerDNS-Server und ``details.ptr`` in der Antwort – unabhaengig von F1-Interna.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import auth as core_auth  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import User  # noqa: E402
from app.services import fanout, ptr, system_settings, webhook_outbox, zone_index  # noqa: E402
from app.services.pdns_client import PowerDNSAPIError  # noqa: E402
from fakes.pdns import FakeDB, fake_pdns, make_zone, rr  # noqa: E402,F401

pytestmark = pytest.mark.wave_integration

Z = "example.com."
REV = "2.0.192.in-addr.arpa."
WWW = "www.example.com."
BASE = f"/api/v1/records/ns1/{Z}"


def p(octet: int) -> str:
    return f"{octet}.{REV}"


class Spy:
    def __init__(self, orig):
        self.orig = orig
        self.calls = []

    async def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return await self.orig(*a, **kw)


@pytest.fixture
def env(monkeypatch, fake_pdns):
    zone_index.invalidate()
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)
    for srv in (fake_pdns.ns1, fake_pdns.ns2):
        srv.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", ttl=300), rr("mail.example.com.", "MX", "10 mx.example.com.")]))
        srv.add_zone(make_zone(REV, [rr(p(1), "PTR", WWW, ttl=7200)]))
    settings = {"ptr_auto_default": False}

    async def gbs(db, key, default):
        return settings.get(key, default)

    monkeypatch.setattr(ptr, "get_bool_setting", gbs)
    # records/bulk (WS-F1) loesen den Admin-Default selbst ueber system_settings.get_bool_setting auf
    monkeypatch.setattr(system_settings, "get_bool_setting", gbs)

    async def events(*a, **kw):
        return 1

    monkeypatch.setattr(webhook_outbox, "enqueue_event", events)
    sync_changes = Spy(ptr.sync_for_changes)
    sync_ptrs = Spy(ptr.sync_ptrs)
    monkeypatch.setattr(ptr, "sync_for_changes", sync_changes)
    monkeypatch.setattr(ptr, "sync_ptrs", sync_ptrs)
    admin = User(id=1, username="root", role="admin", is_active=True, hashed_password="x")

    async def current(request: Request):
        core_auth.set_auth_context(request, "session", None, username=admin.username)
        return admin

    db = FakeDB()

    async def _db():
        yield db

    app.dependency_overrides[core_auth.get_current_user] = current
    app.dependency_overrides[get_db] = _db
    client = TestClient(app, raise_server_exceptions=False)
    yield SimpleNamespace(client=client, pdns=fake_pdns, settings=settings, sync_changes=sync_changes,
                          sync_ptrs=sync_ptrs)
    app.dependency_overrides.pop(core_auth.get_current_user, None)
    app.dependency_overrides.pop(get_db, None)
    zone_index.invalidate()


def _ptr_values(env, octet, srv="ns1"):
    return getattr(env.pdns, srv).values(REV, p(octet), "PTR")


def _called(env) -> bool:
    return bool(env.sync_changes.calls or env.sync_ptrs.calls)


# ---------------------------------------------------------------------------------------------------------------------
# Einzel-Endpunkte
# ---------------------------------------------------------------------------------------------------------------------
def test_create_with_manage_ptr_sets_ptr(env):
    r = env.client.post(BASE, json={"name": "host.example.com.", "type": "A", "ttl": 600,
                                    "records": [{"content": "192.0.2.10"}], "manage_ptr": True})
    assert r.status_code == 200, r.text
    details = r.json()["details"]
    assert [(x["ip"], x["action"]) for x in details["ptr"]] == [("192.0.2.10", "set")]
    assert details["ns1"] == "saved"
    assert _ptr_values(env, 10) == ["host.example.com."] and _ptr_values(env, 10, "ns2") == ["host.example.com."]
    assert env.pdns.ns1.rrset(REV, p(10), "PTR")["ttl"] == 600


def test_create_without_flag_uses_admin_default(env):
    r = env.client.post(BASE, json={"name": "a.example.com.", "type": "A", "records": [{"content": "192.0.2.11"}]})
    assert r.status_code == 200 and "ptr" not in r.json()["details"]
    assert _ptr_values(env, 11) == [] and not env.sync_ptrs.calls
    env.settings["ptr_auto_default"] = True
    r = env.client.post(BASE, json={"name": "b.example.com.", "type": "A", "records": [{"content": "192.0.2.12"}]})
    assert r.status_code == 200 and r.json()["details"]["ptr"][0]["action"] == "set"
    assert _ptr_values(env, 12) == ["b.example.com."]


def test_mx_with_manage_ptr_does_nothing(env):
    r = env.client.post(BASE, json={"name": "mail.example.com.", "type": "MX", "records": [{"content": "20 mx2.example.com."}],
                                    "manage_ptr": True})
    assert r.status_code == 200
    assert not r.json()["details"].get("ptr")
    assert not env.sync_ptrs.calls  # keine PTR-Op fuer Nicht-A/AAAA
    assert env.pdns.ns1.patches[-1][0]["type"] == "MX"


def test_update_moves_ptr(env):
    r = env.client.put(BASE, json={"name": WWW, "type": "A", "ttl": 300, "old_content": "192.0.2.1",
                                   "new_content": "192.0.2.2", "manage_ptr": True})
    assert r.status_code == 200, r.text
    actions = sorted((x["ip"], x["action"]) for x in r.json()["details"]["ptr"])
    assert actions == [("192.0.2.1", "removed"), ("192.0.2.2", "set")]
    assert _ptr_values(env, 1) == [] and _ptr_values(env, 2) == [WWW]


def test_delete_rrset_without_content_removes_all_before_values(env):
    for srv in (env.pdns.ns1, env.pdns.ns2):
        srv.add_zone(make_zone(Z, [rr(WWW, "A", "192.0.2.1", "192.0.2.3")]))
        srv.add_zone(make_zone(REV, [rr(p(1), "PTR", WWW), rr(p(3), "PTR", WWW)]))
    r = env.client.request("DELETE", f"{BASE}/delete", json={"name": WWW, "type": "A", "manage_ptr": True})
    assert r.status_code == 200, r.text
    assert sorted(x["ip"] for x in r.json()["details"]["ptr"] if x["action"] == "removed") == ["192.0.2.1", "192.0.2.3"]
    assert _ptr_values(env, 1) == [] and _ptr_values(env, 3) == []


def test_delete_single_value_keeps_foreign_ptr(env):
    env.pdns.ns1.add_zone(make_zone(REV, [rr(p(1), "PTR", "other.example.net.")]))
    r = env.client.request("DELETE", f"{BASE}/delete", json={"name": WWW, "type": "A", "content": "192.0.2.1",
                                                             "manage_ptr": True})
    assert r.status_code == 200
    (res,) = r.json()["details"]["ptr"]
    assert res["action"] == "skipped" and res["reason"] == "other_target"
    assert _ptr_values(env, 1) == ["other.example.net."]


def test_primary_failure_runs_no_ptr(env):
    env.pdns.ns1.fail_on_patch = PowerDNSAPIError(422, '{"error": "abgelehnt"}', "ns1")
    r = env.client.post(BASE, json={"name": "host.example.com.", "type": "A", "records": [{"content": "192.0.2.10"}],
                                    "manage_ptr": True})
    assert r.status_code >= 400
    assert not _called(env) and _ptr_values(env, 10) == []


# ---------------------------------------------------------------------------------------------------------------------
# Bulk (F1): merge / set_disabled / set_ttl [D7]
# ---------------------------------------------------------------------------------------------------------------------
def test_bulk_merge_creates_ptr(env):
    r = env.client.post(f"{BASE}/bulk", json={"merge": [{"name": WWW, "type": "A", "records": [{"content": "192.0.2.5"}]}],
                                              "manage_ptr": True})
    assert r.status_code == 200, r.text
    ptrs = r.json()["details"]["ptr"]
    assert [(x["ip"], x["action"]) for x in ptrs] == [("192.0.2.5", "set")]
    assert _ptr_values(env, 5) == [WWW] and _ptr_values(env, 1) == [WWW]


def test_bulk_set_disabled_removes_ptr(env):
    r = env.client.post(f"{BASE}/bulk", json={"set_disabled": [{"name": WWW, "type": "A", "content": "192.0.2.1",
                                                                "disabled": True}], "manage_ptr": True})
    assert r.status_code == 200, r.text
    assert [(x["ip"], x["action"]) for x in r.json()["details"]["ptr"]] == [("192.0.2.1", "removed")]
    assert _ptr_values(env, 1) == []


def test_bulk_set_ttl_changes_no_ptr(env):
    r = env.client.post(f"{BASE}/bulk", json={"set_ttl": [{"name": WWW, "type": "A", "ttl": 900}], "manage_ptr": True})
    assert r.status_code == 200, r.text
    assert not r.json()["details"].get("ptr")
    assert _ptr_values(env, 1) == [WWW] and env.pdns.ns1.rrset(REV, p(1), "PTR")["ttl"] == 7200
    assert not env.sync_ptrs.calls


def test_bulk_entry_level_flag_is_ignored(env):
    r = env.client.post(f"{BASE}/bulk", json={"create": [{"name": "n.example.com.", "type": "A",
                                                          "records": [{"content": "192.0.2.20"}], "manage_ptr": True}]})
    assert r.status_code == 200, r.text
    assert "ptr" not in r.json()["details"] and _ptr_values(env, 20) == []


def test_ptr_sync_source_names_action_and_zone(env):
    """L-5 (WS-W3-NACHARBEIT): PTR_SYNC-Audit/-Webhook nennen Schreibart und Forward-Zone (vorher immer BULK_UPDATE
    ohne Zone), fuer alle vier Einhaengepunkte."""
    r = env.client.post(BASE, json={"name": "host.example.com.", "type": "A", "records": [{"content": "192.0.2.10"}],
                                    "manage_ptr": True})
    assert r.status_code == 200, r.text
    r = env.client.put(BASE, json={"name": WWW, "type": "A", "ttl": 300, "old_content": "192.0.2.1",
                                   "new_content": "192.0.2.2", "manage_ptr": True})
    assert r.status_code == 200, r.text
    r = env.client.request("DELETE", f"{BASE}/delete", json={"name": "host.example.com.", "type": "A",
                                                             "manage_ptr": True})
    assert r.status_code == 200, r.text
    r = env.client.post(f"{BASE}/bulk", json={"merge": [{"name": WWW, "type": "A", "records": [{"content": "192.0.2.5"}]}],
                                              "manage_ptr": True})
    assert r.status_code == 200, r.text
    sources = [kw["source"] for _a, kw in env.sync_ptrs.calls]
    assert [(s["action"], s["zone"]) for s in sources] == [
        ("CREATE", Z), ("UPDATE", Z), ("DELETE", Z), ("BULK_UPDATE", Z)]
    assert sources[0]["name"] == "host.example.com." and sources[0]["server"] == "ns1"
