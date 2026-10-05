"""Webhook-Ereignisse: Katalog, Abo-Filter, Abgleich und Payload v2 (F6 5.2, Bauplan B.8).

Rein funktional (keine DB, kein HTTP). ``webhook_outbox.enqueue_event`` baut mit ``build_payload`` je
Empfaenger den Body, der unveraendert gespeichert, signiert und spaeter vom Worker gesendet wird.

Der Body ist ASCII-JSON (``ensure_ascii=True``): unabhaengig vom Zeichensatz der Datenbank byte-identisch
speicher- und wiedersendbar, die Signatur bleibt gueltig.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Optional

from app.core.config import settings

PAYLOAD_VERSION = 2

# Verbindlicher Katalog (B.8). Neue Ereignisse nur hier ergaenzen; der AST-Test
# (tests/test_webhook_wiring.py) prueft, dass jeder enqueue_event-Aufruf ein Katalogereignis sendet.
EVENT_CATALOG: tuple[str, ...] = (
    "record.created",
    "record.updated",
    "record.deleted",
    "record.bulk",
    "record.rollback",
    "record.ptr_synced",
    "zone.created",
    "zone.updated",
    "zone.deleted",
    "zone.imported",
    "dnssec.enabled",
    "dnssec.disabled",
    "dnssec.key_created",
    "dnssec.key_activated",
    "dnssec.key_deactivated",
    "dnssec.key_published",
    "dnssec.key_unpublished",
    "dnssec.key_deleted",
    "dnssec.nsec3_changed",
    "dyndns.updated",
    "webhook.test",
)
# webhook.test ist nicht abonnierbar (geht nur an den getesteten Webhook).
SUBSCRIBABLE_EVENTS: tuple[str, ...] = tuple(e for e in EVENT_CATALOG if not e.startswith("webhook."))
EVENT_CATEGORIES: tuple[str, ...] = ("record", "zone", "dnssec", "dyndns")

MAX_FILTERS = 30
MAX_BODY_BYTES = 512 * 1024
MAX_CHANGES_IN_PAYLOAD = 200


def normalize_event_filters(events: Optional[Iterable[Any]]) -> list[str]:
    """Abo-Filter vereinheitlichen.

    strip + lower, leere entfernen, Duplikate entfernen (Reihenfolge bleibt). Leer -> ``["*"]``;
    enthaelt ``"*"`` -> ``["*"]``. Erlaubt: ``"*"``, Kategorie (``"record"``), ``"<kategorie>.*"`` und
    exakte Namen aus ``SUBSCRIBABLE_EVENTS``. Sonst ``ValueError("Unbekanntes Ereignis: …")``.
    """
    out: list[str] = []
    for raw in events or []:
        e = str(raw if raw is not None else "").strip().lower()
        if not e or e in out:
            continue
        out.append(e)
    if not out or "*" in out:
        return ["*"]
    if len(out) > MAX_FILTERS:
        raise ValueError(f"Höchstens {MAX_FILTERS} Ereignisfilter erlaubt")
    for e in out:
        if e in EVENT_CATEGORIES or e in SUBSCRIBABLE_EVENTS:
            continue
        if e.endswith(".*") and e[:-2] in EVENT_CATEGORIES:
            continue
        raise ValueError(f"Unbekanntes Ereignis: {e}")
    return out


def matches_subscription(subscribed: str, event: str) -> bool:
    """Ein Abo-Eintrag gegen ein Ereignis (Logik aus 2.4.1 unveraendert uebernommen).

    ``"*"``/leer trifft alles; ``"zone.*"`` ist ein Praefix; ``"zone"`` trifft ``zone`` und ``zone.<x>``.
    """
    s = (subscribed or "").strip()
    if not s or s == "*":
        return True
    if s.endswith("*"):
        p = s[:-1]
        return event.startswith(p) or event == p
    return event == s or event.startswith(f"{s}.")


def webhook_wants(events: Optional[list], event: str) -> bool:
    """Will ein Webhook mit diesen Abo-Filtern das Ereignis? (``webhook.test`` nie ueber Abos.)"""
    if event.startswith("webhook."):
        return False
    if not events or events == ["*"]:
        return True
    return any(matches_subscription(str(s), event) for s in events)


def compact_rrset(snapshot: Optional[dict]) -> Optional[dict]:
    """``{"ttl": int, "records": [{"content", "disabled"}]}`` ohne Kommentare; ``None`` bleibt ``None``.

    Nimmt sowohl Snapshots aus ``services/rrsets.rrset_snapshot`` als auch rohe PowerDNS-RRsets.
    """
    if snapshot is None:
        return None
    ttl = snapshot.get("ttl")
    records = []
    for rec in snapshot.get("records") or []:
        if isinstance(rec, dict):
            records.append({"content": str(rec.get("content", "")), "disabled": bool(rec.get("disabled", False))})
        else:
            records.append({"content": str(rec), "disabled": False})
    return {"ttl": int(ttl) if ttl is not None else None, "records": records}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _dumps(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=True, default=_json_default).encode("ascii")


def build_payload(
    *,
    event: str,
    event_id: str,
    delivery_id: str,
    occurred_at: datetime,
    actor_user_id: Optional[int],
    actor_username: Optional[str],
    actor_via: Optional[str],
    zone: Optional[str],
    server: Optional[str],
    audit_log_id: Optional[int],
    data: dict,
) -> bytes:
    """Body v2 als ASCII-JSON-Bytes (F6 5.2). Zu grosse Bodies werden gekuerzt (erst ``changes``, dann ``data``)."""
    data = dict(data or {})
    body = {
        "v": PAYLOAD_VERSION,
        "event": event,
        "event_id": event_id,
        "delivery_id": delivery_id,
        "timestamp": occurred_at.isoformat(),  # Ereigniszeit (UTC, +00:00), nicht Sendezeit
        "app": settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "actor_user_id": actor_user_id,  # v1-Feld bleibt
        "actor": {"user_id": actor_user_id, "username": actor_username, "via": actor_via},
        "zone": zone,
        "server": server,
        "audit_log_id": audit_log_id,
        "data": data,
    }
    raw = _dumps(body)
    if len(raw) > MAX_BODY_BYTES and isinstance(data.get("changes"), list):
        body["data"] = {**data, "changes": [], "changes_truncated": True, "changes_count": len(data["changes"])}
        raw = _dumps(body)
    if len(raw) > MAX_BODY_BYTES:
        body["data"] = {"truncated": True}
        raw = _dumps(body)
    return raw
