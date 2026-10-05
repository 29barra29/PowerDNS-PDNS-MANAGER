"""Audit/Historie gegen eine echte MariaDB (F7 9.3 Nr. 33–37; Gate ``dbutil.requires_db``).

- Nr. 33: ``init_db()`` zweimal – Spalten und Indizes von ``audit_logs`` vorhanden, keine Migrationsfehler.
- Nr. 34: Backfill ``audit_logs.zone_name`` fuer v1-Zeilen, Marker, zweiter Lauf ohne Wirkung.
- Nr. 35: Zonenverlauf ueber HTTP (``TestClient``, ``dependency_overrides``): JSON-Filter ``type``/``name``,
  ``q``, ``total``, Paginierung, ACL (403/Lese-Nutzer), ``reverted_by_id``, Ausblenden vor der Zonen-Neuanlage.
- Nr. 36: ``GET /audit-log`` (Filter, ``total``, Benutzernamen) und CSV-Export; Aufbewahrungs-Einstellung.
- Nr. 37: ``purge_expired_audit_logs`` mit Batch-Schleife.
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from dbutil import requires_db

pytestmark = requires_db

Z = "hist.example."
Z2 = "hist2.example."


def _run(coro):
    return asyncio.run(coro)


async def _add(*objs):
    from app.core.database import async_session

    async with async_session() as s:
        s.add_all(objs)
        await s.commit()
        return [o.id for o in objs]


async def _q(sql, params=None):
    from app.core.database import engine

    async with engine.connect() as c:
        return (await c.execute(text(sql), params or {})).all()


async def _exec(sql, params=None):
    from app.core.database import engine

    async with engine.begin() as c:
        await c.execute(text(sql), params or {})


def _snap(*contents, ttl=3600):
    return {"ttl": ttl, "records": [{"content": c, "disabled": False} for c in sorted(contents)], "comments": []}


def _v2(changes, **extra):
    return {"version": 2, "zone": Z, "changes": changes, "after_source": "reread", "fanout": {"ns1": "saved"}, **extra}


# --- Nr. 33 ------------------------------------------------------------------------------------------------------
def test_init_db_twice_columns_and_indexes(fresh_db):
    from app.core.database import MIGRATION_ERRORS, init_db

    _run(init_db())
    _run(init_db())
    assert MIGRATION_ERRORS == []
    cols = {r[0] for r in _run(_q(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'audit_logs'"))}
    assert {"zone_name", "revert_of_id", "actor_username", "client_ip"} <= cols
    idx = {r[2] for r in _run(_q("SHOW INDEX FROM audit_logs"))}
    assert {"ix_audit_logs_timestamp", "ix_audit_logs_zone_ts", "ix_audit_logs_user_id",
            "ix_audit_logs_revert_of_id"} <= idx


# --- Nr. 34 ------------------------------------------------------------------------------------------------------
def test_backfill_zone_names(fresh_db):
    from app.models.models import AuditLog
    from app.services.audit import BACKFILL_MARKER, backfill_audit_zone_names

    rows = [
        AuditLog(action="CREATE", resource_type="record", resource_name="www.bf.example.",
                 details={"zone": "BF.example", "type": "A"}, status="success"),
        AuditLog(action="BULK_UPDATE", resource_type="record", resource_name="bf.example",
                 details={"created": 1, "deleted": 0}, status="success"),
        AuditLog(action="DELETE", resource_type="zone", resource_name="Bf.Example.", details={}, status="success"),
        AuditLog(action="DNSSEC_ENABLE", resource_type="dnssec_key", resource_name="bf.example", status="success"),
        AuditLog(action="ACME_PRESENT", resource_type="acme", resource_name="www.bf.example",
                 details={"zone": "bf.example."}, status="success"),
        AuditLog(action="LOGIN", resource_type="user", resource_name="bob", details={"ip": "198.51.100.1"},
                 status="success"),
    ]
    ids = _run(_add(*rows))
    _run(_exec("UPDATE audit_logs SET zone_name = NULL WHERE id IN (" + ",".join(map(str, ids)) + ")"))
    _run(_exec("DELETE FROM system_settings WHERE `key` = :k", {"k": BACKFILL_MARKER}))
    _run(backfill_audit_zone_names())
    got = dict(_run(_q("SELECT id, zone_name FROM audit_logs WHERE id IN (" + ",".join(map(str, ids)) + ")")))
    assert [got[i] for i in ids[:5]] == ["bf.example."] * 5
    assert got[ids[5]] is None
    assert _run(_q("SELECT `value` FROM system_settings WHERE `key` = :k", {"k": BACKFILL_MARKER}))
    # zweiter Lauf: Marker vorhanden -> keine Aenderung
    _run(_exec("UPDATE audit_logs SET zone_name = NULL WHERE id = :i", {"i": ids[0]}))
    _run(backfill_audit_zone_names())
    assert _run(_q("SELECT zone_name FROM audit_logs WHERE id = :i", {"i": ids[0]}))[0][0] is None


# --- Daten fuer Nr. 35/36 -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def seeded(fresh_db):
    from app.core.timeutil import utcnow
    from app.models.models import AuditLog, User, UserZoneAccess

    now = utcnow()
    admin = User(username="f7-admin", hashed_password="x", role="admin", is_active=True)
    alice = User(username="f7-alice", hashed_password="x", role="user", is_active=True)
    bob = User(username="f7-bob", hashed_password="x", role="user", is_active=True)
    carol = User(username="f7-carol", hashed_password="x", role="user", is_active=True)
    admin_id, alice_id, bob_id, carol_id = _run(_add(admin, alice, bob, carol))
    _run(_add(UserZoneAccess(user_id=alice_id, zone_name=Z, permission="read")))
    _run(_exec("DELETE FROM users WHERE id = :i", {"i": carol_id}))  # geloeschter Akteur

    www_change = {"name": f"www.{Z}", "type": "A", "before": _snap("192.0.2.1"), "after": _snap("192.0.2.2")}
    e0, = _run(_add(AuditLog(timestamp=now - timedelta(days=5), action="UPDATE", resource_type="record",
                             resource_name=f"www.{Z}", server_name="ns1", details=_v2([www_change]),
                             status="success", user_id=admin_id, zone_name=Z)))
    e5, = _run(_add(AuditLog(timestamp=now - timedelta(days=4), action="DELETE", resource_type="zone",
                             resource_name=Z, server_name="ns1", details={"zone_still_on_other_server": False},
                             status="success", user_id=admin_id, zone_name=Z)))
    e1, = _run(_add(AuditLog(
        timestamp=now - timedelta(hours=3), action="BULK_UPDATE", resource_type="record", resource_name=Z,
        server_name="ns1", status="success", user_id=admin_id, zone_name=Z,
        details=_v2([{"name": f"www.{Z}", "type": "A", "before": None, "after": _snap("192.0.2.10")},
                     {"name": f"mail.{Z}", "type": "MX", "before": None, "after": _snap(f"10 mx.{Z}")}],
                    created=2, deleted=0))))
    e2, = _run(_add(AuditLog(timestamp=now - timedelta(hours=2), action="UPDATE", resource_type="record",
                             resource_name=f"www.{Z}", server_name="ns1", details=_v2([www_change]),
                             status="success", user_id=carol_id, zone_name=Z)))
    rollback_change = {**www_change, "before": www_change["after"], "after": www_change["before"]}
    e3, = _run(_add(AuditLog(timestamp=now - timedelta(hours=1), action="RECORD_ROLLBACK", resource_type="record",
                             resource_name=f"www.{Z}", server_name="ns1",
                             details=_v2([rollback_change], revert_of=e2, forced=False, skipped=[]),
                             status="success", user_id=admin_id, zone_name=Z, revert_of_id=e2)))
    e4, = _run(_add(AuditLog(timestamp=now - timedelta(minutes=30), action="CREATE", resource_type="record",
                             resource_name=f"txt.{Z}", server_name="ns1",
                             details={"zone": Z, "type": "TXT", "records": ['"v=spf1 -all"']},
                             status="success", user_id=alice_id, zone_name=Z)))
    e6, = _run(_add(AuditLog(timestamp=now - timedelta(minutes=20), action="CREATE", resource_type="record",
                             resource_name=f"www.{Z2}", server_name="ns2", details={"zone": Z2, "type": "A"},
                             status="error", error_message="kaputt", user_id=bob_id, zone_name=Z2)))
    return dict(now=now, admin=admin_id, alice=alice_id, bob=bob_id, carol=carol_id,
                e0=e0, e1=e1, e2=e2, e3=e3, e4=e4, e5=e5, e6=e6)


def _client(user_id, role, username, *, session=False):
    from app.core.auth import get_current_user, get_session_user
    from app.main import app
    from app.models.models import User

    user = User(id=user_id, username=username, role=role, is_active=True, hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    if session:
        app.dependency_overrides[get_session_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def overrides(monkeypatch):
    from app.main import app
    from app.services.pdns_client import pdns_manager

    monkeypatch.setattr(pdns_manager, "clients", {"ns1": object()})  # ns1 gilt als geladen und schreibbar
    yield
    app.dependency_overrides.clear()


# --- Nr. 35 ------------------------------------------------------------------------------------------------------
def test_zone_history_filters_and_acl(seeded, overrides):
    s = seeded
    base = f"/api/v1/zones/ns1/{Z}/history"
    c = _client(s["admin"], "admin", "f7-admin")
    r = c.get(base)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 6 and [e["id"] for e in body["entries"]] == [s["e4"], s["e3"], s["e2"], s["e1"],
                                                                        s["e5"], s["e0"]]
    by_id = {e["id"]: e for e in body["entries"]}
    assert by_id[s["e2"]]["reverted_by_id"] == s["e3"] and by_id[s["e2"]]["username"] is None
    assert by_id[s["e1"]]["username"] == "f7-admin" and by_id[s["e1"]]["can_rollback"] is True
    assert by_id[s["e0"]]["before_recreate"] is True and by_id[s["e0"]]["rollback_blocked_reason"] == "zone_recreated"
    assert by_id[s["e4"]]["version"] == 1 and by_id[s["e4"]]["rollback_blocked_reason"] == "legacy_format"
    assert {a["user_id"] for a in body["actors"]} == {s["admin"], s["carol"], s["alice"]}

    def ids(**params):
        resp = c.get(base, params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()["total"], [e["id"] for e in resp.json()["entries"]]

    assert ids(type="MX") == (1, [s["e1"]])                          # JSON_CONTAINS ueber changes[*].type
    assert ids(type="txt") == (1, [s["e4"]])                         # v1-Feld details.type
    assert ids(name=f"mail.{Z}") == (1, [s["e1"]])
    assert ids(name=f"WWW.{Z}".rstrip("."))[0] == 4                 # e0, e1, e2, e3
    assert ids(q="192.0.2.10") == (1, [s["e1"]])
    assert ids(action="update,record_rollback")[1] == [s["e3"], s["e2"], s["e0"]]
    assert ids(status="success", user_id=s["alice"]) == (1, [s["e4"]])
    assert ids(limit=2, offset=0) == (6, [s["e4"], s["e3"]])
    assert ids(limit=2, offset=2) == (6, [s["e2"], s["e1"]])
    since = (s["now"] - timedelta(hours=2, minutes=30)).isoformat() + "Z"
    assert ids(date_from=since)[1] == [s["e4"], s["e3"], s["e2"]]
    assert c.get(base, params={"date_from": since, "date_to": "2000-01-01T00:00:00Z"}).status_code == 400

    entry = c.get(f"{base}/{s['e1']}").json()
    assert entry["id"] == s["e1"] and len(entry["changes"]) == 2
    assert c.get(f"/api/v1/zones/ns1/{Z2}/history/{s['e1']}").status_code == 404  # Eintrag fremder Zone

    # Nicht-Admin ohne Zonenrecht
    c = _client(s["bob"], "user", "f7-bob")
    assert c.get(base).status_code == 403
    # Lese-Nutzer: sieht nur Eintraege nach der letzten endgueltigen Zonenloeschung, kein Zuruecksetzen
    c = _client(s["alice"], "user", "f7-alice")
    r = c.get(base)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4 and [e["id"] for e in body["entries"]] == [s["e4"], s["e3"], s["e2"], s["e1"]]
    e2 = next(e for e in body["entries"] if e["id"] == s["e2"])
    assert e2["can_rollback"] is False and e2["rollback_blocked_reason"] == "no_write_permission"
    assert all(e["client_ip"] is None for e in body["entries"])
    assert {a["user_id"] for a in body["actors"]} == {s["admin"], s["carol"], s["alice"]}
    assert c.get(f"{base}/{s['e0']}").status_code == 404
    assert c.get(f"{base}/{s['e2']}/rollback-preview").status_code == 403  # Vorschau braucht Schreibrecht


# --- Nr. 36 ------------------------------------------------------------------------------------------------------
def test_admin_audit_log_filters_total_and_csv(seeded, overrides):
    s = seeded
    c = _client(s["admin"], "admin", "f7-admin")

    def get(**params):
        resp = c.get("/api/v1/audit-log", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    out = get(zone="HIST.example")
    assert out["total"] == 6 and out["count"] == 6
    assert get(zone=Z, user_id=s["alice"])["total"] == 1
    err = get(status="error", zone=Z2)
    assert err["total"] == 1 and err["entries"][0]["id"] == s["e6"] and err["entries"][0]["username"] == "f7-bob"
    since = (s["now"] - timedelta(hours=2, minutes=30)).isoformat() + "Z"
    assert [e["id"] for e in get(zone=Z, date_from=since)["entries"]] == [s["e4"], s["e3"], s["e2"]]
    assert {e["id"] for e in get(q="hist2")["entries"]} >= {s["e6"]}   # q trifft zone_name
    page = get(zone=Z, limit=2, offset=2)
    assert page["total"] == 6 and page["count"] == 2
    by_id = {e["id"]: e for e in out["entries"]}
    assert by_id[s["e2"]]["username"] is None and by_id[s["e2"]]["reverted_by_id"] == s["e3"]
    assert by_id[s["e1"]]["username"] == "f7-admin" and by_id[s["e3"]]["revert_of_id"] == s["e2"]
    assert c.get(f"/api/v1/audit-log/{s['e1']}").json()["details"]["changes"][1]["type"] == "MX"
    assert c.get("/api/v1/audit-log/999999").status_code == 404

    r = c.get("/api/v1/audit-log/export", params={"zone": Z})
    assert r.status_code == 200
    table = list(csv.reader(io.StringIO(r.content.decode("utf-8").lstrip("﻿")), delimiter=";"))
    assert table[0][-3:] == ["zone_name", "username", "revert_of_id"]
    rows = table[1:]
    assert len(rows) == 6 and all(row[10] == Z for row in rows)
    rollback_row = next(row for row in rows if row[0] == str(s["e3"]))
    assert rollback_row[11] == "f7-admin" and rollback_row[12] == str(s["e2"])

    # Aufbewahrung (Admin-Session)
    c = _client(s["admin"], "admin", "f7-admin", session=True)
    assert c.put("/api/v1/audit-log/settings", json={"retention_days": 3}).status_code == 422
    r = c.put("/api/v1/audit-log/settings", json={"retention_days": 30})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["retention_days"] == 30 and r.json()["settings"]["total_entries"] >= 7
    settings = c.get("/api/v1/audit-log/settings").json()
    assert settings["retention_days"] == 30 and settings["worker_enabled"] is False
    audit_rows = _run(_q("SELECT details FROM audit_logs WHERE action = 'AUDIT_SETTINGS_UPDATE' ORDER BY id DESC"))
    assert audit_rows and '"to": 30' in str(audit_rows[0][0]).replace("'", '"')
    assert c.put("/api/v1/audit-log/settings", json={"retention_days": 0}).status_code == 200


# --- Nr. 37 ------------------------------------------------------------------------------------------------------
def test_purge_expired_audit_logs_batches(fresh_db, monkeypatch):
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import AuditLog
    from app.services import audit as audit_mod
    from app.services.system_settings import get_settings, set_setting

    monkeypatch.setattr(audit_mod, "PURGE_BATCH", 2)
    now = utcnow()

    async def setup(days):
        async with async_session() as db:
            await set_setting(db, audit_mod.RETENTION_KEY, days)
            await db.commit()

    old = [AuditLog(timestamp=now - timedelta(days=40), action="F7PURGE", resource_type="record", status="success")
           for _ in range(3)]
    young = AuditLog(timestamp=now - timedelta(days=10), action="F7PURGE", resource_type="record", status="success")
    _run(_add(*old, young))
    _run(setup("30"))
    deleted = _run(audit_mod.purge_expired_audit_logs(now=now))
    assert deleted == 3
    left = _run(_q("SELECT COUNT(*) FROM audit_logs WHERE action = 'F7PURGE'"))[0][0]
    assert left == 1

    async def stats():
        async with async_session() as db:
            purge = (await db.execute(select(AuditLog).where(AuditLog.action == "AUDIT_PURGE")
                                      .order_by(AuditLog.id.desc()))).scalars().first()
            return purge, await get_settings(db, [audit_mod.LAST_PURGE_AT_KEY, audit_mod.LAST_PURGE_DELETED_KEY])

    purge, st = _run(stats())
    assert purge is not None and purge.details["deleted"] == 3 and purge.details["retention_days"] == 30
    assert st[audit_mod.LAST_PURGE_DELETED_KEY] == "3" and st[audit_mod.LAST_PURGE_AT_KEY]
    _run(setup("0"))
    assert _run(audit_mod.purge_expired_audit_logs(now=now)) == 0
    assert _run(_q("SELECT COUNT(*) FROM audit_logs WHERE action = 'F7PURGE'"))[0][0] == 1
