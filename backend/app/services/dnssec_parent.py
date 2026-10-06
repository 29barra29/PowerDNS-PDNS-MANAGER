"""DNSSEC Teil B: DS der Elternzone und DNSKEY auf den autoritativen Nameservern (F4 3.13, 5.9; Plan WS-F4-C [D10]).

Beide Pruefungen fragen per DNS ausschliesslich Ziele ab, die der Admin in den Propagations-Einstellungen (F12)
freigegeben hat; Konfiguration nur ueber ``propagation.load_settings`` (keine eigenen Einstellungen in F4):

- **Elternzone** (``parent_ds_report``): DS-RRset der Zone bei den oeffentlichen Resolvern (RD=1), nur wenn
  ``propagation_enabled`` an ist und mindestens ein Resolver eingetragen ist. Abgleich mit den eigenen
  SEP-Schluesseln ueber das Tupel ``(key_tag, algorithm, digest_type, digest_hex)``; eigene DS kommen aus der
  PowerDNS-Antwort (``ds``) und werden zusaetzlich aus dem DNSKEY berechnet (SHA-1/SHA-256/SHA-384), damit der
  Abgleich auch ohne ``ds``-Feld funktioniert.
- **DNSKEY** (``check_dnskey_on_ns``/``dnskey_report``): DNSKEY-RRset bei jedem autoritativen Nameserver der Zone
  (Apex-NS aus PowerDNS, Glue aus der Zone bevorzugt, sonst System-Resolver; RD=0), nur wenn
  ``propagation_enabled`` und ``propagation_check_authoritative`` an sind. Ein Nameserver gilt als ``ok``, wenn
  jede seiner abgefragten Adressen autoritativ antwortet und alle erwarteten Key-Tags liefert.

Schutz wie F12 7.2: nur IP-Literale bzw. Adressen aus Zonendaten/NS-Aufloesung, ``propagation.target_allowed``
(keine Loopback-/Link-Local-/Multicast-/reservierten Ziele, IPv6 nur nach Opt-in), hoechstens
``propagation.MAX_NS_TARGETS`` Adressen, Zeitbudget je Pruefung (Elternzone 5 s, DNSKEY 8 s). Rate-Limit und
globale Parallelitaet (``propagation.consume_rate``/``dns_slot``) setzt der Router, der auch die DB-Session vor
der Netzphase freigibt (L6). Die Funktionen hier werfen keine Netzwerkfehler – jede Zeile traegt ihren Status.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

import dns.dnssec
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype

from app.core.timeutil import iso_utc, utcnow
from app.services import dnssec_logic as logic
from app.services import propagation as prop
from app.services.dnssec_parse import compute_key_tag, normalize_dnskey, parse_ds_line

logger = logging.getLogger(__name__)

DNS_PORT = 53  # Tests setzen den Port eines lokalen UDP-Servers
PARENT_DS_TIMEOUT = 3.0  # je Resolver (Spec 3.13)
PARENT_DS_TOTAL = 5.0  # Gesamtlaufzeit der Elternzonen-Pruefung (Spec 3.13)
DNSKEY_TIMEOUT = 3.0  # je Nameserver-Adresse
DNSKEY_TOTAL = 8.0  # Gesamtlaufzeit der DNSKEY-Pruefung (wie der Propagations-Check)
NS_RESOLVE_TIMEOUT = 2.0
DS_DIGESTS = {1: "SHA1", 2: "SHA256", 4: "SHA384"}

PARENT_ERROR_TEXTS = {
    "timeout": "Zeitüberschreitung",
    "servfail": "Serverfehler (SERVFAIL)",
    "network_error": "Netzwerkfehler",
    "bad_response": "Ungültige DNS-Antwort",
}

DsTuple = tuple[int, int, int, str]


# ---------------------------------------------------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------------------------------------------------
def parent_check_enabled(settings: prop.PropagationSettings) -> bool:
    return bool(settings.enabled and settings.resolvers)


def dnskey_check_enabled(settings: prop.PropagationSettings) -> bool:
    return bool(settings.enabled and settings.check_authoritative)


def resolver_label(ip: str) -> Optional[str]:
    return prop.KNOWN_RESOLVER_LABELS.get(ip)


def _resolver_display(ip: str) -> str:
    label = resolver_label(ip)
    return f"{label} ({ip})" if label else ip


# ---------------------------------------------------------------------------------------------------------------------
# Eigene DS (nur SEP-Schluessel)
# ---------------------------------------------------------------------------------------------------------------------
def ds_tuple(line: str) -> Optional[DsTuple]:
    """``(key_tag, algorithm, digest_type, digest_hex)`` einer DS-Zeile oder ``None`` (unlesbar)."""
    p = parse_ds_line(line if isinstance(line, str) else "")
    if p.get("error"):
        return None
    return int(p["key_tag"]), int(p["algorithm"]), int(p["digest_type"]), str(p["digest_hex"]).lower()


def _computed_ds(zone_norm: str, dnskey: Optional[str]) -> set[DsTuple]:
    s = normalize_dnskey(dnskey)
    if not s:
        return set()
    try:
        rdata = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.DNSKEY, s)
        name = dns.name.from_text(zone_norm)
    except Exception:  # noqa: BLE001 - kaputter DNSKEY aus der API bricht die Pruefung nicht
        return set()
    out: set[DsTuple] = set()
    # nur Vergleich, keine Validierung: auch SHA-1-DS berechnen (Standard-Policy von dnspython verbietet das)
    policy = getattr(dns.dnssec, "allow_all_policy", None)
    kwargs = {"policy": policy} if policy is not None else {}
    for digest in DS_DIGESTS.values():
        try:
            t = ds_tuple(dns.dnssec.make_ds(name, rdata, digest, **kwargs).to_text())
        except Exception:  # noqa: BLE001 - z. B. Digest im Build nicht verfuegbar
            t = None
        if t:
            out.add(t)
    return out


def own_sep_ds(zone_norm: str, raw_keys: Iterable[dict]) -> dict[int, dict]:
    """``{key_id: {"key_tag": int|None, "tuples": set}}`` aller SEP-Schluessel (KSK/CSK) der Zone."""
    out: dict[int, dict] = {}
    for raw in raw_keys or []:
        if not isinstance(raw, dict):
            continue
        view = logic.key_view_from_pdns(raw)
        if not view.is_sep:
            continue
        tuples = {t for t in (ds_tuple(x) for x in (raw.get("ds") or []) if isinstance(x, str)) if t}
        tuples |= _computed_ds(zone_norm, raw.get("dnskey"))
        out[view.id] = {"key_tag": compute_key_tag(raw.get("dnskey")), "tuples": tuples}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Elternzone
# ---------------------------------------------------------------------------------------------------------------------
def _parent_row(ip: str, ans: Optional[prop.DnsAnswer], skip_reason: Optional[str] = None) -> dict:
    row = {"resolver": ip, "label": resolver_label(ip), "status": "error", "ds": [], "key_tags": [], "error": None}
    if skip_reason:
        row.update(status="skipped", error=prop.ERROR_TEXTS.get(skip_reason, skip_reason))
        return row
    if ans is None:
        row.update(status="timeout", error=PARENT_ERROR_TEXTS["timeout"])
        return row
    if ans.error_code:
        status = "timeout" if ans.error_code == "timeout" else "error"
        row.update(status=status, error=PARENT_ERROR_TEXTS.get(ans.error_code, ans.error_code))
        return row
    rcode = (ans.rcode or "").upper()
    if rcode == "NXDOMAIN":
        row["status"] = "nxdomain"
    elif rcode == "SERVFAIL":
        row.update(status="servfail", error=PARENT_ERROR_TEXTS["servfail"])
    elif rcode != "NOERROR":
        row["error"] = f"Antwort {rcode or '?'}"[:200]
    elif not ans.values:
        row["status"] = "nodata"
    else:
        row["status"] = "ok"
        row["ds"] = list(ans.values)
        row["key_tags"] = sorted({t[0] for t in (ds_tuple(v) for v in ans.values) if t})
    return row


async def _gather_budget(jobs: dict, total: float) -> dict:
    """Fuehrt ``{schluessel: coroutine}`` parallel aus; was nach ``total`` Sekunden offen ist, ergibt ``None``."""
    tasks = {k: asyncio.ensure_future(c) for k, c in jobs.items()}
    if not tasks:
        return {}
    done, pending = await asyncio.wait(tasks.values(), timeout=total)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    out = {}
    for k, t in tasks.items():
        if t in done and not t.cancelled() and t.exception() is None:
            out[k] = t.result()
        else:
            if t in done and not t.cancelled() and t.exception() is not None:
                logger.warning("DNS-Pruefung fehlgeschlagen (%s): %s", k, type(t.exception()).__name__)
            out[k] = None
    return out


async def query_parent_ds(zone_norm: str, resolvers: Iterable[str], timeout: float = PARENT_DS_TIMEOUT, *,
                          ipv6: bool = False, total: float = PARENT_DS_TOTAL) -> list[dict]:
    """DS-Abfrage der Zone bei jedem Resolver (parallel, RD=1). Zeilen ``ParentDsResolverResult`` in Listenreihenfolge.

    Status: ``ok`` (DS vorhanden), ``nodata`` (keine DS), ``nxdomain``, ``timeout``, ``servfail``, ``error``
    (Netzwerk/sonstiger RCODE, Text gekuerzt), ``skipped`` (Adresse nicht erlaubt bzw. IPv6 aus).
    """
    ips = list(dict.fromkeys(str(x).strip() for x in resolvers or [] if str(x).strip()))
    skipped = {ip: prop.target_allowed(ip, ipv6) for ip in ips}
    jobs = {
        ip: prop.query_dns(zone_norm, "DS", ip, timeout, recursion=True, origin=zone_norm, port=DNS_PORT)
        for ip in ips if not skipped[ip]
    }
    answers = await _gather_budget(jobs, total)
    return [_parent_row(ip, answers.get(ip), skipped[ip]) for ip in ips]


def match_parent_ds(zone_norm: str, raw_keys: Iterable[dict], rows: list[dict]) -> dict:
    """Abgleich DS bei den Eltern <-> eigene SEP-Schluessel: ``keys``, ``unknown_tags``, ``any_visible``."""
    own = own_sep_ds(zone_norm, raw_keys)
    own_all: set[DsTuple] = set().union(*(o["tuples"] for o in own.values())) if own else set()
    answered = [r for r in rows if r["status"] in ("ok", "nodata", "nxdomain")]
    parent_sets = {r["resolver"]: {t for t in (ds_tuple(v) for v in r["ds"]) if t} for r in answered}
    keys: dict[str, dict] = {}
    for key_id, info in sorted(own.items()):
        visible = [ip for ip, ts in parent_sets.items() if ts & info["tuples"]]
        missing = [ip for ip in parent_sets if ip not in visible]
        keys[str(key_id)] = {"key_tag": info["key_tag"], "visible_on": visible, "missing_on": missing}
    unknown = sorted({t[0] for ts in parent_sets.values() for t in ts if t not in own_all})
    return {"keys": keys, "unknown_tags": unknown, "any_visible": any(k["visible_on"] for k in keys.values())}


def empty_parent_report(zone_norm: str) -> dict:
    return {"zone": zone_norm, "enabled": False, "resolvers": [], "keys": {}, "unknown_tags": [],
            "any_visible": False, "checked_at": None}


async def parent_ds_report(zone_norm: str, raw_keys: Iterable[dict], settings: prop.PropagationSettings) -> dict:
    """``ParentDsResponse`` als dict; ``enabled=False`` (ohne Abfrage), wenn die Pruefung nicht freigegeben ist."""
    if not parent_check_enabled(settings):
        return empty_parent_report(zone_norm)
    keys = list(raw_keys or [])
    rows = await query_parent_ds(zone_norm, settings.resolvers, PARENT_DS_TIMEOUT, ipv6=settings.ipv6)
    return {"zone": zone_norm, "enabled": True, "resolvers": rows, **match_parent_ds(zone_norm, keys, rows),
            "checked_at": iso_utc(utcnow())}


def visible_resolvers(report: dict) -> list[str]:
    """Resolver (Anzeigeform), bei denen der DS mindestens eines eigenen Schluessels sichtbar ist."""
    seen: list[str] = []
    for k in (report.get("keys") or {}).values():
        for ip in k.get("visible_on") or []:
            if ip not in seen:
                seen.append(ip)
    return [_resolver_display(ip) for ip in seen]


# ---------------------------------------------------------------------------------------------------------------------
# DNSKEY auf den autoritativen Nameservern
# ---------------------------------------------------------------------------------------------------------------------
def _address_row(ip: Optional[str], ans: Optional[prop.DnsAnswer], skip_reason: Optional[str] = None) -> dict:
    row = {"ip": ip, "status": "error", "key_tags": [], "error": None}
    if skip_reason:
        status = "ns_unresolvable" if skip_reason == "ns_unresolvable" else "skipped"
        row.update(status=status, error=prop.ERROR_TEXTS.get(skip_reason, skip_reason))
        return row
    if ans is None:
        row.update(status="timeout", error=prop.ERROR_TEXTS["deadline"])
        return row
    if ans.error_code:
        status = ans.error_code if ans.error_code in ("timeout", "network_error", "bad_response") else "error"
        row.update(status=status, error=prop.ERROR_TEXTS.get(ans.error_code, ans.error_code))
        return row
    rcode = (ans.rcode or "").upper()
    if rcode == "NOERROR":
        if not ans.aa:
            row.update(status="not_authoritative", error=prop.ERROR_TEXTS["not_authoritative"])
        else:
            row.update(status="ok", key_tags=sorted(set(ans.key_tags or [])))
        return row
    status = {"REFUSED": "refused", "SERVFAIL": "servfail", "NXDOMAIN": "nxdomain"}.get(rcode, "error")
    row.update(status=status, error=prop.ERROR_TEXTS.get(status) or f"Antwort {rcode or '?'}")
    return row


def _evaluate_ns(addresses: list[dict], expected: set[int]) -> dict:
    checked = [a for a in addresses if a["status"] not in ("skipped",)]
    ok_rows = [a for a in addresses if a["status"] == "ok"]
    serves: set[int] = set(ok_rows[0]["key_tags"]) if ok_rows else set()
    for a in ok_rows[1:]:
        serves &= set(a["key_tags"])
    all_answered_ok = bool(ok_rows) and all(a["status"] == "ok" for a in checked)
    ok = all_answered_ok and expected <= serves
    error = next((a["error"] for a in addresses if a["status"] != "ok" and a["error"]), None)
    if not addresses:
        error = prop.ERROR_TEXTS["ns_unresolvable"]
    return {"ok": ok, "serves_key_tags": sorted(serves), "missing_tags": sorted(expected - serves),
            "addresses": addresses, "error": error}


async def check_dnskey_on_ns(zone_norm: str, zone_json: dict, expected_tags: Iterable[int],
                             settings: prop.PropagationSettings, *, timeout: float = DNSKEY_TIMEOUT,
                             total: float = DNSKEY_TOTAL) -> tuple[dict[str, dict], bool]:
    """DNSKEY-Abfrage (RD=0) bei allen Apex-NS der Zone -> ``({ns: DnskeyCheckNameserver}, truncated)``.

    ``zone_json``: PowerDNS-Zone (Apex-NS und Glue). Gleiche Adressen werden nur einmal gefragt.
    """
    expected = {int(t) for t in expected_tags or []}
    ref = prop.extract_reference(zone_json or {}, zone_norm)
    nameservers: list[str] = ref["nameservers"]
    glue: dict[str, list[str]] = ref["glue"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total

    async def resolve(ns: str) -> tuple[list[str], Optional[str]]:
        if ns in glue:
            return list(glue[ns]), None
        budget = max(0.05, min(NS_RESOLVE_TIMEOUT, deadline - loop.time()))
        return await prop.resolve_ns_addresses(ns, ipv6=settings.ipv6, timeout=budget)

    resolved = await _gather_budget({ns: resolve(ns) for ns in nameservers}, max(0.05, deadline - loop.time()))
    plan: dict[str, list[tuple[Optional[str], Optional[str]]]] = {}
    ips: list[str] = []
    truncated = False
    for ns in nameservers:
        got = resolved.get(ns)
        addrs, err = got if got is not None else ([], "ns_unresolvable")
        entries: list[tuple[Optional[str], Optional[str]]] = []
        if err or not addrs:
            entries.append((None, err or "ns_unresolvable"))
        for ip in addrs:
            if ip not in ips:
                if len(ips) >= prop.MAX_NS_TARGETS:
                    truncated = True
                    continue
                ips.append(ip)
            entries.append((ip, prop.target_allowed(ip, settings.ipv6)))
        plan[ns] = entries
    to_query = {ip for entries in plan.values() for ip, skip in entries if ip and not skip and ip in ips}
    sem = asyncio.Semaphore(prop.DNS_CONCURRENCY)

    async def one(ip: str) -> prop.DnsAnswer:
        async with sem:
            budget = max(0.05, min(timeout, deadline - loop.time()))
            return await prop.query_dns(zone_norm, "DNSKEY", ip, budget, recursion=False, origin=zone_norm,
                                        port=DNS_PORT)

    answers = await _gather_budget({ip: one(ip) for ip in sorted(to_query)}, max(0.05, deadline - loop.time()))
    out: dict[str, dict] = {}
    for ns, entries in plan.items():
        rows = [_address_row(ip, answers.get(ip) if ip else None, skip) for ip, skip in entries]
        result = _evaluate_ns(rows, expected)
        if not rows and truncated:
            result["error"] = "Nicht geprüft (zu viele Nameserver-Adressen)"
        out[ns] = result
    return out, truncated


def empty_dnskey_report(zone_norm: str, expected_tags: Iterable[int] = ()) -> dict:
    return {"zone": zone_norm, "enabled": False, "expected_tags": sorted({int(t) for t in expected_tags}),
            "nameservers": {}, "all_ok": False, "truncated": False, "checked_at": None}


def published_key_tags(raw_keys: Iterable[dict]) -> list[int]:
    """Key-Tags aller veroeffentlichten Schluessel (Standard-Erwartung der DNSKEY-Pruefung)."""
    tags = set()
    for raw in raw_keys or []:
        if isinstance(raw, dict) and logic.key_view_from_pdns(raw).published_eff:
            tag = compute_key_tag(raw.get("dnskey"))
            if tag is not None:
                tags.add(tag)
    return sorted(tags)


async def dnskey_report(zone_norm: str, zone_json: dict, expected_tags: Iterable[int],
                        settings: prop.PropagationSettings) -> dict:
    """``DnskeyCheckResponse`` als dict; ``enabled=False`` (ohne Abfrage), wenn nicht freigegeben."""
    expected = sorted({int(t) for t in expected_tags or []})
    if not dnskey_check_enabled(settings):
        return empty_dnskey_report(zone_norm, expected)
    nameservers, truncated = await check_dnskey_on_ns(zone_norm, zone_json, expected, settings)
    all_ok = bool(nameservers) and all(ns["ok"] for ns in nameservers.values()) and not truncated
    return {"zone": zone_norm, "enabled": True, "expected_tags": expected, "nameservers": nameservers,
            "all_ok": all_ok, "truncated": truncated, "checked_at": iso_utc(utcnow())}
