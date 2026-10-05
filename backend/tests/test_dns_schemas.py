"""Schema-Aenderungen in ``schemas/dns.py`` (Welle 0b: F15 5.2, F11 5.13, F4 5.6-Platzhalter, F12) und das
LUA-Policy-Gate in ``routers/records.py`` (ohne DB).
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from authfakes import FakePDNS, FakeSession, bearer, build_app, make_token, make_user
from app.core.auth import create_access_token
from app.models.models import SystemSetting
from app.routers import records
from app.schemas.dns import (
    ALLOWED_RECORD_TYPES,
    GENERIC_TYPE_ERROR,
    BulkRecordUpdate,
    RecordCreate,
    RecordDelete,
    RecordUpdate,
    ServerInfo,
    ZoneCreate,
    ZoneResponse,
)
from app.services import audit as audit_service
from app.services import webhook_outbox
from app.services.lua_records import MSG_DENIED_ADMIN, MSG_DENIED_DISABLED
from app.services.pdns_client import pdns_manager

LUA_OK = "A \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\""


def test_allowed_types_include_lua_and_keep_2_4_1_set():
    assert "LUA" in ALLOWED_RECORD_TYPES and len(ALLOWED_RECORD_TYPES) == 27
    assert RecordCreate(name="a.example.", type="lua", records=[{"content": LUA_OK}]).type == "LUA"
    with pytest.raises(ValidationError, match="Unknown record type: FOO"):
        RecordCreate(name="a.example.", type="foo", records=[{"content": "x"}])


@pytest.mark.parametrize("model,extra", [
    (RecordCreate, {"records": [{"content": "x"}]}),
    (RecordUpdate, {"old_content": "x", "new_content": "y"}),
    (RecordDelete, {}),
])
def test_generic_type_rejected(model, extra):
    with pytest.raises(ValidationError) as exc:
        model(name="a.example.", type="type65402", **extra)
    assert GENERIC_TYPE_ERROR in str(exc.value)


def test_lua_content_validated_and_normalized():
    rec = RecordCreate(name="a.example.", type="LUA", records=[{"content": "  " + LUA_OK + "  "}])
    assert rec.records[0].content == LUA_OK
    with pytest.raises(ValidationError, match="doppelten Anführungszeichen"):
        RecordCreate(name="a.example.", type="LUA", records=[{"content": "A ifportup(443)"}])
    upd = RecordUpdate(name="a.example.", type="LUA", old_content=" alt ", new_content=LUA_OK + " ")
    assert upd.new_content == LUA_OK and upd.old_content == " alt "  # alter Inhalt exakt wie in PowerDNS
    with pytest.raises(ValidationError):
        RecordUpdate(name="a.example.", type="LUA", old_content="x", new_content="A \"a\tb\"")
    # andere Typen bleiben unberuehrt
    assert RecordCreate(name="a.example.", type="TXT", records=[{"content": "\"hallo\""}]).records[0].content == "\"hallo\""


def test_manage_ptr_optional_everywhere():
    assert RecordCreate(name="a.", type="A", records=[{"content": "192.0.2.1"}]).manage_ptr is None
    assert RecordCreate(name="a.", type="A", records=[{"content": "192.0.2.1"}], manage_ptr=True).manage_ptr is True
    assert RecordUpdate(name="a.", type="A", old_content="1", new_content="2", manage_ptr=False).manage_ptr is False
    assert RecordDelete(name="a.", type="A", manage_ptr=True).manage_ptr is True
    assert BulkRecordUpdate(manage_ptr=True).manage_ptr is True and BulkRecordUpdate().manage_ptr is None


def test_zone_create_dnssec_options_typed_and_edited_serial():
    from app.schemas.dnssec import DNSSECEnable

    z = ZoneCreate(name="example.com", enable_dnssec=True, dnssec_options={"algorithm": "ED25519"})
    assert isinstance(z.dnssec_options, DNSSECEnable) and z.dnssec_options.algorithm == "ED25519"
    assert ZoneCreate(name="example.com").dnssec_options is None
    zr = ZoneResponse(id="a.", name="a.", kind="Native", serial=1, edited_serial=2)
    assert zr.edited_serial == 2 and ZoneResponse(id="a.", name="a.", kind="Native", serial=1).edited_serial is None


def test_server_info_url_optional():
    assert ServerInfo(name="ns1", is_reachable=True).url is None


# --------------------------------------------------------------------------- LUA-Gate in records.py
@pytest.fixture
def pdns(monkeypatch):
    fake = FakePDNS()
    monkeypatch.setattr(pdns_manager, "clients", {"srv1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})

    async def enqueue(*a, **k):
        return 0

    async def detached(*a, **k):
        return None

    monkeypatch.setattr(webhook_outbox, "enqueue_event", enqueue)
    monkeypatch.setattr(audit_service, "write_audit_detached", detached)
    return fake


def _session_client(user, policy=None, zone_access=()):
    extra = {SystemSetting: [("lua_records_policy", policy)]} if policy else {}
    session = FakeSession(user_row=user, zone_access=zone_access, extra=extra)
    c = TestClient(build_app(session, records), raise_server_exceptions=False)
    jwt = create_access_token(data={"sub": str(user.id)}, user=user)
    c.headers["Authorization"] = f"Bearer {jwt}"
    return c


LUA_RECORD = {"name": "geo.allowed.example.", "type": "LUA", "ttl": 60, "records": [{"content": LUA_OK}]}


def test_lua_create_denied_for_non_admin_by_default(pdns):
    user = make_user(role="user", uid=5, username="bob")
    c = _session_client(user, zone_access=[("allowed.example.", "manage")])
    r = c.post("/api/v1/records/srv1/allowed.example.", json=LUA_RECORD)
    assert r.status_code == 403 and r.json()["detail"] == MSG_DENIED_ADMIN
    r = c.post("/api/v1/records/srv1/allowed.example./bulk", json={"create": [LUA_RECORD], "delete": []})
    assert r.status_code == 403
    r = c.put("/api/v1/records/srv1/allowed.example.", json={"name": "geo.allowed.example.", "type": "LUA",
                                                            "old_content": LUA_OK, "new_content": LUA_OK})
    assert r.status_code == 403
    assert pdns.calls == {}
    # Loeschen bleibt erlaubt (F15 5.6), normale Typen ebenfalls
    r = c.request("DELETE", "/api/v1/records/srv1/allowed.example./delete",
                  json={"name": "geo.allowed.example.", "type": "LUA"})
    assert r.status_code == 200
    assert c.post("/api/v1/records/srv1/allowed.example.", json={
        "name": "www.allowed.example.", "type": "A", "records": [{"content": "192.0.2.1"}]}).status_code == 200


def test_lua_policy_manage_allows_zone_managers_and_disabled_blocks_admins(pdns):
    user = make_user(role="user", uid=5, username="bob")
    c = _session_client(user, policy="manage", zone_access=[("allowed.example.", "manage")])
    assert c.post("/api/v1/records/srv1/allowed.example.", json=LUA_RECORD).status_code == 200
    c = _session_client(make_user(), policy="disabled")
    r = c.post("/api/v1/records/srv1/allowed.example.", json=LUA_RECORD)
    assert r.status_code == 403 and r.json()["detail"] == MSG_DENIED_DISABLED


def test_lua_admin_session_ok_but_token_without_allow_admin_denied(pdns):
    assert _session_client(make_user()).post("/api/v1/records/srv1/allowed.example.", json=LUA_RECORD).status_code == 200
    session = FakeSession(token_row=make_token(scope_zones=None), user_row=make_user())
    c = TestClient(build_app(session, records), raise_server_exceptions=False)
    r = c.post("/api/v1/records/srv1/allowed.example.", json=LUA_RECORD, headers=bearer())
    assert r.status_code == 403 and r.json()["detail"] == MSG_DENIED_ADMIN
