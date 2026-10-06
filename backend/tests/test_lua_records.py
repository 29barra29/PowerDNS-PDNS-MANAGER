"""F15 9.1 – LUA-Records: Validator, Schemas, Policy, Routen (records/lua/settings_lua), Server-Status, Sync mit dem
Frontend. Ohne DB: ``FakeSession`` (authfakes) fuer Auth/Settings, ``FakePowerDNSClient`` (fakes.pdns) fuer PowerDNS.

Die Kernfaelle des Validators stehen zusaetzlich in ``test_lua_records_core.py`` (Welle 0); hier die vollstaendige
Liste aus der Spec und alles, was in Welle 3 dazukam (Server-Status, Router, Template-Validator, Sync-Test).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from authfakes import PLAIN, FakeSession, bearer, build_app, make_token, make_user
from fakes.pdns import FakePowerDNSClient, make_zone, rr
from app.core.auth import create_access_token, scope_from_token
from app.core.request_context import current_token_scope
from app.models.models import SystemSetting
from app.routers import lua as lua_router
from app.routers import records, settings_lua
from app.routers.templates import TemplateRecord
from app.schemas.dns import ALLOWED_RECORD_TYPES, RecordCreate, RecordDelete, RecordUpdate
from app.services import audit as audit_service
from app.services import fanout, lua_records as lr, webhook_outbox
from app.services.pdns_client import PowerDNSAPIError, pdns_manager

Z = "example.com."

# Vorlagen aus F15 6.4 (identisch mit LUA_TEMPLATES in frontend/src/lib/luaRecord.js)
TEMPLATES = [
    ("A", "ifportup(443, {'192.0.2.1', '192.0.2.2'})"),
    ("AAAA", "ifportup(443, {'2001:db8::1', '2001:db8::2'})"),
    ("A", "ifurlup('https://www.example.com/health', {{'192.0.2.1', '192.0.2.2'}, {'198.51.100.1'}}, "
          "{stringmatch='OK'})"),
    ("A", "pickclosest({'192.0.2.1', '198.51.100.1', '203.0.113.1'})"),
    ("A", "pickwrandom({{70, '192.0.2.1'}, {30, '192.0.2.2'}})"),
    ("A", "view({{{'10.0.0.0/8', '192.168.0.0/16'}, {'10.0.0.10'}}, {{'0.0.0.0/0', '::/0'}, {'203.0.113.10'}}})"),
    ("A", ";if country({'DE', 'AT', 'CH'}) then return '192.0.2.10' end return '198.51.100.10'"),
    ("A", ";if continent('EU') then return {'192.0.2.10'} else return {'198.51.100.10'} end"),
    ("TXT", "latlon()"),
]


@pytest.fixture(autouse=True)
def _clean_status_cache():
    lr.clear_status_cache()
    yield
    lr.clear_status_cache()


def _client(session: FakeSession, *routers, user=None, token: str | None = None) -> TestClient:
    c = TestClient(build_app(session, *routers), raise_server_exceptions=False)
    if token:
        c.headers.update(bearer(token))
    elif user is not None:
        c.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    return c


def _settings_rows(policy: str | None) -> dict:
    return {SystemSetting: [("lua_records_policy", policy)]} if policy is not None else {}


# --------------------------------------------------------------------------- 1-4: Validator
@pytest.mark.parametrize("rtype,code", TEMPLATES)
def test_templates_valid_and_unchanged(rtype, code):
    content = f'{rtype} "{code}"'
    assert lr.validate_lua_content(content) == content


def test_normalization_type_and_chunks():
    assert lr.validate_lua_content("a   \"pickrandom({'192.0.2.1'})\"") == "A \"pickrandom({'192.0.2.1'})\""
    assert lr.validate_lua_content("A \"pick\" \"random({'1.2.3.4'})\"") == "A \"pick\" \"random({'1.2.3.4'})\""
    # mehrere Abschnitte: genau ein Leerzeichen dazwischen (so speichert PowerDNS den Inhalt)
    assert lr.validate_lua_content("A \"pick\"    \"random({'1.2.3.4'})\"") == "A \"pick\" \"random({'1.2.3.4'})\""
    assert lr.normalize_lua_content("a  \"x(\"   \")\" ") == 'A "x(" ")"'


def test_long_strings_and_comments_valid():
    assert lr.validate_lua_content("A \"[[ ( ]] .. pickrandom({'1.2.3.4'}) -- ( kommentar\"")


TYPES_LIST = "A, AAAA, CNAME, TXT, MX, SRV, PTR, CAA, NAPTR, LOC, SPF, HTTPS, SVCB, SSHFP, TLSA"


@pytest.mark.parametrize("content,message", [
    ("", "LUA-Inhalt fehlt."),
    ("A", "LUA-Inhalt muss mit dem Ziel-Typ beginnen, z. B. A \"ifportup(443, {'192.0.2.1'})\"."),
    ('NS "x()"', f"Ziel-Typ NS ist für LUA nicht erlaubt. Erlaubt: {TYPES_LIST}"),
    ('LUA "x()"', f"Ziel-Typ LUA ist für LUA nicht erlaubt. Erlaubt: {TYPES_LIST}"),
    ("A x()", "Der LUA-Code muss in doppelten Anführungszeichen stehen."),
    ('A "a" b', "Anführungszeichen im LUA-Inhalt sind nicht ausgeglichen."),
    ('A "x(\\"y\\")"', "Doppelte Anführungszeichen im LUA-Code sind nicht erlaubt – bitte einfache (') oder [[…]] "
                       "verwenden."),
    ('A ""', "Der LUA-Code ist leer."),
    ('A "f({1)"', "Klammern im LUA-Code sind nicht ausgeglichen ((), {}, [])."),
    ("A \"f('x)\"", "Eine Zeichenkette im LUA-Code ist nicht geschlossen."),
    ('A "--[[ offen"', "Ein Block-Kommentar (--[[ … ]]) im LUA-Code ist nicht geschlossen."),
    ('A "a\nb"', "LUA-Inhalt darf keine Zeilenumbrüche, Tabulatoren oder Steuerzeichen enthalten."),
    ('A "' + "x" * 4000 + '"', "LUA-Inhalt ist zu lang (max. 4000 Zeichen)."),
])
def test_invalid_contents_exact_messages(content, message):
    with pytest.raises(ValueError) as ei:
        lr.validate_lua_content(content)
    assert str(ei.value) == message


# --------------------------------------------------------------------------- 5-8: Schemas
def test_record_create_lua_normalized_and_invalid_rejected():
    rec = RecordCreate(name="w.example.com", type="lua", ttl=60,
                       records=[{"content": "a \"ifportup(443, {'192.0.2.1'})\""}])
    assert rec.type == "LUA"
    assert rec.records[0].content == "A \"ifportup(443, {'192.0.2.1'})\""
    with pytest.raises(ValidationError) as ei:
        RecordCreate(name="w.example.com", type="LUA", ttl=60, records=[{"content": 'A "f({1)"'}])
    assert "Klammern im LUA-Code sind nicht ausgeglichen" in str(ei.value)


@pytest.mark.parametrize("factory", [
    lambda: RecordCreate(name="w.example.com", type="TYPE65402", ttl=60, records=[{"content": 'A "x()"'}]),
    lambda: RecordUpdate(name="w.example.com", type="type65402", old_content='A "x()"', new_content='A "y()"'),
    lambda: RecordDelete(name="w.example.com", type="TYPE1"),
])
def test_generic_type_notation_rejected(factory):
    with pytest.raises(ValidationError) as ei:
        factory()
    assert "Generische Typangaben" in str(ei.value)


def test_record_update_normalizes_only_new_content():
    upd = RecordUpdate(name="w.example.com", type="LUA", new_content='A  "x()"', old_content='A "y()"')
    assert upd.new_content == 'A "x()"' and upd.old_content == 'A "y()"'


def test_template_record_validates_lua():
    with pytest.raises(ValidationError) as ei:
        TemplateRecord(name="@", type="LUA", content='A "x(")')
    assert "LUA" in str(ei.value)
    ok = TemplateRecord(name="@", type="lua", content="a   \"ifportup(443, {'192.0.2.1'})\"")
    assert ok.content == "A \"ifportup(443, {'192.0.2.1'})\""
    # andere Typen bleiben unberuehrt
    assert TemplateRecord(name="@", type="TXT", content='"x(")').content == '"x(")'


# --------------------------------------------------------------------------- 9-11: Policy
ADMIN = SimpleNamespace(role="admin")
USER = SimpleNamespace(role="user")


@pytest.mark.parametrize("policy,user,expected", [
    ("admin", ADMIN, True), ("admin", USER, False), ("manage", USER, True),
    ("manage", ADMIN, True), ("disabled", ADMIN, False), ("disabled", USER, False),
])
def test_policy_matrix(policy, user, expected):
    assert lr.lua_policy_allows(policy, user) is expected


def test_policy_admin_needs_token_with_allow_admin():
    """F14: ein Admin-Token ohne ``allow_admin`` ist fuer die Policy ``admin`` kein Admin."""
    reset = current_token_scope.set(scope_from_token(make_token(allow_admin=False, scope_zones=None)))
    try:
        assert lr.lua_policy_allows("admin", ADMIN) is False
        assert lr.lua_policy_allows("manage", ADMIN) is True
    finally:
        current_token_scope.reset(reset)
    reset = current_token_scope.set(scope_from_token(make_token(allow_admin=True, scope_zones=None)))
    try:
        assert lr.lua_policy_allows("admin", ADMIN) is True
    finally:
        current_token_scope.reset(reset)


@pytest.mark.parametrize("raw,expected", [(None, "admin"), (" Manage ", "manage"), ("bogus", "admin"),
                                          ("DISABLED", "disabled"), ("", "admin")])
async def test_get_lua_policy(monkeypatch, raw, expected):
    from app.services import system_settings

    async def fake_get_setting(db, key, default=None):
        assert key == "lua_records_policy"
        return raw

    monkeypatch.setattr(system_settings, "get_setting", fake_get_setting)
    assert await lr.get_lua_policy(object()) == expected


async def test_assert_lua_write_allowed_messages(monkeypatch):
    for policy, message in (("admin", lr.MSG_DENIED_ADMIN), ("disabled", lr.MSG_DENIED_DISABLED)):
        async def fake_policy(db, _p=policy):
            return _p

        monkeypatch.setattr(lr, "get_lua_policy", fake_policy)
        with pytest.raises(HTTPException) as ei:
            await lr.assert_lua_write_allowed(object(), USER)
        assert ei.value.status_code == 403 and ei.value.detail == message
    assert lr.MSG_DENIED_ADMIN == "LUA-Records dürfen nur Administratoren anlegen oder ändern."
    assert lr.MSG_DENIED_DISABLED == "LUA-Records sind in diesem Panel deaktiviert (Einstellungen → DNS-Optionen)."


# --------------------------------------------------------------------------- 12: Record-Routen
LUA_CONTENT = "A \"ifportup(443, {'192.0.2.1'})\""
LUA_BODY = {"name": "geo.example.com.", "type": "LUA", "ttl": 60, "records": [{"content": "a  " + LUA_CONTENT[2:]}]}


@pytest.fixture
def ns1(monkeypatch):
    fake = FakePowerDNSClient("ns1", [make_zone(Z, [rr(Z, "NS", "ns1.example.com."),
                                                    rr("old.example.com.", "LUA", LUA_CONTENT, ttl=60)])])
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    monkeypatch.setattr(fanout, "REREAD_DELAY", 0)

    async def enqueue(*a, **k):
        return 0

    async def audit(*a, **k):
        return None

    monkeypatch.setattr(webhook_outbox, "enqueue_event", enqueue)
    monkeypatch.setattr(records, "write_audit", audit)
    monkeypatch.setattr(audit_service, "write_audit_detached", audit)
    return fake


def test_record_routes_policy_gate_before_pdns(ns1, monkeypatch):
    user = make_user(role="user", uid=5, username="bob")
    c = _client(FakeSession(user_row=user, zone_access=[(Z, "manage")]), records, user=user)

    async def no_targets(*a, **k):
        pytest.fail("Policy-Gate muss vor _writable_targets_for_zone greifen")

    monkeypatch.setattr(records, "_writable_targets_for_zone", no_targets)
    r = c.post(f"/api/v1/records/ns1/{Z}", json=LUA_BODY)
    assert r.status_code == 403 and r.json()["detail"] == lr.MSG_DENIED_ADMIN
    r = c.put(f"/api/v1/records/ns1/{Z}", json={"name": "old.example.com.", "type": "LUA", "ttl": 60,
                                                "old_content": LUA_CONTENT, "new_content": LUA_CONTENT})
    assert r.status_code == 403 and r.json()["detail"] == lr.MSG_DENIED_ADMIN
    assert ns1.calls == []


def test_record_create_as_admin_sends_normalized_lua(ns1):
    admin = make_user()
    c = _client(FakeSession(user_row=admin), records, user=admin)
    r = c.post(f"/api/v1/records/ns1/{Z}", json=LUA_BODY)
    assert r.status_code == 200, r.text
    rrsets = ns1.patches[-1]
    assert rrsets[0]["type"] == "LUA" and rrsets[0]["name"] == "geo.example.com."
    assert [x["content"] for x in rrsets[0]["records"]] == [LUA_CONTENT]
    assert ns1.values(Z, "geo.example.com.", "LUA") == [LUA_CONTENT]


def test_record_delete_lua_without_policy_check(ns1, monkeypatch):
    calls = []

    async def spy(*a, **k):
        calls.append(a)
        raise AssertionError("Policy darf beim Loeschen nicht geprueft werden")

    monkeypatch.setattr(lr, "assert_lua_write_allowed", spy)
    monkeypatch.setattr(lr, "get_lua_policy", spy)
    user = make_user(role="user", uid=5, username="bob")
    c = _client(FakeSession(user_row=user, zone_access=[(Z, "manage")]), records, user=user)
    r = c.request("DELETE", f"/api/v1/records/ns1/{Z}/delete",
                  json={"name": "old.example.com.", "type": "LUA", "content": LUA_CONTENT})
    assert r.status_code == 200, r.text
    assert calls == []
    assert ns1.rrset(Z, "old.example.com.", "LUA") is None


# --------------------------------------------------------------------------- 13: /settings/lua
def _route_contexts(app):
    from route_policy import _iter_route_contexts

    return list(_iter_route_contexts(app.routes))


def _route_dependency_names(app, path: str, method: str) -> list[str]:
    for rc in _route_contexts(app):
        if rc.path == path and method in (rc.methods or ()):
            return [getattr(d.call, "__name__", "") for d in rc.dependant.dependencies]
    raise AssertionError(f"Route {method} {path} fehlt")


def test_settings_routes_need_admin_browser_session():
    from app.main import app

    for method in ("GET", "PUT"):
        names = _route_dependency_names(app, "/api/v1/settings/lua", method)
        assert "get_admin_session_user" in names, (method, names)
    # Panel-Token (auch mit allow_admin) -> 403
    session = FakeSession(token_row=make_token(allow_admin=True, scope_zones=None), user_row=make_user())
    c = _client(session, settings_lua, token=PLAIN)
    assert c.get("/api/v1/settings/lua").status_code == 403
    # Nicht-Admin mit Browser-Session -> 403
    user = make_user(role="user", uid=5, username="bob")
    assert _client(FakeSession(user_row=user), settings_lua, user=user).get("/api/v1/settings/lua").status_code == 403


def test_settings_get_returns_policy_and_limits():
    admin = make_user()
    c = _client(FakeSession(user_row=admin, extra=_settings_rows("manage")), settings_lua, user=admin)
    r = c.get("/api/v1/settings/lua")
    assert r.status_code == 200
    assert r.json() == {"policy": "manage", "default_policy": "admin", "target_types": list(lr.LUA_TARGET_TYPES),
                        "max_content_length": 4000}


def test_settings_put_validates_sets_and_audits(monkeypatch):
    saved, audits = [], []

    async def spy_set(db, key, value):
        saved.append((key, value))

    async def spy_audit(db, action, resource_type, resource_name=None, **kw):
        audits.append((action, resource_type, resource_name, kw.get("details"), kw.get("user_id")))

    monkeypatch.setattr(settings_lua, "set_setting", spy_set)
    monkeypatch.setattr(settings_lua, "write_audit", spy_audit)
    admin = make_user()
    c = _client(FakeSession(user_row=admin), settings_lua, user=admin)  # kein Key -> admin
    assert c.put("/api/v1/settings/lua", json={"policy": "all"}).status_code == 422
    r = c.put("/api/v1/settings/lua", json={"policy": "manage"})
    assert r.status_code == 200
    assert r.json() == {"message": "LUA-Einstellungen gespeichert", "settings": {"policy": "manage"}}
    assert saved == [("lua_records_policy", "manage")]
    assert audits == [("LUA_SETTINGS_UPDATE", "settings", "lua",
                       {"changed": {"policy": {"from": "admin", "to": "manage"}}}, admin.id)]
    # gleicher Wert -> weder Schreiben noch Audit
    saved.clear()
    audits.clear()
    c = _client(FakeSession(user_row=admin, extra=_settings_rows("manage")), settings_lua, user=admin)
    assert c.put("/api/v1/settings/lua", json={"policy": "manage"}).status_code == 200
    assert saved == [] and audits == []


# --------------------------------------------------------------------------- 14: Sync Frontend/Backend
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _js_string_list(text: str, name: str) -> list[str]:
    m = re.search(rf"export const {name}\s*=\s*(?:Object\.freeze\()?\[([^\]]*)\]", text)
    assert m, f"{name} nicht gefunden"
    return re.findall(r"'([^']+)'", m.group(1))


def test_frontend_lists_match_backend():
    lua_js = FRONTEND / "lib" / "luaRecord.js"
    types_js = FRONTEND / "constants" / "dnsRecordTypes.js"
    if not lua_js.exists() or not types_js.exists():
        pytest.skip("Frontend-Quellen nicht vorhanden (Backend-only-Checkout)")
    lua_text = lua_js.read_text(encoding="utf-8")
    assert _js_string_list(lua_text, "LUA_TARGET_TYPES") == list(lr.LUA_TARGET_TYPES)
    m = re.search(r"export const LUA_MAX_CONTENT_LENGTH\s*=\s*(\d+)", lua_text)
    assert m and int(m.group(1)) == lr.LUA_MAX_CONTENT_LENGTH
    assert _js_string_list(lua_text, "GEO_FUNCTIONS") == list(lr.GEO_FUNCTIONS)
    types = _js_string_list(types_js.read_text(encoding="utf-8"), "ALL_RECORD_TYPE_KEYS")
    assert sorted(types) == sorted(ALLOWED_RECORD_TYPES)
    assert types.index("LUA") == types.index("ALIAS") + 1


# --------------------------------------------------------------------------- 15: parse_lua_config
def test_parse_lua_config_whitelist():
    cfg = [{"name": "enable-lua-records", "value": "shared"}, {"name": "launch", "value": "gmysql,geoip"},
           {"name": "gmysql-password", "value": "geheim"}, {"name": "edns-subnet-processing", "value": "yes"},
           {"name": "lua-records-exec-limit", "value": "1000"}, {"name": "lua-health-checks-interval", "value": "5"}]
    out = lr.parse_lua_config(cfg)
    assert out == {"lua_records": "shared", "geoip_backend": True, "edns_subnet_processing": True,
                   "exec_limit": 1000, "health_checks_interval": 5}
    assert "geheim" not in repr(out)
    missing = lr.parse_lua_config([{"name": "api", "value": "yes"}])
    assert missing["lua_records"] is None and missing["geoip_backend"] is None
    assert lr.parse_lua_config([{"name": "enable-lua-records", "value": "no"},
                                {"name": "launch", "value": "gsqlite3"}])["lua_records"] == "no"
    assert lr.parse_lua_config([{"name": "enable-lua-records", "value": "yes"},
                                {"name": "launch", "value": "gpgsql geoip:second"}])["geoip_backend"] is True
    assert lr.parse_lua_config(None)["lua_records"] is None


@pytest.mark.parametrize("exc,text,reachable", [
    (PowerDNSAPIError(503, "Cannot connect to http://intern:8081", "x"), "Server nicht erreichbar", False),
    (PowerDNSAPIError(504, "Timeout", "x", transport_error=True), "Zeitüberschreitung beim Abruf der Konfiguration",
     False),
    (PowerDNSAPIError(502, "Verbindungsfehler", "x", transport_error=True), "Server nicht erreichbar", False),
    (PowerDNSAPIError(401, '{"error": "Unauthorized"}', "x"), "API-Key abgelehnt (HTTP 401)", True),
    (PowerDNSAPIError(500, "boom", "x"), "Konfiguration nicht lesbar (HTTP 500)", True),
    (RuntimeError("kaputt"), "Konfiguration nicht lesbar", False),
])
def test_status_error_texts(exc, text, reachable):
    assert lr.status_error_text(exc) == (text, reachable)


# --------------------------------------------------------------------------- 16/17: /lua/*
@pytest.fixture
def two_status(monkeypatch):
    ok = FakePowerDNSClient("ns1")
    ok.config = [{"name": "enable-lua-records", "value": "yes"}, {"name": "launch", "value": "gsqlite3"},
                 {"name": "api-key", "value": "streng-geheim"}]
    down = FakePowerDNSClient("ns2")
    down.config = None

    async def fail(*a, **k):
        down.calls.append(("GET", "/config", None, {}, 0))
        raise PowerDNSAPIError(503, "Cannot connect to PowerDNS server 'ns2' at http://intern:8081", "ns2")

    down.get_config = fail
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": ok, "ns2": down})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    return SimpleNamespace(ok=ok, down=down)


def _config_calls(fake) -> int:
    return sum(1 for c in fake.calls if c[1] == "/config")


def test_server_status_cache_refresh_and_errors(two_status):
    user = make_user(role="user", uid=5, username="bob")
    uc = _client(FakeSession(user_row=user, zone_access=[(Z, "read")]), lua_router, user=user)
    r = uc.get("/api/v1/lua/server-status")
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {s["name"]: s for s in body["servers"]}
    assert by_name["ns1"]["lua_records"] == "yes" and by_name["ns1"]["reachable"] is True
    assert by_name["ns1"]["geoip_backend"] is False and by_name["ns1"]["error"] is None
    assert by_name["ns2"]["reachable"] is False and by_name["ns2"]["error"] == "Server nicht erreichbar"
    assert body["cached"] is False
    assert "streng-geheim" not in r.text and "intern" not in r.text
    assert _config_calls(two_status.ok) == 1 and _config_calls(two_status.down) == 1

    # zweiter Aufruf: Cache
    body2 = uc.get("/api/v1/lua/server-status").json()
    assert body2["cached"] is True and _config_calls(two_status.ok) == 1
    # refresh als Nicht-Admin: ignoriert
    assert uc.get("/api/v1/lua/server-status?refresh=true").status_code == 200
    assert _config_calls(two_status.ok) == 1
    # refresh als Admin: neue Abfrage
    admin = make_user()
    ac = _client(FakeSession(user_row=admin), lua_router, user=admin)
    assert ac.get("/api/v1/lua/server-status?refresh=true").json()["cached"] is False
    assert _config_calls(two_status.ok) == 2 and _config_calls(two_status.down) == 2


def test_server_status_needs_admin_or_zone_right(two_status):
    user = make_user(role="user", uid=6, username="eve")
    r = _client(FakeSession(user_row=user), lua_router, user=user).get("/api/v1/lua/server-status")
    assert r.status_code == 403
    assert _config_calls(two_status.ok) == 0
    # Panel-Token mit Scope, der keine Zone des Nutzers trifft -> ebenfalls kein Zonenrecht
    session = FakeSession(token_row=make_token(user_id=6, scope_zones=["other.example."]), user_row=user,
                          zone_access=[(Z, "manage")])
    assert _client(session, lua_router, token=PLAIN).get("/api/v1/lua/server-status").status_code == 403
    # Admin-Token ohne allow_admin: Rolle admin -> Status ja, refresh nein
    session = FakeSession(token_row=make_token(allow_admin=False, scope_zones=None), user_row=make_user())
    c = _client(session, lua_router, token=PLAIN)
    assert c.get("/api/v1/lua/server-status").status_code == 200
    assert c.get("/api/v1/lua/server-status?refresh=true").json()["cached"] is True
    assert _config_calls(two_status.ok) == 1


def test_server_status_lists_unloaded_servers_and_drops_stale_cache(two_status, monkeypatch):
    admin = make_user()
    c = _client(FakeSession(user_row=admin), lua_router, user=admin)
    assert len(c.get("/api/v1/lua/server-status").json()["servers"]) == 2
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": two_status.ok})
    monkeypatch.setattr(pdns_manager, "unloaded", {"ns3": "api key unreadable"})
    body = c.get("/api/v1/lua/server-status").json()
    assert [s["name"] for s in body["servers"]] == ["ns1", "ns3"]
    assert body["servers"][1]["reachable"] is False and body["servers"][1]["error"].startswith("Server nicht geladen")
    assert "ns2" not in lr._status_cache
    monkeypatch.setattr(pdns_manager, "clients", {})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    assert c.get("/api/v1/lua/server-status").json()["servers"] == []


def test_policy_endpoint_for_user_admin_and_token():
    user = make_user(role="user", uid=5, username="bob")
    body = _client(FakeSession(user_row=user), lua_router, user=user).get("/api/v1/lua/policy").json()
    assert body == {"policy": "admin", "can_write": False, "reason": lr.MSG_DENIED_ADMIN,
                    "target_types": list(lr.LUA_TARGET_TYPES), "max_content_length": 4000}
    body = _client(FakeSession(user_row=user, extra=_settings_rows("manage")), lua_router,
                   user=user).get("/api/v1/lua/policy").json()
    assert body["can_write"] is True and body["reason"] is None and body["policy"] == "manage"
    admin = make_user()
    body = _client(FakeSession(user_row=admin, extra=_settings_rows("disabled")), lua_router,
                   user=admin).get("/api/v1/lua/policy").json()
    assert body["can_write"] is False and body["reason"] == lr.MSG_DENIED_DISABLED
    # Admin-Token ohne allow_admin bei Policy admin -> kein Schreibrecht (F14)
    session = FakeSession(token_row=make_token(allow_admin=False, scope_zones=None), user_row=admin)
    assert _client(session, lua_router, token=PLAIN).get("/api/v1/lua/policy").json()["can_write"] is False


def test_routes_registered_in_app_with_plan_order():
    from app.main import app

    paths = {(m, rc.path) for rc in _route_contexts(app) for m in (rc.methods or ())}
    for key in (("GET", "/api/v1/lua/policy"), ("GET", "/api/v1/lua/server-status"),
                ("GET", "/api/v1/settings/lua"), ("PUT", "/api/v1/settings/lua")):
        assert key in paths, key
    assert lua_router.ROUTER_ORDER == 75 and settings_lua.ROUTER_ORDER == 105
