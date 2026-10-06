"""WS-W3-NACHARBEIT (Welle 4a): Restpunkte aus den Wellen 2 und 3.

- F15-fix3-Antrag: ``max_length`` fuer ``RecordDelete.content``/``BulkDisabledChange.content``, ``bulk.build_plan``
  normalisiert Werte fehlender RRsets nicht mehr.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fakes.pdns import make_zone, rr
from app.schemas.bulk import BulkDisabledChange, BulkRecordUpdate
from app.schemas.dns import RECORD_CONTENT_MAX_LENGTH, RecordDelete
from app.services import bulk as bulk_service
from app.services import lua_records

Z = "example.com."
SOA = "ns1.example.com. hostmaster.example.com. 1 10800 3600 604800 3600"


# ---------------------------------------------------------------------------------------------
# F15-fix3: Laengengrenzen
# ---------------------------------------------------------------------------------------------
def test_record_content_limit_value():
    # ueber der PowerDNS-Grenze (64000), unter der Normalisierungsgrenze, nicht die LUA-Grenze 4000
    assert RECORD_CONTENT_MAX_LENGTH == 65535
    assert 64000 < RECORD_CONTENT_MAX_LENGTH < lua_records.LUA_NORMALIZE_MAX_LENGTH
    assert RECORD_CONTENT_MAX_LENGTH > lua_records.LUA_MAX_CONTENT_LENGTH


def test_record_delete_content_max_length():
    ok = RecordDelete(name=f"www.{Z}", type="LUA", content="A " + "x" * (RECORD_CONTENT_MAX_LENGTH - 2))
    assert len(ok.content) == RECORD_CONTENT_MAX_LENGTH
    # extern angelegte, laengere LUA-Werte (ueber 4000) bleiben einzeln loeschbar
    assert RecordDelete(name=f"www.{Z}", type="LUA", content='A "' + "y" * 5000 + '"').content
    assert RecordDelete(name=f"www.{Z}", type="A").content is None
    with pytest.raises(ValidationError):
        RecordDelete(name=f"www.{Z}", type="LUA", content="A " + "x" * RECORD_CONTENT_MAX_LENGTH)


def test_bulk_disabled_change_content_max_length():
    BulkDisabledChange(name=f"www.{Z}", type="TXT", content="x" * RECORD_CONTENT_MAX_LENGTH, disabled=True)
    with pytest.raises(ValidationError):
        BulkDisabledChange(name=f"www.{Z}", type="TXT", content="x" * (RECORD_CONTENT_MAX_LENGTH + 1), disabled=True)


def test_bulk_request_rejects_overlong_delete_and_disabled_values():
    big = "x" * (RECORD_CONTENT_MAX_LENGTH + 1)
    with pytest.raises(ValidationError):
        BulkRecordUpdate(delete=[{"name": f"www.{Z}", "type": "TXT", "content": big}])
    with pytest.raises(ValidationError):
        BulkRecordUpdate(set_disabled=[{"name": f"www.{Z}", "type": "TXT", "content": big, "disabled": True}])


# ---------------------------------------------------------------------------------------------
# F15-fix3: ck() erst, wenn es ein RRset gibt
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def ck_calls(monkeypatch):
    calls = []
    real = bulk_service.content_key

    def spy(rtype, content, zone):
        calls.append((rtype, len(content)))
        return real(rtype, content, zone)

    monkeypatch.setattr(bulk_service, "content_key", spy)
    return calls


def _zone(*rrs):
    return make_zone(Z, [rr(Z, "SOA", SOA), *rrs])


def test_delete_value_of_missing_rrset_skips_normalization(ck_calls):
    ops = BulkRecordUpdate(delete=[{"name": f"www.{Z}", "type": "LUA", "content": "A x" + " " * 60_000 + "y"}])
    plan = bulk_service.build_plan(Z, _zone(), ops, strict=True)
    assert [i["code"] for i in plan.issues] == ["value_missing"]
    assert ck_calls == []


def test_set_disabled_of_missing_rrset_skips_normalization(ck_calls):
    ops = BulkRecordUpdate(set_disabled=[{"name": f"www.{Z}", "type": "TXT", "content": '"a"', "disabled": True}])
    plan = bulk_service.build_plan(Z, _zone(), ops, strict=True)
    assert [i["code"] for i in plan.issues] == ["value_missing"]
    assert ck_calls == []


def test_delete_semantics_unchanged_for_existing_and_double_deletes():
    zone = _zone(rr(f"www.{Z}", "A", "192.0.2.1", "192.0.2.2"))
    # letzter Wert zweimal geloescht: das zweite Mal ohne Wirkung (kein value_missing)
    ops = BulkRecordUpdate(delete=[
        {"name": f"www.{Z}", "type": "A", "content": "192.0.2.1"},
        {"name": f"www.{Z}", "type": "A", "content": "192.0.2.2"},
        {"name": f"www.{Z}", "type": "A", "content": "192.0.2.2"},
    ])
    plan = bulk_service.build_plan(Z, zone, ops, strict=True)
    assert plan.issues == []
    assert len(plan.changes) == 1
    # unbekannter Wert eines bestehenden RRsets bleibt ein Problem
    ops = BulkRecordUpdate(delete=[{"name": f"www.{Z}", "type": "A", "content": "192.0.2.9"}])
    plan = bulk_service.build_plan(Z, zone, ops, strict=True)
    assert [i["code"] for i in plan.issues] == ["value_missing"]
    # set_disabled auf bestehenden Wert wirkt
    ops = BulkRecordUpdate(set_disabled=[{"name": f"www.{Z}", "type": "A", "content": "192.0.2.1", "disabled": True}])
    plan = bulk_service.build_plan(Z, zone, ops, strict=True)
    assert plan.issues == [] and len(plan.changes) == 1
