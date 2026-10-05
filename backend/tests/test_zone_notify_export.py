"""F2: NOTIFY und Zonen-Export (Spec F2/F3 9.1 Nr. 22–26) ohne Datenbank.

Handler direkt mit gemocktem PowerDNS-Client, ``assert_zone_access`` und ``_log_action``; zusaetzlich ein
HTTP-Durchlauf mit ``authfakes`` (Leserecht darf exportieren, aber kein NOTIFY senden; F3 7 "Panel-Tokens/F14").
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from authfakes import FakePDNS, FakeSession, build_app, make_user
from app.core.auth import create_access_token
from app.routers import zones
from app.services import audit as audit_service
from app.services.pdns_client import PowerDNSAPIError, pdns_manager


class _Client:
    def __init__(self, *, notify_exc=None, export=None, export_exc=None):
        self.notify_exc = notify_exc
        self.export = export
        self.export_exc = export_exc
        self.notified = []

    async def notify_zone(self, zone_id, **kw):
        self.notified.append(zone_id)
        if self.notify_exc:
            raise self.notify_exc
        return {"result": "Notification queued"}

    async def get_zone_axfr(self, zone_id):
        if self.export_exc:
            raise self.export_exc
        return self.export


@pytest.fixture
def env(monkeypatch):
    state = {"client": _Client(), "access": [], "audit": []}

    def get_client(name):
        if name != "srv1":
            raise ValueError(f"Server '{name}' nicht gefunden")
        return state["client"]

    async def access(db, user, zone_id, *, write=False):
        state["access"].append((zone_id, write))

    async def log_action(db, action, resource_name, server_name=None, details=None, status="success",
                         error_message=None, user_id=None):
        state["audit"].append({"action": action, "resource_name": resource_name, "server_name": server_name,
                               "details": details, "status": status, "error_message": error_message,
                               "user_id": user_id})

    monkeypatch.setattr(zones.pdns_manager, "get_client", get_client)
    monkeypatch.setattr(zones, "assert_zone_access", access)
    monkeypatch.setattr(zones, "_log_action", log_action)
    return state


USER = make_user(role="user", uid=4, username="ops")


# --- Nr. 22 --------------------------------------------------------------------------------------------------
async def test_notify_success_audit(env):
    res = await zones.notify_zone("srv1", "Example.COM", None, USER)
    assert env["access"] == [("Example.COM", True)]
    assert env["client"].notified == ["Example.COM"]
    assert res.message == "NOTIFY für Zone 'example.com.' auf 'srv1' ausgelöst"
    assert res.details == {"server": "srv1"}
    (call,) = env["audit"]
    assert call["action"] == "ZONE_NOTIFY" and call["resource_name"] == "example.com."
    assert call["details"] == {"server": "srv1"} and call["status"] == "success" and call["user_id"] == 4


async def test_notify_unknown_server_404_without_audit(env):
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("nope", "example.com.", None, USER)
    assert e.value.status_code == 404 and env["audit"] == []


# --- Nr. 23 --------------------------------------------------------------------------------------------------
async def test_notify_422_message_and_error_audit(env):
    env["client"] = _Client(notify_exc=PowerDNSAPIError(422, '{"error": "Domain is not primary"}', "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("srv1", "example.com", None, USER)
    assert e.value.status_code == 422
    assert e.value.detail.startswith("NOTIFY fehlgeschlagen: Domain is not primary. NOTIFY funktioniert nur")
    assert "primary=yes" in e.value.detail
    (call,) = env["audit"]
    assert call["status"] == "error" and call["error_message"] == "Domain is not primary"
    assert call["details"] == {"server": "srv1", "status_code": 422}
    # 400 wird ebenfalls zu 422 mit Erklaerung
    env["client"] = _Client(notify_exc=PowerDNSAPIError(400, "Zone is Native", "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("srv1", "example.com", None, USER)
    assert e.value.status_code == 422 and "Zone is Native" in e.value.detail


# --- Nr. 24 --------------------------------------------------------------------------------------------------
async def test_notify_5xx_masked(env):
    env["client"] = _Client(notify_exc=PowerDNSAPIError(502, "upstream connect error 10.0.0.5:8081", "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("srv1", "example.com", None, USER)
    assert e.value.status_code == 502
    assert e.value.detail == zones._PDNS_UNAVAILABLE and "10.0.0.5" not in e.value.detail
    assert env["audit"][0]["status"] == "error"


async def test_notify_404_and_other_status(env):
    env["client"] = _Client(notify_exc=PowerDNSAPIError(404, '{"error": "Could not find domain"}', "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("srv1", "Example.com", None, USER)
    assert e.value.status_code == 404 and e.value.detail == "Zone 'example.com.' existiert auf 'srv1' nicht"
    env["client"] = _Client(notify_exc=PowerDNSAPIError(409, '{"error": "busy"}', "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.notify_zone("srv1", "example.com", None, USER)
    assert e.value.status_code == 409 and e.value.detail == "busy"


# --- Nr. 25 --------------------------------------------------------------------------------------------------
async def test_export_returns_filename_and_audits(env):
    content = "example.com.\t3600\tIN\tSOA\tns1. hostmaster. 1 2 3 4 5\nwww.example.com.\t300\tIN\tA\t192.0.2.1\n"
    env["client"] = _Client(export=content)
    res = await zones.export_zone("srv1", "Example.com.", None, USER)
    assert env["access"] == [("Example.com.", False)]
    assert res == {"zone": "Example.com.", "server": "srv1", "format": "bind", "content": content,
                   "filename": "example.com.txt"}
    (call,) = env["audit"]
    assert call["action"] == "ZONE_EXPORT" and call["resource_name"] == "example.com."
    assert call["details"] == {"server": "srv1", "bytes": len(content.encode()), "lines": 2}

    env["client"] = _Client(export={"zone": "a.example.\t60\tIN\tA\t192.0.2.9\n"})
    res = await zones.export_zone("srv1", "a.example.", None, USER)
    assert res["content"] == "a.example.\t60\tIN\tA\t192.0.2.9\n" and res["filename"] == "a.example.txt"

    env["client"] = _Client(export=None)
    res = await zones.export_zone("srv1", "a.example.", None, USER)
    assert res["content"] == ""


async def test_export_errors_are_not_audited_and_5xx_masked(env):
    env["client"] = _Client(export_exc=PowerDNSAPIError(503, "backend down at 10.0.0.9", "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.export_zone("srv1", "a.example.", None, USER)
    assert e.value.status_code == 503 and e.value.detail == zones._PDNS_UNAVAILABLE
    env["client"] = _Client(export_exc=PowerDNSAPIError(404, "Could not find domain", "ns1"))
    with pytest.raises(HTTPException) as e:
        await zones.export_zone("srv1", "a.example.", None, USER)
    assert e.value.status_code == 404
    assert env["audit"] == []


def test_export_filename_sanitized():
    assert zones._export_filename("Example.COM.") == "example.com.txt"
    assert zones._export_filename("0/24.2.0.192.in-addr.arpa.") == "0_24.2.0.192.in-addr.arpa.txt"
    assert zones._export_filename("xn--bcher-kva.example.") == "xn--bcher-kva.example.txt"
    assert zones._export_filename("") == "zone.txt"


# --- Nr. 26 --------------------------------------------------------------------------------------------------
def test_pdns_message_property():
    assert PowerDNSAPIError(422, '{"error": "Domain is not primary"}', "ns1").pdns_message == "Domain is not primary"
    assert PowerDNSAPIError(500, "  plain text  ", "ns1").pdns_message == "plain text"
    assert len(PowerDNSAPIError(500, "x" * 2000, "ns1").pdns_message) == 500
    assert len(PowerDNSAPIError(422, '{"error": "' + "y" * 900 + '"}', "ns1").pdns_message) == 500
    assert PowerDNSAPIError(422, '{"other": 1}', "ns1").pdns_message == '{"other": 1}'


# --- HTTP: Leserecht exportiert, NOTIFY braucht Schreibrecht ---------------------------------------------------
@pytest.fixture
def http(monkeypatch):
    fake = FakePDNS()
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})

    async def detached(*a, **k):
        return None

    monkeypatch.setattr(audit_service, "write_audit_detached", detached)
    user = make_user(role="user", uid=4, username="ops")
    session = FakeSession(user_row=user, zone_access=[("read.example.", "read"), ("rw.example.", "manage")])
    client = TestClient(build_app(session, zones), raise_server_exceptions=False)
    jwt = create_access_token(data={"sub": "4"}, user=user)
    return client, {"Authorization": f"Bearer {jwt}"}, fake, session


def test_read_permission_allows_export_but_not_notify(http):
    client, headers, fake, session = http
    r = client.get("/api/v1/zones/srv1/read.example./export", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "read.example.txt"
    r = client.post("/api/v1/zones/srv1/read.example./notify", headers=headers, json={})
    assert r.status_code == 403 and r.json()["detail"] == "Nur Lese-Zugriff auf diese Zone"
    assert fake.calls.get("notify_zone") is None
    r = client.post("/api/v1/zones/srv1/rw.example./notify", headers=headers, json={})
    assert r.status_code == 200 and fake.calls.get("notify_zone") == 1
    r = client.post("/api/v1/zones/srv1/other.example./notify", headers=headers, json={})
    assert r.status_code == 403 and "other.example" not in r.json()["detail"]
    # Audits landen in der Request-Session (Erfolg), Commit vor der Antwort (DbWrite)
    actions = [getattr(o, "action", None) for o in session.added]
    assert "ZONE_EXPORT" in actions and "ZONE_NOTIFY" in actions
