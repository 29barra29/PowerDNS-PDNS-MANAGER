"""Vergleicht BIND-Zone-Datei mit existierendem Zonen-JSON (PowerDNS-API-Format)."""
from __future__ import annotations

import logging
from io import StringIO
from typing import Any, Dict, List, Set, Tuple

import dns.name
import dns.rdatatype
import dns.zone

logger = logging.getLogger(__name__)

RecordKey = Tuple[str, str, str]  # name, type, content normalized


def _norm_name(n: str, origin: str) -> str:
    n = (n or "").strip().lower()
    if not n:
        return origin.lower()
    o = origin.lower()
    if not o.endswith("."):
        o += "."
    if not n.endswith("."):
        n = n + "."
    return n


def _rrsets_from_bind(zone_name: str, content: str) -> List[RecordKey]:
    zname = _norm_name(zone_name, zone_name)
    o = zone_name if zone_name.endswith(".") else zone_name + "."
    origin = dns.name.from_text(o)
    z = dns.zone.from_text(
        StringIO(content),
        origin=origin,
        relativize=True,
        allow_include=False,
    )
    out: List[RecordKey] = []
    for name, node in z.nodes.items():
        for rdataset in node.rdatasets:
            rtype = dns.rdatatype.to_text(rdataset.rdtype)
            if rtype in ("NSEC", "NSEC3", "NSEC3PARAM", "RRSIG", "TYPE65534"):
                continue
            # relativisiert: vollqualifizierter Name = Teilzone + $ORIGIN
            absn = name + origin
            fq = absn.to_text(omit_final_dot=False).lower()
            if not fq.endswith("."):
                fq += "."
            for rdata in rdataset:
                c = rdata.to_text()
                out.append((_norm_name(fq, zname), rtype, c.strip()))
    return out


def _rrsets_from_pdns(z: Dict[str, Any]) -> List[RecordKey]:
    out: List[RecordKey] = []
    for rr in z.get("rrsets", []) or []:
        t = rr.get("type") or ""
        n = _norm_name(rr.get("name", ""), "")
        for rec in rr.get("records", []) or []:
            c = (rec.get("content") or "").strip()
            if not c and t not in ("NS", "MX"):
                continue
            out.append((n, t, c))
    return out


def set_from_records(recs: List[RecordKey]) -> Set[RecordKey]:
    return set(recs)


def build_import_diff(
    zone_name: str,
    bind_content: str,
    existing_zone: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """
    Liefert statistische Diff-Daten für die UI.
    *existing_zone*: Antwort von GET /zones/.../detail oder None, wenn Zonenname noch fehlt.
    """
    try:
        new_recs = _rrsets_from_bind(zone_name, bind_content)
    except Exception as e:
        logger.exception("parse zone file")
        return {
            "parse_error": str(e)[:500],
            "import_rrset_count": 0,
            "existing_rrset_count": 0,
            "would_add": [],
            "would_remove": [],
            "unchanged_count": 0,
        }
    ex_recs: List[RecordKey] = _rrsets_from_pdns(existing_zone) if existing_zone else []
    A = set_from_records(new_recs)
    B = set_from_records(ex_recs)
    add = sorted(A - B)
    rem = sorted(B - A)
    unchanged = len(A & B)
    return {
        "zone": _norm_name(zone_name, zone_name),
        "zone_exists": bool(existing_zone),
        "import_rrset_count": len(new_recs),
        "unique_import_records": len(A),
        "existing_rrset_count": len(B),
        "unchanged_count": unchanged,
        "would_add": [{"name": a[0], "type": a[1], "content": a[2]} for a in add[:200]],
        "would_add_total": len(add),
        "would_remove": [{"name": a[0], "type": a[1], "content": a[2]} for a in rem[:200]],
        "would_remove_total": len(rem),
        "parse_error": None,
    }
