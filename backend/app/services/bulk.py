"""Bulk-Editor: Plan-Builder, Vorschau und Anwenden (F1 5.3, Bauplan B.5/B.6a/B.7).

Ablauf von ``POST …/bulk`` (``apply_bulk``):

1. ``static_validate`` (Namen in der Zone, kein SOA-Loeschen, keine DNSSEC-Auto-Typen) und das LUA-Gate fuer
   LUA-Ops, die anlegen/aendern (F15 5.6) – beides **vor** jedem PowerDNS-Zugriff.
2. ``fanout.apply_rrsets`` in **Betriebsart B** (je Server dessen eigener Zonenstand, ein PATCH je Server,
   Primary zuerst, bei Primary-Fehler keine Peer-Writes, Nachpruefung nach Transportfehler [D1, D3]):

   - Builder am Primary: ``expected``-Pruefung (409), strikter Plan (fehlende Werte -> 404, LUA -> 403,
     blockierende Probleme -> 422). Abbruch per ``BulkRejected`` aus dem Builder (``apply_rrsets`` reicht
     Ausnahmen des Primary-Builders durch) – es wird nichts geschrieben. Der Vorher-Zustand fuer Audit/Historie
     stammt aus genau dem Lesezugriff, auf dem der PATCH beruht.
   - Builder an Peers: nicht-strikter Plan aus dem Stand des Peers (fehlende Werte = ``peer_drift``,
     absolute Ops setzen den Primary-Zielzustand, blockierende Probleme -> ``error: …`` nur fuer diesen Peer).
     Gemeinsame Datenbank: der Peer liest den bereits geschriebenen Stand -> ``skipped (no changes needed)``.

3. PTR-Pflege (F11, ``sync_ptr_for_changes`` -> ``ptr.sync_for_changes`` mit den Audit-v2-``changes``),
   Audit ``BULK_UPDATE`` v2 (vollstaendige ``changes``), Webhook ``record.bulk`` (F6 5.3 + ``data.ptr``).

``POST …/bulk/preview`` (``preview_bulk``) baut denselben strikten Plan gegen den Primary, schreibt nichts und
liefert den fertigen Request-Body inklusive ``expected``-Fingerprints (Vorschau == Anwendung).

CPU-lastiges Parsen/Planen der Vorschau laeuft per ``asyncio.to_thread``; der Builder des Fan-outs ist
synchron (Plan B.5) – sein Aufwand ist proportional zu den Ops, nicht zur Zonengroesse.
Keine Record-Inhalte in Log-Ausgaben (nur Zaehler).
"""
from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.schemas.bulk import (
    BULK_MAX_CHANGED_RRSETS,
    BULK_MAX_OPS,
    BULK_MAX_TEXT_LINES,
    BulkExpectation,
    BulkMergeItem,
    BulkPreviewRequest,
    BulkRecordUpdate,
    BulkTextInput,
    count_ops,
)
from app.schemas.dns import (
    ALLOWED_RECORD_TYPES,
    MessageResponse,
    RecordCreate,
    RecordDelete,
    RecordItem,
)
from app.services import fanout
from app.services.audit import write_audit
from app.services.bind_fragment import ParsedFragment, has_errors, issue, parse_bind_fragment
from app.services.pdns_client import PowerDNSAPIError
from app.services.record_history import AFTER_COMPUTED, AFTER_REREAD, history_details, webhook_changes
from app.services.rrsets import (
    DNSSEC_AUTO_TYPES,
    content_key,
    index_rrsets,
    norm_name,
    rrset_snapshot,
    snapshot_fingerprint,
)

logger = logging.getLogger(__name__)

RRKey = tuple[str, str]

DNSKEY_MANAGED_TYPES = frozenset({"DNSKEY", "CDS", "CDNSKEY"})
LUA_WRITE_OP_LISTS = ("create", "merge", "set_ttl", "set_disabled")  # Ops, die LUA-RRsets anlegen/aendern

# PTR-Pflege (F11, Plan B.6a). Die Werte entsprechen ``ptr.PTR_TYPES`` und ``ptr.KEY_AUTO_DEFAULT``; das Modul
# ``services/ptr.py`` wird nur geladen, wenn die PTR-Pflege tatsaechlich laeuft.
PTR_FORWARD_TYPES = frozenset({"A", "AAAA"})
PTR_AUTO_DEFAULT_KEY = "ptr_auto_default"

MSG_EMPTY = "Keine Records zu verarbeiten"
MSG_INVALID = "Die Änderung ist ungültig."
MSG_BLOCKED = "Die Änderung kann so nicht angewendet werden."
MSG_MISSING = "Mindestens ein Wert ist auf dem Server nicht (mehr) vorhanden. Bitte Zone neu laden."
MSG_CONFLICT = "Die Zone wurde seit der Vorschau geändert. Bitte Vorschau neu laden."
MSG_CONFLICT_AUDIT = "Konflikt mit aktuellem Stand"
MSG_NO_CHANGES = "Keine Änderungen nötig"


def _key(name: str, rtype: str) -> RRKey:
    return (norm_name(name), (rtype or "").strip().upper())


def _inside(name: str, zone_norm: str) -> bool:
    n = norm_name(name)
    return n == zone_norm or n.endswith("." + zone_norm)


# =============================================================================
# Plan
# =============================================================================
class _RRState:
    """Arbeitskopie eines RRsets: Werte nach ``content_key`` (Einfuegereihenfolge = Sende-Reihenfolge)."""

    __slots__ = ("ttl", "values")

    def __init__(self, ttl: int, values: dict[str, dict]):
        self.ttl = ttl
        self.values = values

    def snapshot(self, comments: list) -> Optional[dict]:
        if not self.values:
            return None
        return {
            "ttl": int(self.ttl),
            "records": [{"content": v["content"], "disabled": bool(v["disabled"])} for v in self.values.values()],
            "comments": list(comments or []),
        }


@dataclass
class PlanChange:
    name: str
    type: str
    op: str                      # create | update | delete
    semantics: list[str]
    before: Optional[dict]
    after: Optional[dict]
    added: list[str]
    removed: list[str]
    kept: list[str]
    disabled_changed: list[str]
    ttl_before: Optional[int]
    ttl_after: Optional[int]

    @property
    def key(self) -> RRKey:
        return (self.name, self.type)

    def to_dict(self) -> dict:
        """Format ``BulkChange`` (Vorschau)."""
        return {
            "name": self.name, "type": self.type, "op": self.op, "semantics": list(self.semantics),
            "before": copy.deepcopy(self.before), "after": copy.deepcopy(self.after),
            "added": list(self.added), "removed": list(self.removed), "kept": list(self.kept),
            "disabled_changed": list(self.disabled_changed),
            "ttl_before": self.ttl_before, "ttl_after": self.ttl_after,
        }

    def audit_dict(self) -> dict:
        """Eintrag in ``details.changes`` (Audit v2, F1 4.3 / F7 4.3)."""
        return {"name": self.name, "type": self.type, "op": self.op,
                "before": copy.deepcopy(self.before), "after": copy.deepcopy(self.after)}

    def patch(self) -> dict:
        """PATCH-Eintrag; REPLACE ohne ``comments`` (PowerDNS behaelt bestehende Kommentare)."""
        if self.after is None:
            return {"name": self.name, "type": self.type, "changetype": "DELETE"}
        return {
            "name": self.name, "type": self.type, "ttl": int(self.after["ttl"]), "changetype": "REPLACE",
            "records": [{"content": r["content"], "disabled": bool(r["disabled"])} for r in self.after["records"]],
        }


@dataclass
class BulkPlan:
    changes: list[PlanChange] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    noop_ops: int = 0       # Ops ohne Wirkung (nur nicht-strikt gezaehlt)
    unchanged: int = 0      # beruehrte RRsets ohne Aenderung

    @property
    def blocking(self) -> bool:
        return has_errors(self.issues)

    def codes(self) -> set[str]:
        return {i.get("code") for i in self.issues}

    def patch_rrsets(self) -> list[dict]:
        return [c.patch() for c in self.changes]

    def audit_changes(self) -> list[dict]:
        return [c.audit_dict() for c in self.changes]

    def summary(self) -> dict:
        return {
            "rrsets_created": sum(1 for c in self.changes if c.op == "create"),
            "rrsets_updated": sum(1 for c in self.changes if c.op == "update"),
            "rrsets_deleted": sum(1 for c in self.changes if c.op == "delete"),
            "rrsets_unchanged": self.unchanged,
            "values_added": sum(len(c.added) for c in self.changes),
            "values_removed": sum(len(c.removed) for c in self.changes),
        }


def ops_counts(ops: BulkRecordUpdate) -> dict:
    return {"create": len(ops.create), "delete": len(ops.delete), "merge": len(ops.merge),
            "set_ttl": len(ops.set_ttl), "set_disabled": len(ops.set_disabled)}


def op_types(ops: BulkRecordUpdate) -> set[str]:
    out: set[str] = set()
    for lst in (ops.create, ops.delete, ops.merge, ops.set_ttl, ops.set_disabled):
        out.update((i.type or "").upper() for i in lst)
    return out


def lua_write_keys(ops: BulkRecordUpdate) -> list[RRKey]:
    """(Name, Typ) der LUA-RRsets, die ``create``/``merge``/``set_ttl``/``set_disabled`` anlegen bzw. aendern."""
    keys: list[RRKey] = []
    for attr in LUA_WRITE_OP_LISTS:
        for item in getattr(ops, attr):
            k = _key(item.name, item.type)
            if k[1] == "LUA" and k not in keys:
                keys.append(k)
    return keys


def static_validate(zone_norm: str, ops: BulkRecordUpdate) -> list[dict]:
    """Pruefungen ohne PowerDNS-Zugriff (F1 5.3). Nicht leer -> 422 bzw. blockierende Vorschau."""
    zone_norm = norm_name(zone_norm)
    out: list[dict] = []
    seen_outside: set[str] = set()
    for lst in (ops.create, ops.delete, ops.merge, ops.set_ttl, ops.set_disabled, ops.expected):
        for item in lst:
            n = norm_name(item.name)
            if not _inside(n, zone_norm) and n not in seen_outside:
                seen_outside.add(n)
                out.append(issue("outside_zone", name=n, zone=zone_norm))
    for d in ops.delete:
        if d.content is None and (d.type or "").upper() == "SOA":
            out.append(issue("soa_forbidden", name=norm_name(d.name), type="SOA"))
    for attr in LUA_WRITE_OP_LISTS:
        for item in getattr(ops, attr):
            t = (item.type or "").upper()
            if t in DNSSEC_AUTO_TYPES:
                out.append(issue("type_unknown", name=norm_name(item.name), type=t,
                                 text=f"{t} wird von PowerDNS selbst erzeugt und kann nicht geändert werden."))
    return out


def build_plan(
    zone_norm: str,
    zone_json: Optional[dict],
    ops: BulkRecordUpdate,
    *,
    strict: bool,
    lua_allowed: bool = True,
    dnssec_enabled: bool = False,
    lua_message: Optional[str] = None,
    lua_policy: Optional[str] = None,
) -> BulkPlan:
    """Plan fuer EINEN Server aus dessen Zonenstand (rein, synchron; F1 5.3).

    ``strict`` (Primary): fehlende Werte/RRsets sind Probleme (``value_missing``/``rrset_missing``);
    sonst (Peers) zaehlen sie als ``noop_ops`` (``peer_drift``). Reihenfolge: create, delete, merge,
    set_disabled, set_ttl. Nur RRsets mit geaendertem Fingerprint landen in ``changes`` (sortiert).
    """
    zone_norm = norm_name(zone_norm)
    cur = index_rrsets(zone_json)
    plan = BulkPlan()
    work: dict[RRKey, Optional[_RRState]] = {}
    sem: dict[RRKey, list[str]] = {}
    removed_here: dict[RRKey, set[str]] = {}

    def ck(rtype: str, content: str) -> str:
        return content_key(rtype, content, zone_norm)

    def existing_index(k: RRKey) -> dict[str, dict]:
        snap = cur.get(k)
        out: dict[str, dict] = {}
        for r in (snap or {}).get("records") or []:
            out.setdefault(ck(k[1], r["content"]), {"content": r["content"], "disabled": bool(r["disabled"])})
        return out

    def state(k: RRKey) -> Optional[_RRState]:
        if k not in work:
            snap = cur.get(k)
            work[k] = None if snap is None else _RRState(int(snap.get("ttl") or 0), existing_index(k))
        return work[k]

    def mark(k: RRKey, s: str) -> None:
        lst = sem.setdefault(k, [])
        if s not in lst:
            lst.append(s)

    def missing(k: RRKey, code: str, content: Optional[str] = None) -> None:
        if strict:
            if code == "value_missing":
                plan.issues.append(issue(code, name=k[0], type=k[1], content=content))
            else:
                plan.issues.append(issue(code, name=k[0], type=k[1]))
        else:
            plan.noop_ops += 1

    # 1) create = REPLACE (PowerDNS-Darstellung bestehender gleicher Werte wiederverwenden)
    for item in ops.create:
        k = _key(item.name, item.type)
        existing = existing_index(k)
        values: dict[str, dict] = {}
        for r in item.records:
            c = ck(k[1], r.content)
            if c in values:
                continue
            content = existing[c]["content"] if c in existing else r.content
            values[c] = {"content": content, "disabled": bool(r.disabled)}
        work[k] = _RRState(int(item.ttl), values)
        mark(k, "replace")
    # 2) + 3) delete (RRset bzw. einzelner Wert)
    for item in ops.delete:
        k = _key(item.name, item.type)
        if item.content is None:
            work[k] = None
            mark(k, "delete_rrset")
            continue
        st = state(k)
        removed = removed_here.get(k, set())
        if st is None and not removed:
            # RRset fehlt: ohne Normalisierung des (evtl. sehr langen) Werts melden (F15-fix3)
            missing(k, "value_missing", item.content)
            continue
        c = ck(k[1], item.content)
        if st is None or c not in st.values:
            if c not in removed:  # doppelt in derselben Anfrage -> keine Wirkung
                missing(k, "value_missing", item.content)
            continue
        del st.values[c]
        removed_here.setdefault(k, set()).add(c)
        if not st.values:
            work[k] = None
        mark(k, "delete_value")
    # 4) merge (Werte anhaengen)
    for item in ops.merge:
        k = _key(item.name, item.type)
        st = state(k)
        if st is None:
            st = _RRState(int(item.ttl if item.ttl is not None else ops.default_ttl), {})
            work[k] = st
        for r in item.records:
            c = ck(k[1], r.content)
            if c not in st.values:
                st.values[c] = {"content": r.content, "disabled": bool(r.disabled)}
        if item.ttl is not None:
            st.ttl = int(item.ttl)
        mark(k, "merge")
    # 5) set_disabled
    for item in ops.set_disabled:
        k = _key(item.name, item.type)
        st = state(k)
        if st is None:
            missing(k, "value_missing", item.content)
            continue
        c = ck(k[1], item.content)
        if c not in st.values:
            missing(k, "value_missing", item.content)
            continue
        st.values[c] = {**st.values[c], "disabled": bool(item.disabled)}
        mark(k, "disabled")
    # 6) set_ttl
    for item in ops.set_ttl:
        k = _key(item.name, item.type)
        st = state(k)
        if st is None:
            missing(k, "rrset_missing")
            continue
        st.ttl = int(item.ttl)
        mark(k, "ttl")

    # Aenderungen bestimmen
    for k in sorted(work):
        before = cur.get(k)
        st = work[k]
        after = st.snapshot((before or {}).get("comments") or []) if st is not None else None
        if snapshot_fingerprint(before, k[1], zone_norm) == snapshot_fingerprint(after, k[1], zone_norm):
            plan.unchanged += 1
            continue
        b_idx = {ck(k[1], r["content"]): r for r in (before or {}).get("records") or []}
        a_idx = {ck(k[1], r["content"]): r for r in (after or {}).get("records") or []}
        if before is None:
            op = "create"
        elif after is None:
            op = "delete"
        else:
            op = "update"
        plan.changes.append(PlanChange(
            name=k[0], type=k[1], op=op, semantics=list(sem.get(k, [])),
            before=copy.deepcopy(before), after=after,
            added=[r["content"] for c, r in a_idx.items() if c not in b_idx],
            removed=[r["content"] for c, r in b_idx.items() if c not in a_idx],
            kept=[r["content"] for c, r in a_idx.items() if c in b_idx],
            disabled_changed=[r["content"] for c, r in a_idx.items()
                              if c in b_idx and bool(b_idx[c]["disabled"]) != bool(r["disabled"])],
            ttl_before=(before or {}).get("ttl") if before is not None else None,
            ttl_after=after["ttl"] if after is not None else None,
        ))

    _check_changes(plan, zone_norm, cur, work, lua_allowed=lua_allowed, dnssec_enabled=dnssec_enabled,
                   lua_message=lua_message, lua_policy=lua_policy)
    return plan


def _check_changes(plan: BulkPlan, zone_norm: str, cur: dict, work: dict, *, lua_allowed: bool,
                   dnssec_enabled: bool, lua_message: Optional[str], lua_policy: Optional[str]) -> None:
    """Pruefungen auf den Aenderungen (SOA, Apex-NS, CNAME, LUA, DNSKEY, Mengenlimit)."""
    for c in plan.changes:
        if c.type == "SOA" and c.after is None:
            plan.issues.append(issue("soa_forbidden", name=c.name, type=c.type))
        if c.type == "NS" and c.name == zone_norm and c.after is None:
            plan.issues.append(issue("apex_ns_delete", name=c.name, type=c.type))
        if c.type == "LUA" and c.after is not None and not lua_allowed:
            from app.services.lua_records import MSG_DENIED_ADMIN

            msg = lua_message or MSG_DENIED_ADMIN
            plan.issues.append(issue("lua_forbidden", name=c.name, type=c.type, message=msg,
                                     policy=lua_policy or "admin"))
        if dnssec_enabled and c.type in DNSKEY_MANAGED_TYPES:
            plan.issues.append(issue("dnskey_managed", "warning", name=c.name, type=c.type))

    # CNAME-Regeln je betroffenem Owner-Namen (Typen nach Anwendung, ohne DNSSEC-Auto-Typen)
    names = {c.name for c in plan.changes}
    if names:
        types: dict[str, set[str]] = {}
        for (n, t) in cur:
            if n in names and t not in DNSSEC_AUTO_TYPES:
                types.setdefault(n, set()).add(t)
        for (n, t), st in work.items():
            if n not in names or t in DNSSEC_AUTO_TYPES:
                continue
            if st is not None and st.values:
                types.setdefault(n, set()).add(t)
            else:
                types.setdefault(n, set()).discard(t)
        for n in sorted(names):
            tset = types.get(n, set())
            if "CNAME" not in tset:
                continue
            if n == zone_norm:
                plan.issues.append(issue("apex_cname", name=n, type="CNAME"))
            elif len(tset) > 1:
                plan.issues.append(issue("cname_conflict", name=n, type="CNAME"))
            st = work.get((n, "CNAME"))
            count = len(st.values) if st is not None else len(((cur.get((n, "CNAME")) or {}).get("records") or []))
            if (n, "CNAME") in work and st is None:
                count = 0
            if count > 1:
                plan.issues.append(issue("cname_multi", name=n, type="CNAME"))

    if len(plan.changes) > BULK_MAX_CHANGED_RRSETS:
        plan.issues.append(issue("too_many_changes", max=BULK_MAX_CHANGED_RRSETS))


# =============================================================================
# Textfluss
# =============================================================================
def compile_text_ops(parsed: ParsedFragment, text: BulkTextInput, zone_json: Optional[dict],
                     zone_norm: str) -> tuple[Optional[BulkRecordUpdate], list[dict]]:
    """Geparste RRsets -> ``BulkRecordUpdate`` je Modus (F1 5.3). Rueckgabe ``(ops|None, issues)``.

    - ``merge``: je RRset ein ``BulkMergeItem`` (TTL nur, wenn im Text angegeben)
    - ``replace``: je RRset ein ``RecordCreate`` (TTL: Text > bestehende TTL am Primary > ``default_ttl``)
    - ``sync_scope``: wie ``replace``, dazu ``RecordDelete`` fuer Scope-RRsets, die im Text fehlen und am Primary
      existieren; ungueltige Scope-Eintraege -> Warnung ``scope_invalid``.
    """
    zone_norm = norm_name(zone_norm)
    cur = index_rrsets(zone_json)
    issues: list[dict] = []
    creates: list[RecordCreate] = []
    merges: list[BulkMergeItem] = []
    deletes: list[RecordDelete] = []
    for rr in parsed.rrsets.values():
        records = [RecordItem(content=v.content, disabled=v.disabled) for v in rr.values]
        if text.mode == "merge":
            merges.append(BulkMergeItem(name=rr.name, type=rr.type, ttl=rr.ttl, records=records))
            continue
        ttl = rr.ttl
        if ttl is None:
            existing = cur.get((rr.name, rr.type))
            ttl = int(existing["ttl"]) if existing is not None else int(text.default_ttl)
            if not (60 <= ttl <= 604800):
                issues.append(issue("ttl_range", "warning", name=rr.name, type=rr.type, ttl=ttl))
                ttl = min(max(ttl, 60), 604800)
        creates.append(RecordCreate(name=rr.name, type=rr.type, ttl=ttl, records=records))
    if text.mode == "sync_scope":
        in_text = set(parsed.rrsets)
        done: set[RRKey] = set()
        for sk in text.scope:
            k = _key(sk.name, sk.type)
            if (not _inside(k[0], zone_norm) or k[1] == "SOA" or k[1] in DNSSEC_AUTO_TYPES
                    or k[1] not in ALLOWED_RECORD_TYPES):
                issues.append(issue("scope_invalid", "warning", name=k[0], type=k[1]))
                continue
            if k in in_text or k in done:
                continue
            done.add(k)
            if k in cur:
                deletes.append(RecordDelete(name=k[0], type=k[1]))
    if len(creates) + len(merges) + len(deletes) > BULK_MAX_OPS:
        issues.append(issue("too_many_changes", max=BULK_MAX_OPS))
        return None, issues
    ops = BulkRecordUpdate(create=creates, merge=merges, delete=deletes, default_ttl=text.default_ttl,
                           source="text", mode=text.mode)
    return ops, issues


def prune_unchanged(ops: BulkRecordUpdate, plan: BulkPlan) -> BulkRecordUpdate:
    """Ops entfernen, deren RRset unveraendert bleibt (Textfluss: nur Wirksames wird gesendet)."""
    changed = {c.key for c in plan.changes}

    def keep(lst):
        return [i for i in lst if _key(i.name, i.type) in changed]

    return ops.model_copy(update={
        "create": keep(ops.create), "delete": keep(ops.delete), "merge": keep(ops.merge),
        "set_ttl": keep(ops.set_ttl), "set_disabled": keep(ops.set_disabled),
    })


def touched_keys(ops: BulkRecordUpdate) -> list[RRKey]:
    """Alle (Name, Typ), die irgendeine Op der Anfrage beruehrt (auch solche, die unveraendert bleiben)."""
    keys: set[RRKey] = set()
    for lst in (ops.create, ops.delete, ops.merge, ops.set_ttl, ops.set_disabled):
        keys.update(_key(i.name, i.type) for i in lst)
    return sorted(keys)


def expectations(ops: BulkRecordUpdate, zone_json: Optional[dict], zone_norm: str) -> list[BulkExpectation]:
    """Fingerprints des Primary-Stands fuer **jedes** von den Ops beruehrte RRset (fehlend -> ``absent``).

    Auch RRsets, die die Vorschau als unveraendert zeigt, bekommen einen Fingerprint: Aendert jemand ein solches
    RRset zwischen Vorschau und Anwenden, wuerde z. B. ein ``create`` (REPLACE) den neuen Wert sonst ohne 409
    entfernen (Vorschau == Anwendung).
    """
    cur = index_rrsets(zone_json)
    return [BulkExpectation(name=k[0], type=k[1], fingerprint=snapshot_fingerprint(cur.get(k), k[1], zone_norm))
            for k in touched_keys(ops)]


# =============================================================================
# LUA-Policy (F15 5.6) und Server-Ziele
# =============================================================================
@dataclass
class LuaContext:
    allowed: bool = True
    message: Optional[str] = None
    policy: Optional[str] = None


async def lua_context(db, user, ops: BulkRecordUpdate) -> LuaContext:
    """Policy nur lesen, wenn die Anfrage LUA-RRsets beruehrt (sonst kein DB-Zugriff)."""
    if "LUA" not in op_types(ops):
        return LuaContext()
    from app.services.lua_records import get_lua_policy, lua_denied_message, lua_policy_allows

    policy = await get_lua_policy(db)
    return LuaContext(allowed=lua_policy_allows(policy, user), message=lua_denied_message(policy), policy=policy)


def _lua_static_issues(ops: BulkRecordUpdate, lua: LuaContext, existing: list[dict]) -> list[dict]:
    if lua.allowed:
        return []
    flagged = {(i.get("name"), i.get("type")) for i in existing if i.get("code") == "lua_forbidden"}
    out = []
    for k in lua_write_keys(ops):
        if k in flagged:
            continue
        out.append(issue("lua_forbidden", name=k[0], type=k[1], message=lua.message or "", policy=lua.policy))
    return out


def no_target_error(server_name: str, info: dict) -> HTTPException:
    """Primary nicht schreibbar: read-only -> 403, nicht geladen -> 503, unbekannt -> 404."""
    hint = str(info.get(server_name) or "")
    if hint == fanout.INFO_READ_ONLY:
        return fanout.read_only_error(server_name)
    if hint.startswith("not loaded"):
        reason = hint.split(":", 1)[1].strip() if ":" in hint else ""
        return HTTPException(status_code=503,
                             detail=f"Server '{server_name}' ist nicht geladen ({reason or 'unbekannt'}).")
    return HTTPException(status_code=404, detail=f"Server '{server_name}' nicht gefunden")


# =============================================================================
# Vorschau
# =============================================================================
def _empty_summary() -> dict:
    return BulkPlan().summary()


async def preview_bulk(db, user, server_name: str, zone_id: str, req: BulkPreviewRequest) -> dict:
    """Trockenlauf gegen den Primary (F1 3.2): Diff, Probleme und der fertige Request-Body fuer ``/bulk``.

    Kein Audit, kein Webhook. 200 auch bei blockierenden Problemen (stehen in ``issues``).
    """
    zone_norm = norm_name(zone_id)
    targets, info = await fanout.writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise no_target_error(server_name, info)
    primary_client = dict(targets).get(server_name) or targets[0][1]
    peers = [n for n, _ in targets if n != server_name]
    try:
        zone_json = await primary_client.get_zone(zone_id)
    except PowerDNSAPIError as e:
        if fanout.zone_not_found_for(e):
            raise HTTPException(status_code=404,
                                detail=f"Zone '{zone_norm}' ist auf Server '{server_name}' nicht vorhanden")
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    dnssec = bool((zone_json or {}).get("dnssec"))

    source = req.ops.source if req.ops is not None else "text"
    mode = req.ops.mode if req.ops is not None else req.text.mode

    def blocked(issues: list[dict]) -> dict:
        return {"zone": zone_norm, "server": server_name, "source": source, "mode": mode, "blocking": True,
                "issues": issues, "changes": [], "summary": _empty_summary(), "ops": None, "peers": peers,
                "skipped_servers": dict(info)}

    pre_issues: list[dict] = []
    if req.text is not None:
        parsed = await asyncio.to_thread(parse_bind_fragment, zone_norm, req.text.content,
                                         max_lines=BULK_MAX_TEXT_LINES)
        if parsed.has_errors:
            return blocked(parsed.issues)
        ops, compile_issues = compile_text_ops(parsed, req.text, zone_json, zone_norm)
        pre_issues = parsed.warnings + compile_issues
        if ops is None or has_errors(compile_issues):
            return blocked(pre_issues)
    else:
        ops = req.ops.model_copy(update={"expected": [], "force": False})
        if count_ops(ops) == 0:
            return blocked([issue("empty_input")])
        static = static_validate(zone_norm, ops)
        if static:
            return blocked(static)

    lua = await lua_context(db, user, ops)
    plan = await asyncio.to_thread(build_plan, zone_norm, zone_json, ops, strict=True, lua_allowed=lua.allowed,
                                   dnssec_enabled=dnssec, lua_message=lua.message, lua_policy=lua.policy)
    issues = pre_issues + plan.issues
    if req.text is not None:
        ops = prune_unchanged(ops, plan)
    issues += _lua_static_issues(ops, lua, issues)
    ops = ops.model_copy(update={"expected": expectations(ops, zone_json, zone_norm)})
    blocking = has_errors(issues)
    return {
        "zone": zone_norm, "server": server_name, "source": ops.source, "mode": ops.mode, "blocking": blocking,
        "issues": issues, "changes": [c.to_dict() for c in plan.changes], "summary": plan.summary(),
        "ops": None if blocking or not plan.changes else ops, "peers": peers, "skipped_servers": dict(info),
    }


# =============================================================================
# Anwenden
# =============================================================================
class BulkRejected(Exception):
    """Abbruch aus dem Primary-Builder (nichts geschrieben). ``audit_details`` -> Fehler-Audit."""

    def __init__(self, status_code: int, detail: Any, *, audit_details: Optional[dict] = None,
                 error_message: Optional[str] = None):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
        self.audit_details = audit_details
        self.error_message = error_message


class PeerPlanBlocked(Exception):
    """Blockierendes Problem nur auf einem Peer -> ``error: …`` fuer diesen Peer, kein PATCH dort."""


@dataclass
class PtrOutcome:
    results: list           # vollstaendige PtrResults (Antwort details.ptr)
    compact: Optional[list]  # kompakt (Audit details.ptr, Webhook data.ptr)


async def sync_ptr_for_changes(db, user, server_name: str, requested: Optional[bool], changes: list[dict], *,
                               ttl_default: int = 3600) -> Optional[PtrOutcome]:
    """Gemeinsamer PTR-Einhaengepunkt aller vier Record-Schreiber (create/update/delete/bulk; F11 5.12, B.6a).

    Laeuft nur nach Primary-Erfolg und nur, wenn A/AAAA-RRsets geaendert wurden und die PTR-Pflege aktiv ist
    (``requested`` bzw. Admin-Default ``ptr_auto_default`` = ``ptr.resolve_manage_ptr``). ``changes`` im
    Audit-v2-Format; ``ptr.sync_for_changes`` wirft nie. Rueckgabe ``None`` = keine PTR-Pflege gelaufen.
    """
    if not any(str((c or {}).get("type", "")).upper() in PTR_FORWARD_TYPES for c in changes or []):
        return None
    if requested is None:
        try:
            from app.services.system_settings import get_bool_setting

            manage = await get_bool_setting(db, PTR_AUTO_DEFAULT_KEY, False)
        except Exception:  # noqa: BLE001 - der Forward-Write ist erfolgt und muss auditiert werden
            logger.exception("PTR-Pflege: Admin-Default nicht lesbar – PTR-Pflege uebersprungen")
            return None
    else:
        manage = bool(requested)
    if not manage:
        return None
    from app.services import ptr as ptr_service  # F9F11-BE (Plan B.6a)

    results = await ptr_service.sync_for_changes(db, user, server_name, changes, ttl_default=ttl_default,
                                                 actor_user_id=getattr(user, "id", None))
    results = jsonable_encoder(results or [])
    return PtrOutcome(results=results, compact=jsonable_encoder(ptr_service.compact(results)))


@dataclass
class _ApplyState:
    zone_norm: str
    server_name: str
    ops: BulkRecordUpdate
    lua: LuaContext
    plan: Optional[BulkPlan] = None
    forced: bool = False
    drift: dict[str, int] = field(default_factory=dict)

    def details(self, *, fanout_summary: Optional[dict], applied: Optional[bool], after_source: Optional[str],
                primary_outcome: Optional[str], extra: Optional[dict] = None) -> dict:
        changes = self.plan.audit_changes() if self.plan is not None else []
        ext = {
            "primary_outcome": primary_outcome,
            "source": self.ops.source,
            "mode": self.ops.mode,
            "ops": ops_counts(self.ops),
            "forced": self.forced,
            "peer_drift": dict(self.drift),
            "changes_total": len(changes),
        }
        if applied is False:
            ext["applied"] = False
        ext.update(extra or {})
        return history_details(self.zone_norm, changes, fanout=fanout_summary, after_source=after_source,
                               legacy={"created": len(self.ops.create), "deleted": len(self.ops.delete)},
                               extra=ext)

    # --- Builder (Betriebsart B) ----------------------------------------------------------------------------
    def build(self, server: str, zone_json: dict) -> list[dict]:
        if server == self.server_name:
            return self._primary(zone_json)
        return self._peer(server, zone_json)

    def _primary(self, zone_json: dict) -> list[dict]:
        ops = self.ops
        conflicts = []
        for x in ops.expected:
            fp = snapshot_fingerprint(rrset_snapshot(zone_json, x.name, x.type), x.type, self.zone_norm)
            if fp != x.fingerprint:
                conflicts.append({"name": norm_name(x.name), "type": x.type.upper()})
        plan = build_plan(self.zone_norm, zone_json, ops, strict=True, lua_allowed=self.lua.allowed,
                          dnssec_enabled=bool((zone_json or {}).get("dnssec")), lua_message=self.lua.message,
                          lua_policy=self.lua.policy)
        if ops.expected:
            # Mit Sperre muss jedes RRset, das jetzt geaendert wuerde, abgedeckt sein: Ein RRset ohne Fingerprint
            # hat die Vorschau nicht als Aenderung gezeigt (Vorschau == Anwendung) -> wie eine Abweichung behandeln.
            covered = {_key(x.name, x.type) for x in ops.expected}
            flagged = {(c["name"], c["type"]) for c in conflicts}
            for c in plan.changes:
                if c.key not in covered and c.key not in flagged:
                    conflicts.append({"name": c.name, "type": c.type})
        if conflicts and not ops.force:
            raise BulkRejected(
                409, {"message": MSG_CONFLICT, "conflicts": conflicts},
                audit_details=self.details(fanout_summary=None, applied=False, after_source=None,
                                           primary_outcome=None, extra={"conflicts": conflicts}),
                error_message=MSG_CONFLICT_AUDIT,
            )
        self.forced = bool(conflicts) and ops.force
        self.plan = plan
        missing = [i for i in plan.issues if i.get("code") in ("value_missing", "rrset_missing")]
        if missing:
            raise BulkRejected(
                404, {"message": MSG_MISSING, "issues": missing},
                audit_details=self.details(fanout_summary=None, applied=False, after_source=None,
                                           primary_outcome=None, extra={"issues": missing}),
                error_message=MSG_MISSING,
            )
        if "lua_forbidden" in plan.codes():
            from app.services.lua_records import MSG_DENIED_ADMIN

            raise BulkRejected(403, self.lua.message or MSG_DENIED_ADMIN)
        if plan.blocking:
            raise BulkRejected(422, {"message": MSG_BLOCKED, "issues": plan.issues})
        return plan.patch_rrsets()

    def _peer(self, server: str, zone_json: dict) -> list[dict]:
        plan = build_plan(self.zone_norm, zone_json, self.ops, strict=False, lua_allowed=True,
                          dnssec_enabled=bool((zone_json or {}).get("dnssec")))
        if plan.noop_ops:
            self.drift[server] = plan.noop_ops
        if plan.blocking:
            text = "; ".join(i.get("message", "") for i in plan.issues if i.get("severity") == "error")
            raise PeerPlanBlocked(text[:500])
        return plan.patch_rrsets()


async def apply_bulk(db, user, server_name: str, zone_id: str, ops: BulkRecordUpdate) -> MessageResponse:
    """``POST …/bulk`` (F1 3.3). Aufrufer hat ``assert_zone_access(write=True)`` bereits geprueft."""
    zone_norm = norm_name(zone_id)
    if count_ops(ops) == 0:
        raise HTTPException(status_code=422, detail={"message": MSG_EMPTY, "issues": [issue("empty_input")]})
    static = static_validate(zone_norm, ops)
    if static:
        raise HTTPException(status_code=422, detail={"message": MSG_INVALID, "issues": static})
    lua = await lua_context(db, user, ops)
    if not lua.allowed and lua_write_keys(ops):
        # LUA-Gate vor jedem PowerDNS-Zugriff (F15 3.1); Loeschungen pruefen der Plan (after is not None)
        raise HTTPException(status_code=403, detail=lua.message)

    targets, info = await fanout.writable_targets_for_zone(db, zone_id, server_name)
    if not targets:
        raise no_target_error(server_name, info)

    st = _ApplyState(zone_norm=zone_norm, server_name=server_name, ops=ops, lua=lua)

    async def audit_error(details: dict, message: str) -> None:
        await write_audit(db, "BULK_UPDATE", "record", zone_norm, user_id=user.id, details=details,
                          status="error", error_message=message, server_name=server_name, zone_name=zone_norm)

    try:
        fan = await fanout.apply_rrsets(db, server_name, zone_id, build=st.build, targets=targets, info=info)
    except BulkRejected as rej:
        if rej.audit_details is not None:
            await audit_error(rej.audit_details, rej.error_message or str(rej.detail))
        raise HTTPException(status_code=rej.status_code, detail=rej.detail)

    summary = fan.summary
    if not fan.primary_success:
        if fan.primary_status == fanout.STATUS_ZONE_MISSING:
            detail = f"Zone '{zone_norm}' ist auf Server '{server_name}' nicht vorhanden – es wurde nichts geschrieben."
            code = 404
        else:
            err = fan.primary_error
            code = (err.status_code if err is not None and err.status_code else None) or 502
            detail = err.detail if err is not None else f"Server '{server_name}' hat die Änderung nicht angenommen."
        await audit_error(st.details(fanout_summary=summary, applied=False, after_source=None,
                                     primary_outcome=fan.primary_outcome), str(detail))
        raise HTTPException(status_code=code, detail=detail)

    plan = st.plan or BulkPlan()
    base = {
        "created": len(ops.create), "deleted": len(ops.delete),
        "changed_rrsets": len(plan.changes), "unchanged_rrsets": plan.unchanged,
        "fanout": summary, "peer_drift": dict(st.drift),
    }
    if not any(v == fanout.STATUS_SAVED for v in fan.results.values()):
        return MessageResponse(message=MSG_NO_CHANGES, details={**base, "audit_id": None})

    audit_changes = plan.audit_changes()
    ptr = await sync_ptr_for_changes(db, user, server_name, ops.manage_ptr, audit_changes,
                                     ttl_default=ops.default_ttl)
    after_source = AFTER_REREAD if fan.primary_outcome == "verified_after_timeout" else AFTER_COMPUTED
    details = st.details(fanout_summary=summary, applied=None, after_source=after_source,
                         primary_outcome=fan.primary_outcome)
    if ptr is not None:
        details["ptr"] = ptr.compact
    logger.info("Bulk %s/%s: %d RRsets geaendert, %d unveraendert", server_name, zone_norm, len(plan.changes),
                plan.unchanged)
    entry = await write_audit(db, "BULK_UPDATE", "record", zone_norm, user_id=user.id, details=details,
                              server_name=server_name, zone_name=zone_norm)

    from app.services.webhook_outbox import enqueue_event

    data = {"server": server_name, "zone": zone_norm, "created": len(ops.create), "deleted": len(ops.delete),
            "source": ops.source, "mode": ops.mode, **webhook_changes(audit_changes),
            "changes_total": len(audit_changes), "fanout": summary}
    if ptr is not None:
        data["ptr"] = ptr.compact
    await enqueue_event(db, "record.bulk", actor=user, data=data, zone=zone_norm, server=server_name,
                        audit_log_id=entry.id if entry is not None else None)

    resp = {**base, "audit_id": entry.id if entry is not None else None}
    if ptr is not None:
        resp["ptr"] = ptr.results
    return MessageResponse(message=f"Bulk-Änderung angewendet: {len(plan.changes)} RRsets geändert", details=resp)
