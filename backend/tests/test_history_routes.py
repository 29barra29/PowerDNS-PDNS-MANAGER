"""Routen von Zonenverlauf und Audit-Log (F7 9.2 Nr. 29–32) sowie Sichtbarkeit fuer Nicht-Admins [S12, D12].

- Nr. 29–31: ``TestClient`` ohne lifespan/DB, ``Authorization: Bearer x`` (CSRF-neutral): die Routen matchen
  (401 statt 404/405 durch Shadowing oder SPA-Catch-all).
- Nr. 32: Routen-Walk – Aufbewahrungs-Einstellung nur per Admin-Browser-Session, Einzeleintrag nur Admin.
- Liste/Einzeleintrag direkt mit Fake-Session: ``public_details`` fuer Nicht-Admins, Ausblenden der Eintraege vor
  der letzten endgueltigen Zonenloeschung, ``before_recreate`` fuer Admins.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from fakes.pdns import FakeDB, FakeResult, fake_pdns  # noqa: E402,F401 - Fixture
from app.core.auth import get_admin_session_user, get_admin_user, get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import AuditLog, ServerConfig, User, UserZoneAccess  # noqa: E402
from app.routers import history  # noqa: E402
from route_policy import iter_app_routes  # noqa: E402

A = "/api/v1"
Z = "example.com."
client = TestClient(app, raise_server_exceptions=False)
AUTH = {"Authorization": "Bearer x"}


# --- Nr. 29–31: Routen matchen ---------------------------------------------------------------------------------
def test_rollback_post_matches_route():
    r = client.post(f"{A}/zones/ns1/{Z}/history/5/rollback", headers=AUTH, json={"force": False})
    assert r.status_code == 401, r.text
    r = client.post(f"{A}/zones/ns1/{Z}/history/5/rollback", headers=AUTH)  # Body optional
    assert r.status_code == 401


@pytest.mark.parametrize("path", [
    f"{A}/zones/ns1/{Z}/history",
    f"{A}/zones/ns1/{Z}/history/5",
    f"{A}/zones/ns1/{Z}/history/5/rollback-preview",
    f"{A}/zones/ns1/sub.{Z}/history?type=A&name=www.{Z}",
])
def test_history_get_routes_match(path):
    r = client.get(path, headers=AUTH)
    assert r.status_code == 401, (path, r.status_code, r.text[:200])
    assert "text/html" not in r.headers.get("content-type", "")


@pytest.mark.parametrize("method,path", [
    ("GET", f"{A}/audit-log"),
    ("GET", f"{A}/audit-log/export"),
    ("GET", f"{A}/audit-log/settings"),
    ("PUT", f"{A}/audit-log/settings"),
    ("GET", f"{A}/audit-log/7"),
])
def test_audit_routes_match(method, path):
    r = client.request(method, path, headers=AUTH, json={"retention_days": 30} if method == "PUT" else None)
    assert r.status_code == 401, (method, path, r.status_code)


# --- Nr. 32: Routen-Walk ----------------------------------------------------------------------------------------
def _route(method, path):
    for r in iter_app_routes(app):
        if r.method == method and r.path == path:
            return r
    raise AssertionError(f"Route {method} {path} fehlt")


def _direct(route):
    return [d.call for d in route.dependant.dependencies]


def test_audit_settings_require_admin_session():
    for method in ("GET", "PUT"):
        assert get_admin_session_user in _direct(_route(method, f"{A}/audit-log/settings"))
    assert get_admin_user in _direct(_route("GET", f"{A}/audit-log/{{entry_id:int}}"))
    for method, suffix in (("GET", ""), ("GET", "/{audit_id:int}"), ("GET", "/{audit_id:int}/rollback-preview"),
                           ("POST", "/{audit_id:int}/rollback")):
        route = _route(method, f"{A}/zones/{{server_name}}/{{zone_id:path}}/history{suffix}")
        assert get_current_user in _direct(route)


def test_history_router_is_included_before_zones():
    from app.main import ROUTER_MODULES

    names = [m.__name__.rsplit(".", 1)[-1] for m in ROUTER_MODULES]
    assert names.index("history") < names.index("zones")
    assert history.ROUTER_ORDER == 50


# --- Sichtbarkeit (public_details, zone_recreated) --------------------------------------------------------------
def _log(id, **kw):
    base = dict(id=id, timestamp=datetime(2026, 10, 1, 12, 0, id), action="UPDATE", resource_type="record",
                resource_name=f"www.{Z}", server_name="ns1", zone_name=Z, user_id=3, status="success",
                error_message=None, revert_of_id=None, actor_username="alice", client_ip="198.51.100.7",
                details={"version": 2, "zone": Z, "after_source": "reread", "fanout": {"ns1": "saved"},
                         "client_ip": "198.51.100.7",
                         "changes": [{"name": f"www.{Z}", "type": "A", "before": None,
                                      "after": {"ttl": 60, "records": [{"content": "192.0.2.1", "disabled": False}],
                                                "comments": []}}]})
    base.update(kw)
    return AuditLog(**base)


class ListDB(FakeDB):
    def __init__(self, rows, *, perm=None, zone_deletes=()):
        super().__init__(execute_handler=self._handle)
        self.rows = rows
        self.perm = perm
        self.zone_deletes = list(zone_deletes)
        for r in rows:
            self.objects[(AuditLog, r.id)] = r

    def _handle(self, stmt):
        desc = getattr(stmt, "column_descriptions", None) or []
        entity = desc[0].get("entity") if desc else None
        names = [d.get("name") for d in desc]
        if entity is ServerConfig:
            return FakeResult([])
        if entity is UserZoneAccess:
            return FakeResult([self.perm] if self.perm else [])
        if entity is User:
            return FakeResult([(3, "alice")])
        if entity is AuditLog and names == ["AuditLog"]:
            return FakeResult(self.rows)
        if entity is AuditLog and names == ["id", "details"]:
            return FakeResult(self.zone_deletes)
        if entity is AuditLog and names == ["user_id"]:
            return FakeResult([(3,)])
        return FakeResult([])

    async def scalar(self, stmt, *a, **k):
        self.executed.append(stmt)
        return len(self.rows)


def _user(role="user"):
    return User(id=3 if role == "user" else 1, username="alice" if role == "user" else "admin", role=role,
                is_active=True, hashed_password="x")


def _list_kwargs(**kw):
    base = dict(limit=50, offset=0, action=None, resource_type=None, record_type=None, name=None, q=None, user_id=None,
                status_filter=None, date_from=None, date_to=None)
    base.update(kw)
    return base


async def test_list_non_admin_gets_public_details_and_no_write(fake_pdns):
    rows = [_log(31, status="error", error_message='{"error": "intern: /var/lib/pdns"}'), _log(30)]
    db = ListDB(rows, perm="read", zone_deletes=[(20, {"zone_still_on_other_server": False})])
    out = await history.list_zone_history("ns1", "Example.COM", db, current_user=_user(), **_list_kwargs())
    assert out["zone"] == Z and out["total"] == 2 and out["actors"] == [{"user_id": 3, "username": "alice"}]
    e0, e1 = out["entries"]
    assert e0["error_message"] == "PowerDNS-Fehler" and e0["client_ip"] is None
    assert "client_ip" not in e0["details"] and "changes" not in e0["details"]
    assert e1["rollback_blocked_reason"] == "no_write_permission" and e1["can_rollback"] is False
    assert e1["changes"][0]["after"]["ttl"] == 60 and e1["username"] == "alice"
    # Eintraege bis zur letzten endgueltigen Zonenloeschung sind per SQL ausgeblendet (id > 20)
    sql = [s for s in db.executed if getattr(s, "column_descriptions", None)
           and [d.get("name") for d in s.column_descriptions] == ["AuditLog"]][0]
    compiled = sql.compile()
    assert "audit_logs.id >" in str(compiled) and 20 in compiled.params.values()


async def test_list_non_admin_q_does_not_search_hidden_fields(fake_pdns):
    """Fix-Runde [S12]: ``q`` darf fuer Nicht-Admins weder den ganzen Details-Text noch error_message treffen
    (sonst waere ``total`` ein Ja/Nein-Orakel fuer ``auth.token_prefix``, ``client_ip``, PowerDNS-Rohtexte)."""
    from sqlalchemy.dialects import mysql

    async def where_sql(user, perm):
        db = ListDB([_log(30)], perm=perm)
        await history.list_zone_history("ns1", Z, db, current_user=user, **_list_kwargs(q="198.51.100"))
        audit_stmts = [s for s in db.executed if "audit_logs" in str(s) and "198.51.100" in str(s.compile().params)]
        assert len(audit_stmts) == 2  # count (total) und Trefferliste
        return [str(s.whereclause.compile(dialect=mysql.dialect())) for s in audit_stmts]

    for sql in await where_sql(_user(), "read"):
        assert "error_message" not in sql and "CAST(audit_logs.details AS CHAR)" not in sql
        assert "json_extract(audit_logs.details" in sql and "resource_name" in sql
    for sql in await where_sql(_user("admin"), None):
        assert "error_message" in sql and "CAST(audit_logs.details AS CHAR)" in sql


async def test_list_admin_sees_everything_with_before_recreate(fake_pdns):
    rows = [_log(30), _log(15)]
    db = ListDB(rows, zone_deletes=[(20, {"zone_still_on_other_server": False})])
    out = await history.list_zone_history("ns1", Z, db, current_user=_user("admin"), **_list_kwargs())
    new, old = out["entries"]
    assert new["before_recreate"] is False and new["can_rollback"] is True and new["client_ip"] == "198.51.100.7"
    assert old["before_recreate"] is True and old["rollback_blocked_reason"] == "zone_recreated"
    sql = [s for s in db.executed if getattr(s, "column_descriptions", None)
           and [d.get("name") for d in s.column_descriptions] == ["AuditLog"]][0]
    assert 20 not in sql.compile().params.values()


async def test_entry_hidden_for_non_admin_before_recreate(fake_pdns):
    db = ListDB([_log(15), _log(30), _log(40, zone_name="other.example.")], perm="manage",
                zone_deletes=[(20, {"zone_still_on_other_server": False})])
    with pytest.raises(HTTPException) as ei:
        await history.get_zone_history_entry("ns1", Z, 15, db, _user())
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        await history.get_zone_history_entry("ns1", Z, 40, db, _user())  # fremde Zone
    assert ei.value.status_code == 404 and ei.value.detail == "Audit-Eintrag nicht gefunden"
    ok = await history.get_zone_history_entry("ns1", Z, 30, db, _user())
    assert ok["id"] == 30 and ok["can_rollback"] is True and ok["changes_truncated"] is False
    admin_view = await history.get_zone_history_entry("ns1", Z, 15, db, _user("admin"))
    assert admin_view["before_recreate"] is True


async def test_zone_delete_still_on_other_server_does_not_hide(fake_pdns):
    db = ListDB([_log(15)], perm="manage", zone_deletes=[(20, {"zone_still_on_other_server": True})])
    ok = await history.get_zone_history_entry("ns1", Z, 15, db, _user())
    assert ok["before_recreate"] is False


async def test_non_admin_without_zone_access_is_403(fake_pdns):
    db = ListDB([_log(30)])
    with pytest.raises(HTTPException) as ei:
        await history.list_zone_history("ns1", Z, db, current_user=_user(), **_list_kwargs())
    assert ei.value.status_code == 403


async def test_invalid_date_range_is_400(fake_pdns):
    db = ListDB([])
    with pytest.raises(HTTPException) as ei:
        await history.list_zone_history("ns1", Z, db, current_user=_user("admin"), **_list_kwargs(
            date_from=datetime(2026, 10, 2), date_to=datetime(2026, 10, 1)))
    assert ei.value.status_code == 400


# --- Admin-Audit-Log: Liste, Einzeleintrag, Aufbewahrung (ohne DB) --------------------------------------------
class AuditDB(ListDB):
    """Zusaetzlich: Zaehler/aeltester Eintrag fuer die Aufbewahrungs-Anzeige."""

    async def execute(self, stmt, *a, **k):
        desc = getattr(stmt, "column_descriptions", None) or []
        names = [d.get("name") for d in desc]
        if names and names[0] == "count":
            self.executed.append(stmt)
            res = FakeResult([(len(self.rows), datetime(2026, 1, 2, 3, 4, 5))])
            res.one = lambda: (len(self.rows), datetime(2026, 1, 2, 3, 4, 5))
            return res
        return await super().execute(stmt, *a, **k)


async def test_audit_list_total_username_and_truncation():
    from app.routers import search

    many = [{"name": f"h{i}.{Z}", "type": "A", "before": None, "after": None} for i in range(25)]
    rows = [_log(2, details={"version": 2, "zone": Z, "changes": many}), _log(1, user_id=99)]
    db = AuditDB(rows)
    out = await search.get_audit_log(db, limit=500, offset=0, action=None, resource_type=None, server_name=None,
                                     zone=None, user_id=None, status_filter=None, date_from=None, date_to=None,
                                     q=None, full=False, admin=None)
    assert out["count"] == 2 and out["total"] == 2 and out["limit"] == 500
    first, second = out["entries"]
    assert first["details_truncated"] is True and len(first["details"]["changes"]) == 20
    assert first["details"]["change_count"] == 25 and first["username"] == "alice"
    assert first["zone_name"] == Z and first["client_ip"] == "198.51.100.7"
    full = await search.get_audit_log(db, limit=500, offset=0, action=None, resource_type=None, server_name=None,
                                      zone=None, user_id=None, status_filter=None, date_from=None, date_to=None,
                                      q=None, full=True, admin=None)
    assert full["limit"] == 100 and full["entries"][0]["details_truncated"] is False


async def test_audit_entry_found_and_404():
    from app.routers import search

    db = AuditDB([_log(5)])
    entry = await search.get_audit_log_entry(5, db, admin=None)
    assert entry["id"] == 5 and entry["details_truncated"] is False and entry["username"] == "alice"
    with pytest.raises(HTTPException) as ei:
        await search.get_audit_log_entry(6, db, admin=None)
    assert ei.value.status_code == 404 and ei.value.detail == "Audit-Eintrag nicht gefunden"


async def test_audit_settings_get_and_put(monkeypatch):
    from app.routers import search
    from app.schemas.history import AuditSettingsUpdate
    from app.services import audit as audit_service, system_settings

    store = {"audit_retention_days": "0", "audit_last_purge_at": "2026-10-01T00:00:00+00:00",
             "audit_last_purge_deleted": "12"}
    audits = []

    async def get_settings(db, keys):
        return {k: store[k] for k in keys if k in store}

    async def set_setting(db, key, value):
        store[key] = value

    async def write_audit(db, action, resource_type, resource_name=None, **kw):
        audits.append((action, resource_type, resource_name, kw))
        return None

    monkeypatch.setattr(system_settings, "get_settings", get_settings)
    monkeypatch.setattr(system_settings, "set_setting", set_setting)
    monkeypatch.setattr(audit_service, "write_audit", write_audit)
    db = AuditDB([_log(1), _log(2)])
    admin = _user("admin")
    s = await search.get_audit_settings(db, admin=admin)
    assert s == {"retention_days": 0, "last_purge_at": "2026-10-01T00:00:00+00:00", "last_purge_deleted": 12,
                 "worker_enabled": False, "total_entries": 2, "oldest_entry_at": "2026-01-02T03:04:05+00:00"}
    out = await search.update_audit_settings(AuditSettingsUpdate(retention_days=30), db, admin=admin)
    assert out["message"] == "Aufbewahrung gespeichert" and out["settings"]["retention_days"] == 30
    assert store["audit_retention_days"] == "30"
    assert audits == [("AUDIT_SETTINGS_UPDATE", "settings", "audit_retention",
                       {"user_id": 1, "details": {"retention_days": {"from": 0, "to": 30}}})]


@pytest.mark.parametrize("value,ok", [(0, True), (7, True), (3650, True), (1, False), (6, False), (3651, False),
                                      (-1, False)])
def test_audit_settings_validator(value, ok):
    from pydantic import ValidationError

    from app.schemas.history import AuditSettingsUpdate

    if ok:
        assert AuditSettingsUpdate(retention_days=value).retention_days == value
    else:
        with pytest.raises(ValidationError):
            AuditSettingsUpdate(retention_days=value)


def test_audit_settings_validator_message():
    from pydantic import ValidationError

    from app.schemas.history import AuditSettingsUpdate

    with pytest.raises(ValidationError) as ei:
        AuditSettingsUpdate(retention_days=3)
    assert "0 (unbegrenzt) oder zwischen 7 und 3650" in str(ei.value)
