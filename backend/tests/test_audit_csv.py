"""CSV-Export des Audit-Logs: Formel-Injection-Schutz fuer ALLE Spalten [S13], neue Spalten am Ende (F7 3.7)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fakes.pdns import FakeDB, FakeResult
from app.models.models import AuditLog, User
from app.routers import search

EVIL = ["=HYPERLINK(\"http://evil\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]


def _row(i, value):
    return AuditLog(
        id=i, timestamp=datetime(2026, 10, 1, 12, 0, i), action=value, resource_type=value, resource_name=value,
        server_name=value, user_id=7, status=value, error_message=value, details={"q": value},
        zone_name=value, revert_of_id=None,
    )


class CsvDB(FakeDB):
    def __init__(self, rows, users):
        super().__init__(execute_handler=self._handle)
        self.rows = rows
        self.users = users

    def _handle(self, stmt):
        desc = getattr(stmt, "column_descriptions", None) or []
        entity = desc[0].get("entity") if desc else None
        if entity is User:
            return FakeResult(self.users)
        if entity is AuditLog:
            return FakeResult(self.rows)
        return FakeResult([])


def _parse(resp):
    raw = resp.body.decode("utf-8")
    assert raw.startswith("﻿")
    return list(csv.reader(io.StringIO(raw[1:]), delimiter=";"))


async def test_every_cell_is_formula_safe():
    rows = [_row(i + 1, v) for i, v in enumerate(EVIL)]
    db = CsvDB(rows, [(7, "=cmd|' /C calc'!A0")])
    resp = await search.export_audit_log_csv(db, action=None, resource_type=None, server_name=None, zone=None,
                                             user_id=None, status_filter=None, date_from=None, date_to=None, q=None,
                                             max_rows=100, admin=None)
    table = _parse(resp)
    header, data = table[0], table[1:]
    assert header[:10] == ["id", "timestamp_utc", "action", "resource_type", "resource_name", "server_name",
                           "user_id", "status", "error_message", "details_json"]
    assert header[10:] == ["zone_name", "username", "revert_of_id"]
    assert len(data) == len(EVIL)
    for line, value in zip(data, EVIL):
        cells = dict(zip(header, line))
        for col in ("action", "resource_type", "resource_name", "server_name", "status", "error_message", "zone_name"):
            assert cells[col] == "'" + value, (col, cells[col])
        assert cells["username"].startswith("'=")
        assert json.loads(cells["details_json"]) == {"q": value}  # beginnt mit "{" -> unveraendert
        assert cells["user_id"] == "7" and cells["revert_of_id"] == ""
        assert cells["timestamp_utc"].endswith("+00:00")


async def test_export_filters_are_applied():
    db = CsvDB([], [])
    await search.export_audit_log_csv(db, action="create,UPDATE", resource_type="record", server_name="ns1",
                                      zone="Example.COM", user_id=3, status_filter="error",
                                      date_from=datetime(2026, 10, 1), date_to=datetime(2026, 10, 2), q="www",
                                      max_rows=5, admin=None)
    stmt = next(s for s in db.executed if getattr(s, "column_descriptions", None)
                and s.column_descriptions[0].get("entity") is AuditLog)
    compiled = stmt.compile()
    values = list(compiled.params.values())
    sql = str(compiled)
    assert "example.com." in values and "record" in values and "ns1" in values and "error" in values
    assert 3 in values and 5 in values and "%www%" in values
    assert "audit_logs.action IN" in sql
    assert "lower(audit_logs.zone_name) LIKE" in sql  # q durchsucht zusaetzlich Aktion/Server/Zone


async def test_plain_values_unchanged():
    row = AuditLog(id=1, timestamp=datetime(2026, 10, 1), action="CREATE", resource_type="record",
                   resource_name="www.example.com.", server_name="ns1", user_id=None, status="success",
                   error_message=None, details=None, zone_name="example.com.", revert_of_id=4)
    table = _parse(await search.export_audit_log_csv(
        CsvDB([row], []), action=None, resource_type=None, server_name=None, zone=None, user_id=None,
        status_filter=None, date_from=None, date_to=None, q=None, max_rows=10, admin=None))
    cells = dict(zip(table[0], table[1]))
    assert cells["action"] == "CREATE" and cells["resource_name"] == "www.example.com."
    assert cells["user_id"] == "" and cells["details_json"] == "" and cells["error_message"] == ""
    assert cells["zone_name"] == "example.com." and cells["revert_of_id"] == "4" and cells["username"] == ""
