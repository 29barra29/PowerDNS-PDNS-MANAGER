"""Record-Historie: Audit-Details v2, Vorher/Nachher-Erfassung, Rollback-Planung (F7 5.2, Bauplan B.6/B.7).

Gemeinsamer Baustein fuer alle Record-Schreiber (Einzel-Endpoints und Bulk in ``routers/records.py``, Rollback in
``routers/history.py``, spaeter DynDNS/PTR aus F9/F11 und der Bulk-Umbau aus F1). Snapshots und Kanonisierung kommen
ausschliesslich aus ``services/rrsets.py``; hier liegt nur, was die Historie darauf aufbaut.

Audit-Details v2 (Vertrag F7 4.3, Bauplan B.7)::

    {"version": 2, "zone": "example.com.", "changes": [{"name", "type", "before": Snapshot|None, "after": Snapshot|None}],
     "after_source": "reread"|"computed"|None, "fanout": {...}, "primary_outcome": "ok"|...,
     optional "history_incomplete", "history_truncated", "change_count", "change_keys", Legacy-Felder (v1) ...}

Snapshot = ``{"ttl": int, "records": [{"content", "disabled"}] (sortiert), "comments": [...]}`` – ``None`` heisst
"RRset existiert (danach) nicht".

Typischer Ablauf in einem schreibenden Handler (Betriebsart B des Fan-outs)::

    capture = PrimaryCapture(server_name, [rr_key(name, rtype)])
    fan = await fanout.apply_rrsets(db, server_name, zone_id, build=capture.wrap(builder), targets=..., info=...)
    change = await describe_change(fan, capture, primary_client, zone_id, zone_norm, legacy={...})
    # change.details -> write_audit(details=...), change.changes -> Webhook (webhook_changes), change.before -> PTR (F11)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional

from fastapi import HTTPException
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import normalize_rr_name, normalize_zone_name
from app.core.timeutil import iso_utc, to_naive_utc
from app.models.models import AuditLog, User
from app.services.audit import ZONE_SCOPED_RESOURCE_TYPES, public_details, public_error_message
from app.services.rrsets import norm_name, rrset_snapshot, snapshot_fingerprint
from app.services.webhook_events import MAX_CHANGES_IN_PAYLOAD, compact_rrset

logger = logging.getLogger(__name__)

__all__ = [
    "HISTORY_VERSION", "MAX_DETAILS_BYTES", "LIST_MAX_CHANGES", "DNSSEC_TYPES", "ACME_LABEL",
    "ZONE_SCOPED_RESOURCE_TYPES", "BLOCK_MESSAGES", "ROLLBACK_CONFLICT_MESSAGE", "RRKey",
    "rr_key", "snapshot_rrset", "snapshot_many", "computed_after", "simulate_patch", "snapshots_equivalent",
    "build_changes", "history_details", "exclusion_reason", "rollback_block_reason", "RollbackItem",
    "plan_rollback", "inverse_rrsets", "read_rrsets", "capture_after", "PrimaryCapture", "RecordChange",
    "describe_change", "webhook_changes", "serialize_history_entry", "serialize_audit_entry", "public_details",
    "load_reverted_by", "load_usernames", "last_final_zone_delete_id", "build_audit_filters", "parse_actions",
    "PUBLIC_SEARCH_PATHS",
]

HISTORY_VERSION = 2
MAX_DETAILS_BYTES = 4_000_000
LIST_MAX_CHANGES = 20
CHANGE_KEYS_LIMIT = 500
MAX_ACTION_FILTERS = 20
# Mehr betroffene RRsets -> eine GET /zones/{id} statt einzelner gefilterter Lesezugriffe
READ_ZONE_THRESHOLD = 20
READ_TIMEOUT = 10.0
DNSSEC_TYPES = frozenset({"DNSKEY", "CDNSKEY", "CDS", "DS", "RRSIG", "NSEC", "NSEC3", "NSEC3PARAM"})
ACME_LABEL = "_acme-challenge"
AFTER_REREAD = "reread"
AFTER_COMPUTED = "computed"

RRKey = tuple[str, str]  # (Name lower + Punkt, TYPE)
ExclusionReason = Literal["soa", "dnssec", "acme_challenge"]

BLOCK_MESSAGES = {
    "legacy_format": ("Dieser Eintrag stammt aus einer Version vor 3.0 und enthält keinen vollständigen "
                      "Vorher-Zustand – Zurücksetzen ist nicht möglich."),
    "failed_action": "Die protokollierte Aktion ist fehlgeschlagen und hat nichts verändert.",
    "not_record_change": "Nur Record-Änderungen können zurückgesetzt werden.",
    "incomplete": ("Der Vorher-Zustand konnte damals nicht vollständig erfasst werden – Zurücksetzen ist "
                   "nicht möglich."),
    "no_changes": "Diese Aktion hat keine Records verändert.",
    "only_excluded_records": ("Der Eintrag betrifft nur SOA-, DNSSEC- oder _acme-challenge-Records; diese werden "
                              "nicht zurückgesetzt."),
    "zone_recreated": ("Die Zone wurde nach dieser Änderung endgültig gelöscht und neu angelegt – Zurücksetzen "
                       "ist nicht möglich."),
    "dyndns_repair": ("Dieser Eintrag ist eine DynDNS-Reparatur: ein veralteter Server wurde auf den Stand der "
                      "anderen gebracht. Zurücksetzen würde den Server wieder abweichen lassen und ist nicht möglich."),
    "no_write_permission": "Nur Lese-Zugriff auf diese Zone",
    "server_read_only": "Der Server ist auf 'Speichern: Nein' gesetzt",
}
ROLLBACK_CONFLICT_MESSAGE = ("Die Zone wurde seit dieser Änderung erneut geändert. Zurücksetzen würde neuere "
                             "Änderungen überschreiben – mit force=true trotzdem ausführen.")


# =============================================================================
# Snapshots und Vergleich
# =============================================================================
def rr_key(name: str, rtype: str) -> RRKey:
    """Schluessel eines RRsets: (Name lower + Punkt, TYPE)."""
    return (norm_name(name), (rtype or "").strip().upper())


def snapshot_rrset(zone: Any, name: str, rtype: str) -> Optional[dict]:
    """Snapshot eines RRsets (``rrsets.rrset_snapshot``; ``zone`` = Zonen-JSON oder Liste von RRsets)."""
    return rrset_snapshot(zone, name, rtype)


def snapshot_many(zone: Any, keys: Iterable[RRKey]) -> dict[RRKey, Optional[dict]]:
    return {k: rrset_snapshot(zone, k[0], k[1]) for k in keys}


def _sorted_records(records: Iterable[Any]) -> list[dict]:
    out = []
    for r in records or []:
        if isinstance(r, dict):
            out.append({"content": str(r.get("content", "")), "disabled": bool(r.get("disabled", False))})
        else:
            out.append({"content": str(getattr(r, "content", r)), "disabled": bool(getattr(r, "disabled", False))})
    out.sort(key=lambda r: (r["content"], r["disabled"]))
    return out


def computed_after(before: Optional[dict], rrset_payload: dict) -> Optional[dict]:
    """Erwarteter Zustand nach einem PATCH-Eintrag (Fallback, wenn der Primary nicht erneut lesbar ist).

    DELETE oder leere ``records`` -> ``None``. ``comments`` aus dem Payload, wenn der Schluessel gesendet wurde,
    sonst die Kommentare von ``before`` (PowerDNS laesst sie dann unveraendert).
    """
    if str(rrset_payload.get("changetype", "REPLACE")).upper() == "DELETE":
        return None
    records = _sorted_records(rrset_payload.get("records") or [])
    if not records:
        return None
    ttl = rrset_payload.get("ttl")
    if ttl is None:
        ttl = (before or {}).get("ttl") or 0
    if "comments" in rrset_payload:
        comments = list(rrset_payload.get("comments") or [])
    else:
        comments = list((before or {}).get("comments") or [])
    return {"ttl": int(ttl), "records": records, "comments": comments}


def simulate_patch(
    before_map: dict[RRKey, Optional[dict]],
    *,
    value_deletes: Iterable[tuple[RRKey, str]] = (),
    rrsets: Iterable[dict] = (),
) -> dict[RRKey, Optional[dict]]:
    """Fallback-Berechnung des Nachher-Zustands: erst Wert-Loeschungen, dann RRset-Payloads in Reihenfolge."""
    state: dict[RRKey, Optional[dict]] = dict(before_map)
    for key, content in value_deletes:
        snap = state.get(key)
        if snap is None:
            continue
        remaining = [r for r in snap.get("records") or [] if r.get("content") != content]
        state[key] = {**snap, "records": remaining} if remaining else None
    for payload in rrsets:
        key = rr_key(payload.get("name", ""), payload.get("type", ""))
        state[key] = computed_after(state.get(key), payload)
    return state


def _comment_key(snap: Optional[dict]) -> list[tuple[str, str]]:
    return sorted(
        (str(c.get("content", "")), str(c.get("account", "")))
        for c in (snap or {}).get("comments") or []
        if isinstance(c, dict)
    )


def snapshots_equivalent(a: Optional[dict], b: Optional[dict], rtype: str, *, include_comments: bool,
                         origin: str = "") -> bool:
    """Gleich, wenn TTL und die Menge (kanonischer Inhalt, disabled) uebereinstimmen.

    Kanonisierung ueber ``rrsets.snapshot_fingerprint`` (AAAA komprimiert, Namens-Typen case-insensitiv, TXT
    case-sensitiv). Mit ``include_comments`` muessen zusaetzlich die Kommentare (content, account) gleich sein.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if snapshot_fingerprint(a, rtype, origin) != snapshot_fingerprint(b, rtype, origin):
        return False
    if include_comments and _comment_key(a) != _comment_key(b):
        return False
    return True


def build_changes(before_map: dict[RRKey, Optional[dict]], after_map: dict[RRKey, Optional[dict]],
                  *, origin: str = "") -> list[dict]:
    """``changes`` (sortiert nach Name/Typ), nur wirksame Aenderungen (No-ops fallen weg)."""
    out = []
    for key in sorted(set(before_map) | set(after_map)):
        b = before_map.get(key)
        a = after_map.get(key)
        if snapshots_equivalent(b, a, key[1], include_comments=True, origin=origin):
            continue
        out.append({"name": key[0], "type": key[1], "before": b, "after": a})
    return out


def _details_size(details: dict) -> int:
    return len(json.dumps(details, ensure_ascii=True, default=str))


def history_details(
    zone: str,
    changes: list[dict],
    *,
    fanout: Optional[dict],
    after_source: Optional[str],
    legacy: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Audit-Details v2 (F7 4.3). ``extra`` wird gemerged, ``legacy`` (v1-Felder) nur fuer freie Schluessel.

    Groesser als ``MAX_DETAILS_BYTES`` -> ``changes=[]``, ``history_truncated``, ``change_count``, ``change_keys``
    (erste 500) – solche Eintraege sind nicht ruecksetzbar.
    """
    details: dict[str, Any] = {
        "version": HISTORY_VERSION,
        "zone": normalize_zone_name(zone),
        "changes": list(changes or []),
        "after_source": after_source,
        "fanout": fanout,
    }
    for k, v in (extra or {}).items():
        details[k] = v
    for k, v in (legacy or {}).items():
        details.setdefault(k, v)
    if details["changes"] and _details_size(details) > MAX_DETAILS_BYTES:
        all_changes = details["changes"]
        details["changes"] = []
        details["history_truncated"] = True
        details["change_count"] = len(all_changes)
        details["change_keys"] = [{"name": c.get("name"), "type": c.get("type")} for c in all_changes[:CHANGE_KEYS_LIMIT]]
    return details


# =============================================================================
# Rollback: Sperrgruende, Plan, Payload
# =============================================================================
def exclusion_reason(name: str, rtype: str) -> Optional[ExclusionReason]:
    """SOA, DNSSEC-Typen (inkl. DS/CDS/CDNSKEY) und alles unter ``_acme-challenge`` werden nie zurueckgesetzt."""
    t = (rtype or "").strip().upper()
    if t == "SOA":
        return "soa"
    if t in DNSSEC_TYPES:
        return "dnssec"
    first = norm_name(name).split(".", 1)[0]
    if first == ACME_LABEL:
        return "acme_challenge"
    return None


def _details_of(log: Any) -> dict:
    det = getattr(log, "details", None)
    return det if isinstance(det, dict) else {}


def rollback_block_reason(log: Any, *, recreated_cutoff: Optional[int] = None) -> Optional[str]:
    """Eintragsbezogener Sperrgrund (Codes in ``BLOCK_MESSAGES``) oder ``None``.

    Reihenfolge: failed_action, not_record_change, zone_recreated (Eintrag liegt vor der letzten endgueltigen
    Zonenloeschung, [D12]), dyndns_repair (``DYNDNS_UPDATE`` mit ``details.repair``: Angleichen eines veralteten
    Servers; ein Rollback wuerde ihn wieder abweichen lassen), legacy_format, incomplete, no_changes,
    only_excluded_records.
    """
    details = _details_of(log)
    if getattr(log, "status", None) != "success":
        return "failed_action"
    if getattr(log, "resource_type", None) != "record":
        return "not_record_change"
    if recreated_cutoff is not None and (getattr(log, "id", None) or 0) <= recreated_cutoff:
        return "zone_recreated"
    if getattr(log, "action", None) == "DYNDNS_UPDATE" and details.get("repair") is True:
        return "dyndns_repair"
    if details.get("version") != HISTORY_VERSION:
        return "legacy_format"
    if details.get("history_incomplete") or details.get("history_truncated"):
        return "incomplete"
    changes = details.get("changes") or []
    if not isinstance(changes, list) or not changes:
        return "no_changes"
    if all(exclusion_reason(c.get("name", ""), c.get("type", "")) for c in changes if isinstance(c, dict)):
        return "only_excluded_records"
    return None


@dataclass
class RollbackItem:
    name: str
    type: str
    changetype: Literal["REPLACE", "DELETE"]
    current: Optional[dict]   # Zustand jetzt auf dem Primary
    expected: Optional[dict]  # change.after (Stand direkt nach der Aenderung)
    target: Optional[dict]    # change.before (Ziel des Rollbacks)
    conflict: bool
    noop: bool

    @property
    def key(self) -> RRKey:
        return (self.name, self.type)

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "changetype": self.changetype, "current": self.current,
                "expected": self.expected, "target": self.target, "conflict": self.conflict, "noop": self.noop}

    def conflict_info(self) -> dict:
        return {"name": self.name, "type": self.type, "expected": self.expected, "current": self.current}


def plan_rollback(log: Any, current_zone: Any, *, origin: Optional[str] = None) -> tuple[list[RollbackItem], list[dict]]:
    """Plan fuer das Zuruecksetzen eines v2-Eintrags gegen den aktuellen Zustand des Primary.

    ``current_zone``: Zonen-JSON oder Liste von RRsets. Noop = aktueller Zustand entspricht bereits dem
    Vorher-Zustand (wird nicht geschrieben). Konflikt = aktueller Zustand weicht vom Stand direkt nach der Aenderung
    ab **und** ist kein Noop (Kommentare zaehlen jeweils nicht): Ein RRset, das schon auf dem Ziel steht, verlangt
    weder ``force`` noch erscheint es als Konflikt im Audit (L10).
    Rueckgabe ``(plan, skipped)``; ``skipped`` = ``[{"name", "type", "reason"}]`` (SOA/DNSSEC/ACME).
    """
    details = _details_of(log)
    zone = origin if origin is not None else (details.get("zone") or getattr(log, "zone_name", "") or "")
    plan: list[RollbackItem] = []
    skipped: list[dict] = []
    for change in details.get("changes") or []:
        if not isinstance(change, dict):
            continue
        name, rtype = rr_key(change.get("name", ""), change.get("type", ""))
        reason = exclusion_reason(name, rtype)
        if reason:
            skipped.append({"name": name, "type": rtype, "reason": reason})
            continue
        current = rrset_snapshot(current_zone, name, rtype)
        expected = change.get("after")
        target = change.get("before")
        noop = snapshots_equivalent(current, target, rtype, include_comments=False, origin=zone)
        plan.append(RollbackItem(
            name=name, type=rtype,
            changetype="DELETE" if target is None else "REPLACE",
            current=current, expected=expected, target=target,
            conflict=not noop and not snapshots_equivalent(current, expected, rtype, include_comments=False,
                                                           origin=zone),
            noop=noop,
        ))
    return plan, skipped


def inverse_rrsets(items: Iterable[RollbackItem]) -> list[dict]:
    """PATCH-Payload (Betriebsart A) fuer die Plan-Eintraege.

    DELETE ohne weitere Felder; REPLACE mit TTL und Records des Vorher-Zustands. ``comments`` nur, wenn das RRset
    neu angelegt wird (``current is None``) und der Vorher-Zustand Kommentare hatte – sonst bleiben die
    aktuellen Kommentare unangetastet.
    """
    out = []
    for item in items:
        if item.target is None:
            out.append({"name": item.name, "type": item.type, "changetype": "DELETE"})
            continue
        rr = {
            "name": item.name,
            "type": item.type,
            "ttl": int(item.target.get("ttl") or 0),
            "changetype": "REPLACE",
            "records": [{"content": str(r.get("content", "")), "disabled": bool(r.get("disabled", False))}
                        for r in item.target.get("records") or []],
        }
        if item.current is None and item.target.get("comments"):
            rr["comments"] = list(item.target["comments"])
        out.append(rr)
    return out


# =============================================================================
# Lesen am Primary
# =============================================================================
async def read_rrsets(client: Any, zone_id: str, keys: list[RRKey], *, timeout: float = READ_TIMEOUT) -> Any:
    """Aktueller Zustand der RRsets ``keys`` (Liste von RRsets bzw. Zonen-JSON fuer ``rrset_snapshot``).

    Bis ``READ_ZONE_THRESHOLD`` Schluessel gefilterte Lesezugriffe je RRset (``get_rrsets``), darueber eine
    ``get_zone``. Fehler werden nicht abgefangen.
    """
    if len(keys) > READ_ZONE_THRESHOLD:
        return await client.get_zone(zone_id, timeout=max(timeout, 30.0))
    out: list[dict] = []
    for name, rtype in keys:
        found = await client.get_rrsets(zone_id, name, rtype, timeout=timeout)
        if isinstance(found, list):
            out.extend(found)
    return out


async def capture_after(client: Any, zone_id: str, keys: list[RRKey], *,
                        fallback: dict[RRKey, Optional[dict]]) -> tuple[dict[RRKey, Optional[dict]], str]:
    """Nachher-Zustand vom Primary lesen (``"reread"``); nicht lesbar -> ``fallback`` (``"computed"``)."""
    try:
        current = await read_rrsets(client, zone_id, keys)
        return snapshot_many(current, keys), AFTER_REREAD
    except Exception as exc:  # noqa: BLE001 - PowerDNSAPIError, httpx, ... -> berechneter Zustand
        logger.warning("History: Nachher-Zustand nicht lesbar (%s): %s", zone_id, type(exc).__name__)
        return dict(fallback), AFTER_COMPUTED


class PrimaryCapture:
    """Haelt den Vorher-Zustand des Primary fest, den der Fan-out-Builder (Betriebsart B) ohnehin liest.

    ``wrap(builder)`` liefert einen Builder, der beim Aufruf fuer den Primary dessen Zonen-JSON und die Snapshots
    der ``keys`` speichert und dann den eigentlichen Builder aufruft. Es gibt dadurch keinen zusaetzlichen
    Lesezugriff vor dem Schreiben; ``before`` stammt aus genau dem Stand, auf dem der Primary-Payload beruht.
    """

    def __init__(self, primary: str, keys: Iterable[RRKey]):
        self.primary = primary
        self.keys: list[RRKey] = []
        for k in keys:
            k = rr_key(k[0], k[1])
            if k not in self.keys:
                self.keys.append(k)
        self.before: Optional[dict[RRKey, Optional[dict]]] = None
        self.zone_json: Any = None

    @classmethod
    def from_before(cls, primary: str, before: dict[RRKey, Optional[dict]]) -> "PrimaryCapture":
        """Capture mit bereits bekanntem Vorher-Zustand (Betriebsart A, z. B. Rollback)."""
        cap = cls(primary, list(before))
        cap.before = {rr_key(k[0], k[1]): v for k, v in before.items()}
        return cap

    @property
    def ok(self) -> bool:
        return self.before is not None

    def record(self, zone_json: Any) -> None:
        self.zone_json = zone_json
        self.before = snapshot_many(zone_json, self.keys)

    def wrap(self, build: Callable[[str, dict], Optional[list]]) -> Callable[[str, dict], Optional[list]]:
        def wrapped(server_name: str, zone_json: dict) -> Optional[list]:
            if server_name == self.primary:
                self.record(zone_json)
            return build(server_name, zone_json)

        return wrapped


@dataclass
class RecordChange:
    """Ergebnis von :func:`describe_change` (Audit-Details und Rohdaten fuer Webhooks/PTR)."""

    details: dict
    changes: list[dict] = field(default_factory=list)
    before: dict = field(default_factory=dict)          # {RRKey: Snapshot|None} (Primary, vor dem Schreiben)
    after: dict = field(default_factory=dict)           # {RRKey: Snapshot|None} (nach dem Schreiben)
    after_source: Optional[str] = None
    complete: bool = False                              # before und after erfasst


async def describe_change(
    fan: Any,
    capture: PrimaryCapture,
    client: Any,
    zone_id: str,
    zone: str,
    *,
    legacy: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> RecordChange:
    """Audit-Details v2 nach ``fanout.apply_rrsets`` (Betriebsart B mit ``capture.wrap(builder)``).

    - Primary erfolgreich: ``after`` per Re-Read (Fallback: aus dem gesendeten Payload berechnet); bei
      ``verified_after_timeout`` ist die Quelle immer ``"reread"`` (die Nachpruefung hat den Zustand gelesen) [D3].
    - Primary-Ergebnis unklar (``unknown``): Fehler-Details mit dem tatsaechlich gelesenen Zustand in ``changes``
      und ``history_incomplete`` [D3].
    - sonst (Fehler): ``changes=[]``, ``after_source=None``.
    ``primary_outcome`` steht immer in den Details.
    """
    zone_norm = normalize_zone_name(zone)
    merged_extra = {"primary_outcome": getattr(fan, "primary_outcome", None)}
    merged_extra.update(extra or {})
    summary = fan.summary
    keys = capture.keys
    if fan.primary_success:
        if not capture.ok:
            details = history_details(zone_norm, [], fanout=summary, after_source=None, legacy=legacy,
                                      extra={**merged_extra, "history_incomplete": True})
            return RecordChange(details=details)
        before = dict(capture.before or {})
        payload = list((getattr(fan, "per_server_rrsets", {}) or {}).get(capture.primary) or [])
        if not payload:  # Builder meldete "keine Aenderung noetig"
            details = history_details(zone_norm, [], fanout=summary, after_source=AFTER_COMPUTED, legacy=legacy,
                                      extra=merged_extra)
            return RecordChange(details=details, before=before, after=dict(before), after_source=AFTER_COMPUTED,
                                complete=True)
        fallback = simulate_patch(before, rrsets=payload)
        after, src = await capture_after(client, zone_id, keys, fallback=fallback)
        if getattr(fan, "primary_outcome", None) == "verified_after_timeout":
            src = AFTER_REREAD
        changes = build_changes(before, after, origin=zone_norm)
        details = history_details(zone_norm, changes, fanout=summary, after_source=src, legacy=legacy,
                                  extra=merged_extra)
        return RecordChange(details=details, changes=changes, before=before, after=after, after_source=src,
                            complete=True)

    if getattr(fan, "primary_outcome", None) == "unknown" and capture.ok:
        before = dict(capture.before or {})
        after, src = await capture_after(client, zone_id, keys, fallback={})
        changes = build_changes(before, after, origin=zone_norm) if src == AFTER_REREAD else []
        details = history_details(zone_norm, changes, fanout=summary, after_source=src if changes else None,
                                  legacy=legacy, extra={**merged_extra, "history_incomplete": True})
        return RecordChange(details=details, changes=changes, before=before,
                            after=after if src == AFTER_REREAD else {}, after_source=src if changes else None)

    details = history_details(zone_norm, [], fanout=summary, after_source=None, legacy=legacy, extra=merged_extra)
    return RecordChange(details=details, before=dict(capture.before or {}))


def webhook_changes(changes: list[dict], *, limit: int = MAX_CHANGES_IN_PAYLOAD) -> dict:
    """``changes`` fuer Webhook-Payloads (F6 5.3): ``compact_rrset`` ohne Kommentare, hoechstens ``limit``.

    Rueckgabe ``{"changes": [...]}`` plus ``changes_truncated``/``changes_count`` bei Kuerzung.
    """
    compact = [{"name": c.get("name"), "type": c.get("type"), "before": compact_rrset(c.get("before")),
                "after": compact_rrset(c.get("after"))} for c in changes or []]
    out: dict[str, Any] = {"changes": compact[:limit]}
    if len(compact) > limit:
        out["changes_truncated"] = True
        out["changes_count"] = len(compact)
    return out


# =============================================================================
# Lesen aus der Datenbank
# =============================================================================
async def load_reverted_by(db: AsyncSession, ids: Iterable[int]) -> dict[int, int]:
    """``{audit_id: id des juengsten erfolgreichen RECORD_ROLLBACK auf diesen Eintrag}``."""
    id_list = sorted({int(i) for i in ids if i is not None})
    if not id_list:
        return {}
    rows = (await db.execute(
        select(AuditLog.revert_of_id, func.max(AuditLog.id))
        .where(AuditLog.revert_of_id.in_(id_list), AuditLog.status == "success")
        .group_by(AuditLog.revert_of_id)
    )).all()
    return {int(r[0]): int(r[1]) for r in rows if r[0] is not None and r[1] is not None}


async def load_usernames(db: AsyncSession, ids: Iterable[Optional[int]]) -> dict[int, str]:
    """``{user_id: username}`` fuer existierende Benutzer (geloeschte fehlen)."""
    id_list = sorted({int(i) for i in ids if i is not None})
    if not id_list:
        return {}
    rows = (await db.execute(select(User.id, User.username).where(User.id.in_(id_list)))).all()
    return {int(r[0]): r[1] for r in rows if r[0] is not None}


async def last_final_zone_delete_id(db: AsyncSession, zone: str) -> Optional[int]:
    """ID der letzten erfolgreichen, endgueltigen Zonenloeschung (``zone_still_on_other_server`` nicht wahr).

    Eintraege mit kleinerer oder gleicher ID gehoeren zu einer frueheren Zone gleichen Namens [D12, S12]: fuer
    Nicht-Admins ausgeblendet, nie ruecksetzbar (``zone_recreated``). Fehlt das Feld (sehr alte Eintraege), gilt
    die Loeschung als endgueltig (fail-closed).
    """
    z = normalize_zone_name(zone)
    if not z:
        return None
    rows = (await db.execute(
        select(AuditLog.id, AuditLog.details)
        .where(AuditLog.zone_name == z, AuditLog.resource_type == "zone", AuditLog.action == "DELETE",
               AuditLog.status == "success")
        .order_by(AuditLog.id.desc())
        .limit(50)
    )).all()
    for row in rows:
        det = row[1] if isinstance(row[1], dict) else {}
        if det.get("zone_still_on_other_server") is True:
            continue
        return int(row[0])
    return None


# =============================================================================
# Filter (Zonenverlauf und Admin-Audit-Log)
# =============================================================================
def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_actions(action: Optional[str]) -> list[str]:
    """Komma-Liste -> UPPER, ohne Duplikate; mehr als 20 Werte -> 400."""
    out: list[str] = []
    for part in (action or "").split(","):
        a = part.strip().upper()
        if a and a not in out:
            out.append(a)
    if len(out) > MAX_ACTION_FILTERS:
        raise HTTPException(status_code=400, detail=f"Höchstens {MAX_ACTION_FILTERS} Aktionen im Filter erlaubt")
    return out


# Freitextsuche fuer Nicht-Admins [S12]: nur DNS-Daten, die ``public_details`` ohnehin zeigt (Blattwerte bzw.
# Record-Listen ohne verschachtelte Schluessel aus ``PRIVATE_NESTED_KEYS``). MariaDB-JSON_EXTRACT mit mehreren
# Pfaden liefert ein Array aller Treffer bzw. NULL.
PUBLIC_SEARCH_PATHS = (
    "$.changes[*].name",
    "$.changes[*].type",
    "$.changes[*].before.records[*].content",
    "$.changes[*].after.records[*].content",
    "$.changes[*].before.comments[*].content",
    "$.changes[*].after.comments[*].content",
    # v1 (vor 3.0)
    "$.type",
    "$.records",
    "$.content",
    "$.old",
    "$.new",
)


def build_audit_filters(
    *,
    zone: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    server_name: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Any = None,
    date_to: Any = None,
    q: Optional[str] = None,
    name: Optional[str] = None,
    record_type: Optional[str] = None,
    q_admin_columns: bool = False,
    public_only: bool = False,
) -> list:
    """Gemeinsame WHERE-Bedingungen fuer Zonenverlauf, Audit-Log-Liste und CSV-Export (F7 3.2/3.6).

    ``name``/``record_type`` treffen v1-Felder und ``changes[*]`` (MariaDB ``JSON_CONTAINS``). ``q`` sucht
    case-insensitiv in Ressource, Details und Fehlertext, mit ``q_admin_columns`` zusaetzlich in Aktion,
    Server und Zone. Verkehrter Zeitraum -> 400.

    ``public_only`` (Aufrufer ist kein Admin) [S12]: ``q`` sucht nur in ``resource_name`` und in den
    DNS-Daten unter ``PUBLIC_SEARCH_PATHS`` - nie im ganzen Details-Text und nie im Fehlertext. Sonst
    waere ``total`` ein Ja/Nein-Orakel fuer Werte, die ``public_details``/``public_error_message`` fuer
    Nicht-Admins ausblenden (``auth.token_prefix``, ``client_ip``, PowerDNS-Rohtexte ...).
    ``q_admin_columns`` wird dann ignoriert.
    """
    conds: list = []
    if zone:
        conds.append(AuditLog.zone_name == normalize_zone_name(zone))
    actions = parse_actions(action)
    if actions:
        conds.append(AuditLog.action.in_(actions))
    if resource_type and resource_type.strip():
        conds.append(AuditLog.resource_type == resource_type.strip().lower())
    if server_name and server_name.strip():
        conds.append(AuditLog.server_name == server_name.strip())
    if user_id is not None:
        conds.append(AuditLog.user_id == user_id)
    if status:
        conds.append(AuditLog.status == status)
    df, dt = to_naive_utc(date_from), to_naive_utc(date_to)
    if df and dt and df > dt:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum: 'date_from' liegt nach 'date_to'")
    if df:
        conds.append(AuditLog.timestamp >= df)
    if dt:
        conds.append(AuditLog.timestamp <= dt)
    if name and name.strip():
        n = normalize_rr_name(name)
        conds.append(or_(
            func.lower(AuditLog.resource_name) == n,
            func.lower(AuditLog.resource_name) == n.rstrip("."),
            func.json_contains(func.json_extract(AuditLog.details, "$.changes[*].name"), func.json_quote(n)) == 1,
        ))
    if record_type and record_type.strip():
        t = record_type.strip().upper()
        conds.append(or_(
            func.json_unquote(func.json_extract(AuditLog.details, "$.type")) == t,
            func.json_contains(func.json_extract(AuditLog.details, "$.changes[*].type"), func.json_quote(t)) == 1,
        ))
    if q and q.strip():
        pat = "%" + _escape_like(q.strip().lower()) + "%"
        if public_only:
            public_text = cast(func.json_extract(AuditLog.details, *PUBLIC_SEARCH_PATHS), Text)
            conds.append(or_(
                func.lower(AuditLog.resource_name).like(pat, escape="\\"),
                func.lower(public_text).like(pat, escape="\\"),
            ))
            return conds
        cols = [
            func.lower(AuditLog.resource_name).like(pat, escape="\\"),
            func.lower(cast(AuditLog.details, Text)).like(pat, escape="\\"),
            func.lower(AuditLog.error_message).like(pat, escape="\\"),
        ]
        if q_admin_columns:
            cols += [
                func.lower(AuditLog.action).like(pat, escape="\\"),
                func.lower(AuditLog.server_name).like(pat, escape="\\"),
                func.lower(AuditLog.zone_name).like(pat, escape="\\"),
            ]
        conds.append(or_(*cols))
    return conds


# =============================================================================
# Serialisierung
# =============================================================================
def _change_count(details: dict, changes: list) -> int:
    if details.get("history_truncated") and isinstance(details.get("change_count"), int):
        return int(details["change_count"])
    return len(changes)


def serialize_history_entry(
    log: Any,
    usernames: dict[int, str],
    reverted_by: dict[int, int],
    *,
    user_can_write: bool,
    server_writable: bool,
    max_changes: Optional[int],
    admin: bool,
    recreated_cutoff: Optional[int] = None,
) -> dict:
    """Ein Eintrag des Zonenverlaufs (``schemas.history.HistoryEntry``).

    Nicht-Admins bekommen ``details`` ueber ``public_details`` (Allowlist, ohne Client-IPs und Token-Prefix) und
    einen generischen Fehlertext [S12]; ``client_ip`` nur fuer Admins. ``before_recreate`` markiert Eintraege vor
    der letzten endgueltigen Zonenloeschung (Nicht-Admins sehen sie gar nicht) [D12].
    """
    raw = _details_of(log)
    is_v2 = raw.get("version") == HISTORY_VERSION
    shown = public_details(log.details, admin=admin)
    shown_dict = shown if isinstance(shown, dict) else {}
    all_changes = shown_dict.get("changes") if is_v2 and isinstance(shown_dict.get("changes"), list) else []
    changes: Optional[list] = list(all_changes) if is_v2 else None
    truncated = False
    if changes is not None and max_changes is not None and len(changes) > max_changes:
        changes = changes[:max_changes]
        truncated = True
    details_out: Any
    if isinstance(shown, dict):
        details_out = {k: v for k, v in shown.items() if k != "changes"}
    else:
        details_out = shown
    reason = (rollback_block_reason(log, recreated_cutoff=recreated_cutoff)
              or (None if user_can_write else "no_write_permission")
              or (None if server_writable else "server_read_only"))
    before_recreate = recreated_cutoff is not None and (log.id or 0) <= recreated_cutoff
    return {
        "id": log.id,
        "timestamp": iso_utc(log.timestamp),
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_name": log.resource_name,
        "server_name": log.server_name,
        "zone_name": log.zone_name,
        "user_id": log.user_id,
        "username": usernames.get(log.user_id) if log.user_id is not None else None,
        "actor_username": getattr(log, "actor_username", None),
        "client_ip": getattr(log, "client_ip", None) if admin else None,
        "status": log.status,
        "error_message": log.error_message if admin else public_error_message(log.error_message),
        "version": HISTORY_VERSION if is_v2 else 1,
        "changes": changes,
        "change_count": _change_count(raw, all_changes) if is_v2 else 0,
        "changes_truncated": truncated,
        "details": details_out,
        "revert_of_id": getattr(log, "revert_of_id", None),
        "reverted_by_id": reverted_by.get(log.id),
        "can_rollback": reason is None,
        "rollback_blocked_reason": reason,
        "before_recreate": before_recreate,
    }


def serialize_audit_entry(log: Any, usernames: dict[int, str], reverted_by: dict[int, int], *,
                          full: bool) -> dict:
    """Eintrag des Admin-Audit-Logs (``schemas.history.AuditLogEntry``); ohne ``full`` ``changes`` auf 20 gekuerzt."""
    details = log.details
    truncated = False
    if not full and isinstance(details, dict) and isinstance(details.get("changes"), list) \
            and len(details["changes"]) > LIST_MAX_CHANGES:
        details = {**details, "changes": details["changes"][:LIST_MAX_CHANGES],
                   "change_count": _change_count(details, details["changes"])}
        truncated = True
    return {
        "id": log.id,
        "timestamp": iso_utc(log.timestamp),
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_name": log.resource_name,
        "server_name": log.server_name,
        "zone_name": getattr(log, "zone_name", None),
        "user_id": log.user_id,
        "username": usernames.get(log.user_id) if log.user_id is not None else None,
        "actor_username": getattr(log, "actor_username", None),
        "client_ip": getattr(log, "client_ip", None),
        "details": details,
        "details_truncated": truncated,
        "status": log.status,
        "error_message": log.error_message,
        "revert_of_id": getattr(log, "revert_of_id", None),
        "reverted_by_id": reverted_by.get(log.id),
    }
