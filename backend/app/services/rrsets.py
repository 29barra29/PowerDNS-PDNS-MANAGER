"""RRset-Primitive: Snapshot, Kanonisierung, Fingerprints (F1 5.1, Plan B.6).

Einzige Quelle fuer Snapshot/Kanonisierung von RRsets – Bulk (F1), Historie/Rollback
(F7), DynDNS/PTR (F9/F11) und Propagation (F12) bauen darauf auf. Reine Funktionen
ohne I/O.

Snapshot-Format: ``{"ttl": int, "records": [{"content", "disabled"}] (sortiert nach
(content, disabled)), "comments": [...]}``; ``None`` = RRset fehlt bzw. hat keine Records.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

import dns.exception
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype

from app.core.names import normalize_rr_name

DNSSEC_AUTO_TYPES = frozenset({"RRSIG", "NSEC", "NSEC3", "NSEC3PARAM", "TYPE65534"})
NAME_CONTENT_TYPES = frozenset({"CNAME", "NS", "PTR", "DNAME", "ALIAS", "MX", "SRV"})  # Inhalt case-insensitiv vergleichen
PASSTHROUGH_TYPES = frozenset({"LUA", "ALIAS"})  # dnspython kennt sie nicht (UnknownRdatatype)
ZONE_FINGERPRINT_EXCLUDE = ("SOA", "DNSKEY", "RRSIG", "NSEC", "NSEC3", "NSEC3PARAM", "CDS", "CDNSKEY")

RRKey = tuple[str, str]  # (Name lower + Punkt, TYPE)


def norm_name(name: str) -> str:
    """strip, lower, Trailing-Dot (wie ``core.names.normalize_rr_name``)."""
    return normalize_rr_name(name)


def _origin(origin: str) -> dns.name.Name:
    o = norm_name(origin)
    return dns.name.from_text(o) if o else dns.name.root


def canonical_content(rtype: str, content: str, origin: str) -> str:
    """Kanonische Darstellung eines Record-Inhalts; ``ValueError`` bei ungueltigem Inhalt.

    - LUA: ``lua_records.validate_lua_content`` (normalisiert, Fehler -> ValueError)
    - ALIAS: absoluter Name (relativ zu ``origin``)
    - sonst dnspython ``rdata.from_text(...).to_text()`` (z. B. AAAA komprimiert, TXT in Quotes,
      relative Namen absolut)
    """
    t = (rtype or "").strip().upper()
    c = content if isinstance(content, str) else str(content or "")
    try:
        if t == "LUA":
            from app.services.lua_records import validate_lua_content

            return validate_lua_content(c)
        if t == "ALIAS":
            return dns.name.from_text(c.strip(), origin=_origin(origin)).to_text()
        rdtype = dns.rdatatype.from_text(t)
        rd = dns.rdata.from_text(dns.rdataclass.IN, rdtype, c, origin=_origin(origin), relativize=False)
        return rd.to_text()
    except ValueError:
        raise
    except Exception as exc:  # dns.exception.SyntaxError, UnknownRdatatype, ...
        raise ValueError(str(exc) or type(exc).__name__) from exc


def content_key(rtype: str, content: str, origin: str) -> str:
    """Vergleichsschluessel eines Inhalts: kanonisch, sonst whitespace-normalisiert; Namen-Typen klein."""
    t = (rtype or "").strip().upper()
    c = content if isinstance(content, str) else str(content or "")
    if t == "LUA":
        from app.services.lua_records import normalize_lua_content

        return normalize_lua_content(c)
    try:
        key = canonical_content(t, c, origin)
    except ValueError:
        key = " ".join(c.split())
    if t in NAME_CONTENT_TYPES:
        key = key.lower()
    return key


def _snapshot_from_rr(rr: dict) -> Optional[dict]:
    records = [
        {"content": str(r.get("content", "")), "disabled": bool(r.get("disabled", False))}
        for r in (rr.get("records") or [])
    ]
    if not records:
        return None
    records.sort(key=lambda r: (r["content"], r["disabled"]))
    return {
        "ttl": int(rr.get("ttl") or 0),
        "records": records,
        "comments": list(rr.get("comments") or []),
    }


def rrset_snapshot(zone_json: Optional[dict], name: str, rtype: str) -> Optional[dict]:
    """Snapshot eines RRsets aus einer PowerDNS-Zonenantwort (Name/Typ case-insensitiv).

    ``zone_json`` darf auch eine Liste von RRsets sein (z. B. Ergebnis von ``get_rrsets``).
    """
    n = norm_name(name)
    t = (rtype or "").strip().upper()
    rrsets = zone_json if isinstance(zone_json, list) else ((zone_json or {}).get("rrsets") or [])
    for rr in rrsets:
        if norm_name(str(rr.get("name", ""))) == n and str(rr.get("type", "")).upper() == t:
            return _snapshot_from_rr(rr)
    return None


def index_rrsets(zone_json: Optional[dict]) -> dict[RRKey, dict]:
    """``{(name, TYPE): snapshot}`` fuer alle RRsets mit mindestens einem Record."""
    out: dict[RRKey, dict] = {}
    for rr in (zone_json or {}).get("rrsets") or []:
        snap = _snapshot_from_rr(rr)
        if snap is None:
            continue
        out[(norm_name(str(rr.get("name", ""))), str(rr.get("type", "")).upper())] = snap
    return out


def _digest(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def snapshot_fingerprint(snap: Optional[dict], rtype: str, origin: str) -> str:
    """``"absent"`` oder 32 Hex-Zeichen; reihenfolge-unabhaengig, Kommentare gehen nicht ein."""
    if snap is None:
        return "absent"
    records = sorted(
        [content_key(rtype, r.get("content", ""), origin), bool(r.get("disabled", False))]
        for r in (snap.get("records") or [])
    )
    return _digest({"ttl": int(snap.get("ttl") or 0), "records": records})


def snapshot_from_rrset_payload(rr: dict) -> Optional[dict]:
    """Erwarteter Zustand nach einem PATCH-Eintrag (REPLACE -> Snapshot, DELETE/leer -> None)."""
    if str(rr.get("changetype", "REPLACE")).upper() == "DELETE":
        return None
    return _snapshot_from_rr(rr)


def zone_fingerprint(zone_json: Optional[dict], *, exclude_types: Iterable[str] = ZONE_FINGERPRINT_EXCLUDE) -> str:
    """Inhalts-Fingerprint einer ganzen Zone (fuer den Peer-Vergleich der Propagation [D9]).

    SOA und DNSSEC-Typen gehen standardmaessig nicht ein (Serial/Signaturen unterscheiden
    sich bei getrennten Backends legitim). Kommentare gehen nicht ein.
    """
    excl = {t.upper() for t in exclude_types}
    origin = norm_name((zone_json or {}).get("name") or "")
    items = []
    for (name, rtype), snap in index_rrsets(zone_json).items():
        if rtype in excl:
            continue
        items.append([name, rtype, snapshot_fingerprint(snap, rtype, origin)])
    items.sort()
    return _digest(items)
