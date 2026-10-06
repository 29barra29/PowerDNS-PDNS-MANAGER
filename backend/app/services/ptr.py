"""PTR-Pflege in vom Panel verwalteten Reverse-Zonen (F11 5.8/5.12, Bauplan B.6a [D7, D11]).

Nach einem erfolgreichen A/AAAA-Write wird der passende PTR gesetzt bzw. entfernt – nur in Reverse-Zonen, die ein
schreibbarer Panel-Server fuehrt und auf die der handelnde Benutzer Schreibrecht hat (inkl. F14-Token-Scope). Regeln:

- Fremde PTRs werden nie ueberschrieben oder entfernt (Entfernen nur, wenn der PTR genau auf den eigenen Namen zeigt;
  ein PTR mit anderem Ziel ist ``conflict`` bzw. ``other_target``).
- Classless-Delegationen (RFC 2317) werden erkannt und abgelehnt: Zone ``<a>/<len>.<parent>`` bzw. ``<a>-<b>.<parent>``,
  aber nur, wenn das Host-Oktett im Bereich der Zone liegt [D11]; ausserdem ein CNAME am PTR-Namen.
- TTL eines neu angelegten PTR = TTL des Forward-Records; bestehende PTR-RRsets behalten ihre TTL.
- PTR-Probleme brechen den Forward-Write nie: :func:`sync_ptrs` wirft nicht, jedes Problem steht im Ergebnis.
- Jede geschriebene Reverse-Zone bekommt einen eigenen Audit-Eintrag ``PTR_SYNC`` (Format v2, ruecksetzbar) und das
  Webhook-Ereignis ``record.ptr_synced``; der Forward-Audit traegt die Kurzfassung (:func:`compact`) unter ``ptr``.
- Zonennamen erscheinen in Ergebnissen nur, wenn der Benutzer die Zone lesen darf [S5].

Einstiegspunkte:
- :func:`sync_for_changes` (B.6a): generisch ueber Audit-v2-``changes`` – Records-Endpunkte und Bulk (F1) rufen nur
  diese Funktion; damit sind create/delete/merge/replace/set_disabled/set_ttl abgedeckt.
- :func:`sync_ptrs`: Kern fuer eine Liste von :class:`PtrOp` (auch DynDNS).
- :func:`lookup` (``GET /ptr/lookup``), :func:`reverse_zones_available` (``GET /ptr/config``).
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

import dns.exception
import dns.reversename
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import effective_zone_filter, has_zone_access
from app.core.names import normalize_zone_name
from app.core.request_context import get_token_scope
from app.models.models import User, UserZoneAccess
from app.services import fanout, zone_index
from app.services.acme import find_matching_zone
from app.services.audit import write_audit
from app.services.pdns_client import PowerDNSAPIError, pdns_error_text, pdns_manager
from app.services.record_history import (
    AFTER_REREAD,
    build_changes,
    capture_after,
    history_details,
    simulate_patch,
    webhook_changes,
)
from app.services.rrsets import norm_name, rrset_snapshot
from app.services.system_settings import get_bool_setting

logger = logging.getLogger(__name__)

KEY_AUTO_DEFAULT = "ptr_auto_default"
PTR_TYPES = ("A", "AAAA")
MAX_OPS = 500
DEFAULT_TTL = 3600
READ_TIMEOUT = 15.0
WRITE_TIMEOUT = 15.0
LOOKUP_TIMEOUT = 10.0
READ_ZONE_THRESHOLD = 20  # mehr PTR-Namen in einer Zone -> eine GET /zones/{id} statt einzelner Lesezugriffe
DETAIL_MAX = 300

# "0/25", "128/25" (Netz/Laenge) bzw. "0-63", "64-127" (Bereich). Reine Ziffern = /32-Zone, KEIN Classless.
_CLASSLESS_LABEL = re.compile(r"^(\d{1,3})([-/])(\d{1,3})$")

MSG_INTERNAL = "Interner Fehler"
MSG_INVALID_IP = "Ungueltige IP-Adresse"
MSG_FORBIDDEN_ZONE = "Keine Berechtigung für diese Reverse-Zone"

Action = Literal["set", "removed", "unchanged", "skipped", "error"]


@dataclass(frozen=True)
class PtrOp:
    """Eine PTR-Operation: ``set`` (PTR soll auf ``target`` zeigen) oder ``remove`` (PTR auf ``target`` entfernen)."""

    op: Literal["set", "remove"]
    ip: str  # kanonisch: str(ipaddress.ip_address(x))
    target: str  # Forward-FQDN, normalisiert (lower + Trailing-Dot)
    ttl: int = DEFAULT_TTL


# =====================================================================================================================
# Reine Helfer
# =====================================================================================================================
def canonical_ip(content: Any) -> Optional[str]:
    """Kanonische IP (``2001:db8::5`` komprimiert) oder ``None``, wenn ``content`` keine IP ist."""
    if not isinstance(content, str):
        return None
    try:
        return str(ipaddress.ip_address(content.strip()))
    except ValueError:
        return None


def reverse_name(ip: str) -> str:
    """``192.0.2.5`` -> ``5.2.0.192.in-addr.arpa.``, IPv6 -> Nibble-Form unter ``ip6.arpa.`` (klein)."""
    return dns.reversename.from_address(ip).to_text().lower()


def classless_range(label: str) -> Optional[tuple[int, int]]:
    """Adressbereich eines RFC-2317-Labels oder ``None`` (kein gueltiges Classless-Label).

    ``a/len`` -> ``[a, a + 2^(32-len) - 1]`` (len 24..32, Ende <= 255), ``a-b`` -> ``[a, b]`` (a <= b <= 255) [D11].
    """
    m = _CLASSLESS_LABEL.fullmatch((label or "").strip())
    if not m:
        return None
    a, sep, b = int(m.group(1)), m.group(2), int(m.group(3))
    if a > 255:
        return None
    if sep == "/":
        if not 24 <= b <= 32:
            return None
        end = a + 2 ** (32 - b) - 1
        if end > 255:
            return None
        return (a, end)
    if a > b or b > 255:
        return None
    return (a, b)


def detect_classless_zone(ptr_name: str, zone_names: Iterable[str]) -> Optional[str]:
    """Classless-Zone (RFC 2317), in die ``ptr_name`` delegiert waere, oder ``None``.

    Nur ``in-addr.arpa``: zu ``10.2.0.192.in-addr.arpa.`` passt eine Zone ``<label>.2.0.192.in-addr.arpa.`` mit
    Classless-Label, wenn das Host-Oktett (10) im Bereich des Labels liegt [D11]. ``0/26`` deckt .0-.63 ab – fuer .70
    ist die Elternzone zustaendig.
    """
    p = norm_name(ptr_name)
    if not p.endswith(".in-addr.arpa."):
        return None
    host, _, parent = p.partition(".")
    if not host.isdigit() or int(host) > 255 or not parent:
        return None
    octet = int(host)
    hits = []
    for z in zone_names or ():
        zn = normalize_zone_name(z)
        label, _, zparent = zn.partition(".")
        if zparent != parent:
            continue
        rng = classless_range(label)
        if rng is not None and rng[0] <= octet <= rng[1]:
            hits.append(zn)
    return sorted(hits)[0] if hits else None


def _classless_zone_of_cname(target: str) -> Optional[str]:
    """``10.0/26.2.0.192.in-addr.arpa.`` -> ``0/26.2.0.192.in-addr.arpa.`` (Zone des CNAME-Ziels, RFC 2317)."""
    t = norm_name(target)
    _, _, rest = t.partition(".")
    return rest or None


async def resolve_manage_ptr(db: AsyncSession, requested: Optional[bool]) -> bool:
    """Explizites ``manage_ptr`` des Aufrufers, sonst Admin-Default ``ptr_auto_default`` (Default aus)."""
    if requested is not None:
        return bool(requested)
    return await get_bool_setting(db, KEY_AUTO_DEFAULT, False)


def ops_for_values(target: str, values: Iterable[tuple[str, bool]], *, op: str, ttl: int) -> list[PtrOp]:
    """``op="set"``: nur nicht deaktivierte IP-Werte; ``op="remove"``: alle IP-Werte; Nicht-IPs werden ignoriert."""
    if op not in ("set", "remove"):
        raise ValueError(f"unbekannte PTR-Operation {op!r}")
    t = norm_name(target)
    out: list[PtrOp] = []
    for content, disabled in values or ():
        ip = canonical_ip(content)
        if ip is None:
            continue
        if op == "set" and disabled:
            continue
        out.append(PtrOp(op, ip, t, int(ttl or DEFAULT_TTL)))  # type: ignore[arg-type]
    return out


def ops_for_update(target: str, rtype: str, old_content: str, new_content: str, ttl: int,
                   disabled: bool = False) -> list[PtrOp]:
    """Wertaenderung: alte IP entfernen, neue setzen. Gleiche IP: nichts (bzw. entfernen, wenn jetzt deaktiviert)."""
    if (rtype or "").strip().upper() not in PTR_TYPES:
        return []
    t = norm_name(target)
    old, new = canonical_ip(old_content), canonical_ip(new_content)
    ttl = int(ttl or DEFAULT_TTL)
    if old is not None and old == new:
        return [PtrOp("remove", old, t, ttl)] if disabled else []
    out: list[PtrOp] = []
    if old is not None:
        out.append(PtrOp("remove", old, t, ttl))
    if new is not None and not disabled:
        out.append(PtrOp("set", new, t, ttl))
    return out


def _enabled_ips(values: Optional[Iterable[tuple[str, bool]]]) -> list[str]:
    out: list[str] = []
    for content, disabled in values or ():
        ip = canonical_ip(content)
        if ip is not None and not disabled and ip not in out:
            out.append(ip)
    return out


def ops_for_rrset_change(target: str, before: Iterable[tuple[str, bool]],
                         after: Optional[Iterable[tuple[str, bool]]], ttl: int) -> list[PtrOp]:
    """RRset-Aenderung (``after=None`` = RRset geloescht).

    Verglichen werden nur aktive Werte: was vorher aktiv war und danach nicht mehr (geloescht oder deaktiviert) ->
    ``remove``; was danach aktiv ist und vorher nicht -> ``set``. Reine TTL-Aenderungen erzeugen keine Op.
    """
    t = norm_name(target)
    ttl = int(ttl or DEFAULT_TTL)
    b = _enabled_ips(before)
    a = _enabled_ips(after) if after is not None else []
    out = [PtrOp("remove", ip, t, ttl) for ip in b if ip not in a]
    out += [PtrOp("set", ip, t, ttl) for ip in a if ip not in b]
    return out


def compact(results: Optional[list[dict]]) -> Optional[list[dict]]:
    """Kurzfassung fuer Forward-Audits und Webhooks: ``[{"ip", "ptr", "zone", "action", "reason"}]``."""
    if results is None:
        return None
    return [{k: r.get(k) for k in ("ip", "ptr", "zone", "action", "reason")} for r in results]


def _dedupe(ops: Iterable[PtrOp]) -> list[PtrOp]:
    out: list[PtrOp] = []
    seen: set[tuple[str, str, str]] = set()
    for op in ops or ():
        key = (op.op, op.ip, op.target)
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return out


def _base_result(op: PtrOp) -> dict:
    return {"ip": op.ip, "ptr": None, "zone": None, "target": op.target, "op": op.op, "action": None,
            "reason": None, "existing": None, "classless_zone": None, "fanout": None, "detail": None}


def _skip(res: dict, reason: str, **extra) -> None:
    res.update(action="skipped", reason=reason, **extra)


def _error(res: dict, detail: str) -> None:
    res.update(action="error", reason="error", detail=(detail or MSG_INTERNAL)[:DETAIL_MAX])


def _err_text(exc: Exception) -> str:
    if isinstance(exc, PowerDNSAPIError):
        return pdns_error_text(exc)[:DETAIL_MAX]
    return MSG_INTERNAL


# =====================================================================================================================
# Rechte
# =====================================================================================================================
async def writable_zone_filter(db: AsyncSession, user: User) -> Optional[set[str]]:
    """Zonen, auf die der Aufrufer schreiben darf: ``None`` = alle (Admin ohne Token-Scope), sonst normalisierte Namen.

    Gleiche Regeln wie ``assert_zone_access(write=True)``: Lese-Token -> keine; Token-Scope begrenzt; Benutzer ohne
    Admin-Rolle nur Zonen ohne ``permission = "read"``.
    """
    scope = get_token_scope()
    if scope is not None and scope.read_only:
        return set()
    visible = await effective_zone_filter(db, user)
    if visible is None:
        return None
    if not visible:
        return set()
    rows = (await db.execute(
        select(UserZoneAccess.zone_name, UserZoneAccess.permission).where(UserZoneAccess.user_id == user.id)
    )).all()
    read_only = {
        normalize_zone_name(z) for z, p in rows
        if ((p or "manage").strip().lower() or "manage") == "read"
    }
    return {z for z in visible if z not in read_only}


async def _zone_readable(db: AsyncSession, user: User, zone: Optional[str]) -> bool:
    if not zone:
        return False
    try:
        return await has_zone_access(db, user, zone, write=False)
    except Exception:  # noqa: BLE001 - nur fuer die Anzeige des Zonennamens
        return False


# =====================================================================================================================
# Lesen
# =====================================================================================================================
async def _read_names(client, zone: str, names: list[str], *, timeout: float) -> list[dict]:
    """Alle RRsets der ``names`` (alle Typen, fuer die CNAME-Pruefung) als Liste."""
    if len(names) > READ_ZONE_THRESHOLD:
        zj = await client.get_zone(zone, timeout=timeout)
        wanted = set(names)
        return [rr for rr in (zj or {}).get("rrsets") or [] if norm_name(str(rr.get("name", ""))) in wanted]
    out: list[dict] = []
    for n in names:
        found = await client.get_rrsets(zone, n, timeout=timeout)
        if isinstance(found, list):
            out.extend(found)
    return out


def _zone_matcher(zmap: dict[str, frozenset[str]]):
    """``ptr_name -> (zone, servers) | (None, ())`` ueber einen einmal gelesenen Zonen-Index (nur ``.arpa.``)."""
    arpa = {z for zs in zmap.values() for z in zs if z.endswith(".arpa.")}

    def match(ptr_name: str) -> tuple[Optional[str], tuple[str, ...]]:
        zone = find_matching_zone(ptr_name, arpa)
        if not zone:
            return None, ()
        return zone, tuple(n for n, zs in zmap.items() if zone in zs)

    return arpa, match


# =====================================================================================================================
# sync_ptrs
# =====================================================================================================================
async def sync_ptrs(
    db: AsyncSession,
    ops: list[PtrOp],
    *,
    acl_user: User,
    actor_user_id: Optional[int],
    source: dict,
    preferred_server: Optional[str] = None,
) -> list[dict]:
    """Setzt/entfernt PTRs. Wirft NIE (alle Fehler -> ``action="error"``). Reihenfolge = Reihenfolge der Ops.

    ``acl_user``: Benutzer, dessen Rechte gelten (auch Token-Scope des Requests); ``actor_user_id`` fuer das Audit;
    ``source``: ``{"action", "zone", "name", "server"}`` (ausloesende Aenderung, steht im ``PTR_SYNC``-Audit);
    ``preferred_server``: URL-Server, falls er die Reverse-Zone fuehrt (sonst erster schreibbarer Server).
    """
    ops = _dedupe(ops)
    results = [_base_result(op) for op in ops]
    try:
        await _sync(db, ops, results, acl_user=acl_user, actor_user_id=actor_user_id, source=dict(source or {}),
                    preferred_server=preferred_server)
    except Exception:  # noqa: BLE001 - PTR-Pflege darf den Forward-Write nie brechen
        logger.exception("PTR-Pflege fehlgeschlagen")
    for res in results:
        # "_pending": Aenderung vorbereitet, aber nicht bestaetigt geschrieben -> Fehler
        if res.pop("_pending", False) or res["action"] is None:
            _error(res, MSG_INTERNAL)
    return results


async def _sync(db, ops: list[PtrOp], results: list[dict], *, acl_user, actor_user_id, source, preferred_server) -> None:
    active: list[int] = []
    for i, op in enumerate(ops):
        res = results[i]
        if i >= MAX_OPS:
            _skip(res, "limit")
            continue
        try:
            res["ptr"] = reverse_name(op.ip)
        except (dns.exception.DNSException, ValueError):
            _error(res, MSG_INVALID_IP)
            continue
        if "*" in op.target:
            _skip(res, "wildcard")
            continue
        active.append(i)
    if not active:
        return

    zmap = await zone_index.writable_zone_map(db)
    arpa_names, match = _zone_matcher(zmap)
    readable_cache: dict[str, bool] = {}

    async def readable(zone: str) -> bool:
        if zone not in readable_cache:
            readable_cache[zone] = await _zone_readable(db, acl_user, zone)
        return readable_cache[zone]

    groups: dict[str, dict] = {}
    for i in active:
        res = results[i]
        ptr_name = res["ptr"]
        cz = detect_classless_zone(ptr_name, arpa_names)
        zone, servers = match(ptr_name)
        if cz and (zone is None or not ptr_name.endswith("." + cz)):
            _skip(res, "classless", classless_zone=cz if await readable(cz) else None)
            continue
        if zone is None or not servers:
            _skip(res, "no_reverse_zone")
            continue
        groups.setdefault(zone, {"servers": servers, "items": []})["items"].append(i)

    for zone, group in groups.items():
        try:
            await _sync_zone(db, zone, group["servers"], group["items"], ops, results, acl_user=acl_user,
                             actor_user_id=actor_user_id, source=source, preferred_server=preferred_server,
                             readable=readable)
        except Exception:  # noqa: BLE001 - eine Zone darf die anderen nicht mitreissen
            logger.exception("PTR-Pflege in %s fehlgeschlagen", zone)
            for i in group["items"]:
                if results[i].pop("_pending", False) or results[i]["action"] is None:
                    _error(results[i], MSG_INTERNAL)


async def _sync_zone(db, zone: str, servers: tuple[str, ...], idxs: list[int], ops: list[PtrOp], results: list[dict],
                     *, acl_user, actor_user_id, source, preferred_server, readable) -> None:
    if not await has_zone_access(db, acl_user, zone, write=True):
        show = await readable(zone)
        for i in idxs:
            _skip(results[i], "forbidden", zone=zone if show else None)
        return
    for i in idxs:
        results[i]["zone"] = zone

    primary = preferred_server if preferred_server in servers else servers[0]
    try:
        client = pdns_manager.get_client(primary)
        names: list[str] = []
        for i in idxs:
            if results[i]["ptr"] not in names:
                names.append(results[i]["ptr"])
        current = await _read_names(client, zone, names, timeout=READ_TIMEOUT)
    except (PowerDNSAPIError, ValueError) as exc:
        for i in idxs:
            _error(results[i], _err_text(exc))
        return

    # CNAME am PTR-Namen = RFC-2317-Delegation in der Elternzone
    cname_zone: dict[str, Optional[str]] = {}
    for n in names:
        snap = rrset_snapshot(current, n, "CNAME")
        if snap:
            cname_zone[n] = _classless_zone_of_cname(snap["records"][0]["content"])
    before_snap: dict[str, Optional[dict]] = {}
    working: dict[str, list[dict]] = {}
    for n in names:
        if n in cname_zone:
            continue
        snap = rrset_snapshot(current, n, "PTR")
        before_snap[n] = snap
        working[n] = [{"content": normalize_zone_name(r["content"]), "disabled": bool(r["disabled"])}
                      for r in (snap or {}).get("records") or []]

    new_ttl: dict[str, int] = {}
    touched: dict[str, list[int]] = {}
    ordered = [i for i in idxs if ops[i].op == "remove"] + [i for i in idxs if ops[i].op == "set"]
    for i in ordered:
        res, op = results[i], ops[i]
        n = res["ptr"]
        if n in cname_zone:
            cz = cname_zone[n]
            _skip(res, "classless", classless_zone=cz)
            continue
        lst = working[n]
        contents = [r["content"] for r in lst]
        if op.op == "remove":
            if op.target in contents:
                working[n] = [r for r in lst if r["content"] != op.target]
                res.update(action="removed", _pending=True)
                touched.setdefault(n, []).append(i)
            elif not lst:
                res["action"] = "unchanged"
            else:
                _skip(res, "other_target", existing=contents)
        else:
            if op.target in contents:
                res["action"] = "unchanged"
            elif not lst:
                working[n] = [{"content": op.target, "disabled": False}]
                new_ttl.setdefault(n, op.ttl)
                res.update(action="set", _pending=True)
                touched.setdefault(n, []).append(i)
            else:
                _skip(res, "conflict", existing=contents)

    rrsets: list[dict] = []
    written: list[int] = []
    for n in names:
        if n not in touched:
            continue
        before = before_snap.get(n)
        before_items = sorted((r["content"], r["disabled"]) for r in
                              ({"content": normalize_zone_name(x["content"]), "disabled": x["disabled"]}
                               for x in (before or {}).get("records") or []))
        after_items = sorted((r["content"], r["disabled"]) for r in working[n])
        if before_items == after_items:  # z. B. remove + set desselben Ziels -> nichts zu tun
            for i in touched[n]:
                results[i].pop("_pending", None)
                results[i]["action"] = "unchanged"
            continue
        if working[n]:
            rr = {"name": n, "type": "PTR", "ttl": int(before["ttl"]) if before else int(new_ttl.get(n, DEFAULT_TTL)),
                  "changetype": "REPLACE", "records": working[n]}
            if before and before.get("comments"):
                rr["comments"] = before["comments"]
        else:
            rr = {"name": n, "type": "PTR", "changetype": "DELETE"}
        rrsets.append(rr)
        written.extend(touched[n])
    if not rrsets:
        return

    keys = [(rr["name"], "PTR") for rr in rrsets]
    before_map = {k: before_snap.get(k[0]) for k in keys}
    # Bewusst require_primary=True (Default), abweichend von Plan B.5 ("PTR: require_primary=False") – Entscheidung
    # WS-W3-NACHARBEIT zu Review-Fund L-6: Scheitert der Server der Reverse-Zone, meldet die PTR-Pflege einen Fehler
    # (Audit PTR_SYNC error, Ergebnis "error") statt die Peers auf einen anderen Stand zu bringen; der Forward-Write ist
    # davon unberuehrt, die PTRs lassen sich ueber die Zone nachziehen.
    fan = await fanout.apply_rrsets(db, primary, zone, rrsets, timeout=WRITE_TIMEOUT, before_state=before_map)
    resource_name = keys[0][0] if len(keys) == 1 else zone
    extra = {"auto_ptr": True, "source": source, "primary_outcome": fan.primary_outcome}

    if not fan.primary_success:
        if fan.primary_error is not None:
            detail = _err_text(fan.primary_error)
        else:
            detail = fan.primary_status or MSG_INTERNAL
        for i in written:
            results[i].pop("_pending", None)
            _error(results[i], detail)
        details = history_details(zone, [], fanout=fan.summary, after_source=None, legacy={"type": "PTR"},
                                  extra=extra)
        await write_audit(db, "PTR_SYNC", "record", resource_name, user_id=actor_user_id, details=details,
                          status="error", error_message=detail, server_name=primary, zone_name=zone)
        return

    fallback = simulate_patch(before_map, rrsets=fan.per_server_rrsets.get(primary) or rrsets)
    after, src = await capture_after(client, zone, keys, fallback=fallback)
    if fan.primary_outcome == "verified_after_timeout":
        src = AFTER_REREAD
    changes = build_changes(before_map, after, origin=zone)
    details = history_details(zone, changes, fanout=fan.summary, after_source=src, legacy={"type": "PTR"},
                              extra=extra)
    summary = fan.summary
    for i in written:
        results[i].pop("_pending", None)
        results[i]["fanout"] = summary
    audit = await write_audit(db, "PTR_SYNC", "record", resource_name, user_id=actor_user_id, details=details,
                              server_name=primary, zone_name=zone)
    from app.services.webhook_outbox import enqueue_event

    await enqueue_event(
        db, "record.ptr_synced", actor=acl_user, zone=zone, server=primary,
        data={"server": primary, "zone": zone, **webhook_changes(changes), "source": source, "fanout": summary},
        audit_log_id=audit.id if audit else None,
    )


# =====================================================================================================================
# sync_for_changes (B.6a)
# =====================================================================================================================
def _values_of(snap: Optional[dict]) -> list[tuple[str, bool]]:
    return [(str(r.get("content", "")), bool(r.get("disabled", False))) for r in (snap or {}).get("records") or []]


def ops_for_changes(changes: Iterable[dict], *, ttl_default: int = DEFAULT_TTL) -> list[PtrOp]:
    """PTR-Ops aus Audit-v2-``changes`` (nur A/AAAA; aktive Werte vorher/nachher, TTL des Nachher-Zustands)."""
    ops: list[PtrOp] = []
    for change in changes or ():
        if not isinstance(change, dict):
            continue
        if str(change.get("type", "")).upper() not in PTR_TYPES:
            continue
        after = change.get("after")
        ttl = (after or {}).get("ttl") or ttl_default
        ops += ops_for_rrset_change(
            str(change.get("name", "")),
            _values_of(change.get("before")),
            _values_of(after) if after is not None else None,
            int(ttl),
        )
    return ops


async def sync_for_changes(
    db: AsyncSession,
    user: User,
    server_name: Optional[str],
    changes: list[dict],
    *,
    ttl_default: int = DEFAULT_TTL,
    actor_user_id: Optional[int] = None,
    action: Optional[str] = None,
    zone: Optional[str] = None,
    source: Optional[dict] = None,
) -> list[dict]:
    """PTR-Pflege fuer Record-Aenderungen im Audit-v2-Format (Bauplan B.6a).

    Deckt alle Schreibarten generisch ab: neue aktive Werte -> PTR setzen; geloeschte oder deaktivierte Werte -> PTR
    entfernen (nur wenn er auf den Namen zeigt); reine TTL-Aenderungen -> nichts. Ein ``sync_ptrs``-Aufruf.

    Optionale Zusatzangaben fuer das ``PTR_SYNC``-Audit: ``action`` (``CREATE|UPDATE|DELETE|BULK_UPDATE``), ``zone``
    (Forward-Zone) bzw. ein komplettes ``source``-Dict. Rueckgabe ``[]``, wenn keine A/AAAA-Aenderung PTRs betrifft.
    """
    ops = ops_for_changes(changes, ttl_default=ttl_default)
    if not ops:
        return []
    if source is None:
        names = sorted({str(c.get("name", "")) for c in changes or () if isinstance(c, dict)
                        and str(c.get("type", "")).upper() in PTR_TYPES})
        source = {"action": action or "BULK_UPDATE", "zone": normalize_zone_name(zone) or None,
                  "name": names[0] if len(names) == 1 else None, "server": server_name}
    return await sync_ptrs(
        db, ops, acl_user=user, actor_user_id=actor_user_id if actor_user_id is not None else getattr(user, "id", None),
        source=source, preferred_server=server_name,
    )


# =====================================================================================================================
# Lookup und Konfiguration
# =====================================================================================================================
async def lookup(db: AsyncSession, user: User, ip: str, name: Optional[str], server: Optional[str]) -> dict:
    """Vorschau fuer ``GET /ptr/lookup`` (wirft ``ValueError`` bei ungueltiger IP -> Router 422).

    Zonennamen und aktuelle PTR-Inhalte nur mit Leserecht; ``would`` nur mit ``name`` und Schreibrecht [S5].
    """
    ip_c = canonical_ip(ip)
    if ip_c is None:
        raise ValueError(MSG_INVALID_IP)
    ptr_name = reverse_name(ip_c)
    out: dict[str, Any] = {"ip": ip_c, "ptr": ptr_name, "zone": None, "status": "error", "current": None,
                           "would": None, "classless_zone": None, "detail": None}
    zmap = await zone_index.writable_zone_map(db)
    arpa_names, match = _zone_matcher(zmap)
    cz = detect_classless_zone(ptr_name, arpa_names)
    zone, servers = match(ptr_name)
    if cz and (zone is None or not ptr_name.endswith("." + cz)):
        out.update(status="classless", classless_zone=cz if await _zone_readable(db, user, cz) else None)
        return out
    if zone is None or not servers:
        out["status"] = "no_reverse_zone"
        return out
    if not await _zone_readable(db, user, zone):
        out.update(status="forbidden", detail=MSG_FORBIDDEN_ZONE)
        return out
    out["zone"] = zone
    srv = server if server in servers else servers[0]
    try:
        rrsets = await pdns_manager.get_client(srv).get_rrsets(zone, ptr_name, timeout=LOOKUP_TIMEOUT)
    except (PowerDNSAPIError, ValueError) as exc:
        out.update(status="error", detail=_err_text(exc))
        return out
    cname = rrset_snapshot(rrsets, ptr_name, "CNAME")
    if cname:
        out.update(status="classless", classless_zone=_classless_zone_of_cname(cname["records"][0]["content"]))
        return out
    snap = rrset_snapshot(rrsets, ptr_name, "PTR")
    current = [normalize_zone_name(r["content"]) for r in (snap or {}).get("records") or []]
    out["current"] = current
    if not await has_zone_access(db, user, zone, write=True):
        out.update(status="forbidden", detail=f"Kein Schreibrecht auf die Reverse-Zone {zone}")
        return out
    out["status"] = "ok"
    if name and name.strip():
        target = normalize_zone_name(name)
        if target in current:
            out["would"] = "unchanged"
        elif not current:
            out["would"] = "set"
        else:
            out["would"] = "conflict"
    return out


async def reverse_zones_available(db: AsyncSession, user: User) -> int:
    """Anzahl der ``.arpa.``-Zonen im Zonen-Index, auf die ``user`` schreiben darf (Admin: alle)."""
    names = await zone_index.all_zone_names(db, arpa_only=True)
    allowed = await writable_zone_filter(db, user)
    if allowed is None:
        return len(names)
    return len(names & allowed)
