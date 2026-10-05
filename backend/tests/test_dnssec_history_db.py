"""DNSSEC-Schluesselverlauf gegen MariaDB (``requires_db``, Fixture ``fresh_db``; F4 9 "key_history").

``dnssec_service.key_history`` liest erfolgreiche ``dnssec_key``-Audits einer Zone auf einem Server (Spalte
``audit_logs.zone_name``) und liefert je Schluessel-ID die neuesten Zeitstempel im ISO-UTC-Format. Geprueft werden
die SQL-Filter (Zone, Server, Status, Ressourcentyp) und die Auswertung gegen echte Zeilen aus ``write_audit``.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import text

from dbutil import requires_db

pytestmark = requires_db


def _run(coro):
    return asyncio.run(coro)


async def _write(rows: list[tuple]) -> None:
    """``rows``: (zeit, aktion, details, server, zone, status, resource_type) – aelteste zuerst."""
    from app.core.database import async_session
    from app.services.audit import write_audit

    async with async_session() as s:
        for ts, action, details, server, zone, status, rtype in rows:
            if status == "success":
                obj = await write_audit(s, action, rtype, zone, user_id=1, details=details, server_name=server,
                                        zone_name=zone)
                await s.flush()
                await s.execute(text("UPDATE audit_logs SET `timestamp` = :t WHERE id = :i"), {"t": ts, "i": obj.id})
            else:
                await write_audit(s, action, rtype, zone, user_id=1, details=details, server_name=server,
                                  zone_name=zone, status="error", error_message="x")
        await s.commit()


def test_key_history_newest_timestamps_per_key(fresh_db):
    from app.core.database import async_session
    from app.services.dnssec_service import key_history

    prefix = "dh" + uuid.uuid4().hex[:6]
    zone = f"{prefix}.example."
    other = f"other-{prefix}.example."
    d = lambda day, hour=12: datetime(2026, 10, day, hour, 0, 0)  # noqa: E731
    k = "dnssec_key"
    _run(_write([
        (datetime(2026, 9, 1), "KEY_ACTIVATE", {"key_id": 1}, "ns1", zone, "success", k),     # vor DISABLE -> weg
        (datetime(2026, 9, 2), "DNSSEC_DISABLE", {"zone": zone}, "ns1", zone, "success", k),
        (d(1, 10), "DNSSEC_ENABLE", {"zone": zone, "keys": [{"key_id": 1, "keytype": "csk"}]}, "ns1", zone,
         "success", k),
        (d(2), "KEY_CREATE", {"zone": zone, "key_id": 2, "active": False, "published": True}, "ns1", zone,
         "success", k),
        (d(2, 13), "KEY_CREATE", {"zone": zone, "key_id": 3, "active": True}, "ns1", zone, "error", k),
        (d(3), "KEY_ACTIVATE", {"key_id": 2}, "ns2", zone, "success", k),                    # anderer Server
        (d(4), "KEY_UPDATE", {"key_id": 2, "before": {"active": False, "published": True},
                              "after": {"active": True, "published": False}}, "ns1", zone, "success", k),
        (d(5), "KEY_DEACTIVATE", {"key_id": 1}, "ns1", zone, "success", k),
        (d(5, 13), "KEY_DEACTIVATE", {"key_id": 2}, "ns1", other, "success", k),            # andere Zone
        (d(6), "KEY_PUBLISH", {"key_id": 2}, "ns1", zone, "success", k),
        (d(7), "UPDATE", {"zone": zone, "type": "SOA", "key_id": 2}, "ns1", zone, "success", "record"),
    ]))

    async def read(z, server):
        async with async_session() as s:
            return await key_history(s, z, server)

    hist = _run(read(zone, "ns1"))
    assert set(hist) == {"1", "2"}
    assert hist["1"] == {
        "created_at": "2026-10-01T10:00:00+00:00", "activated_at": "2026-10-01T10:00:00+00:00",
        "deactivated_at": "2026-10-05T12:00:00+00:00", "published_at": None, "unpublished_at": None,
    }
    assert hist["2"] == {
        "created_at": "2026-10-02T12:00:00+00:00", "activated_at": "2026-10-04T12:00:00+00:00",
        "deactivated_at": None, "published_at": "2026-10-06T12:00:00+00:00",
        "unpublished_at": "2026-10-04T12:00:00+00:00",
    }
    assert _run(read(zone, "ns2")) == {"2": {
        "created_at": None, "activated_at": "2026-10-03T12:00:00+00:00", "deactivated_at": None,
        "published_at": None, "unpublished_at": None}}
    assert _run(read(other, "ns1"))["2"]["deactivated_at"] == "2026-10-05T13:00:00+00:00"
    assert _run(read(f"nix-{prefix}.example.", "ns1")) == {}


def test_key_history_key_delete_closes_older_rows(fresh_db):
    from app.core.database import async_session
    from app.services.dnssec_service import key_history

    zone = f"dd{uuid.uuid4().hex[:6]}.example."
    k = "dnssec_key"
    _run(_write([
        (datetime(2026, 10, 1), "KEY_CREATE", {"key_id": 4, "active": True, "published": True}, "ns1", zone,
         "success", k),
        (datetime(2026, 10, 2), "KEY_DELETE", {"key_id": 4}, "ns1", zone, "success", k),
        (datetime(2026, 10, 3), "KEY_CREATE", {"key_id": 4, "active": False, "published": False}, "ns1", zone,
         "success", k),
    ]))

    async def read():
        async with async_session() as s:
            return await key_history(s, zone, "ns1")

    assert _run(read()) == {"4": {"created_at": "2026-10-03T00:00:00+00:00", "activated_at": None,
                                  "deactivated_at": None, "published_at": None, "unpublished_at": None}}
