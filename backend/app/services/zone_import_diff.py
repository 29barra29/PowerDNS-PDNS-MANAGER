"""Vergleicht BIND-Zone-Datei mit existierendem Zonen-JSON (PowerDNS-API-Format)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import StringIO
from typing import Any, Dict, List, Optional, Set, Tuple

import dns.name
import dns.rdatatype
import dns.zone

logger = logging.getLogger(__name__)

RecordKey = Tuple[str, str, str]  # name, type, content normalized


# ---------------------------------------------------------------------------
# Logische Zeilen (gemeinsamer Scanner fuer Import-Vorschau F15 und BIND-Fragment-Parser F1)
# ---------------------------------------------------------------------------
@dataclass
class LogicalLine:
    """Eine logische Zeile einer Zonendatei (Klammern ueber mehrere physische Zeilen aufgeloest)."""

    start: int  # 1-basierte erste physische Zeile
    end: int  # 1-basierte letzte physische Zeile
    text: str  # Kommentare entfernt, Klammern ausserhalb von Quotes -> " ", Zeilen mit " " verbunden, strip()
    leading_ws: bool  # erste physische Zeile beginnt mit Space/Tab (Owner vom Vorgaenger)
    error: Optional[str] = None  # z. B. "Klammer nicht geschlossen"
    disabled: bool = False  # nur mit disabled_marker=True: Zeile stammt aus ";@disabled <rest>"


_DISABLED_MARKER_RE = re.compile(r"^\s*;@disabled\s+(.*)$", re.I)
ERR_PAREN_UNCLOSED = "Klammer nicht geschlossen"
ERR_PAREN_UNOPENED = "Schließende Klammer ohne öffnende Klammer"


def split_logical_lines(content: str, *, disabled_marker: bool = False) -> List[LogicalLine]:
    """Zerlegt eine Zonendatei in logische Zeilen.

    Zeichenweise: ``"`` schaltet den Quote-Modus um, ``\\`` maskiert das folgende Zeichen
    (auch ``\\"`` und ``\\;``), ``;`` ausserhalb von Quotes beendet die physische Zeile,
    ``(``/``)`` ausserhalb von Quotes aendern die Klammertiefe und werden durch ein
    Leerzeichen ersetzt; solange die Tiefe > 0 ist, werden Folgezeilen angehaengt. Ein
    offener Quote endet mit der physischen Zeile. EOF mit offener Klammer -> ``error``
    "Klammer nicht geschlossen" (Zeile = Start). Leere logische Zeilen entfallen.

    ``disabled_marker=True`` (F1-Text-Editor): Zeilen der Form ``;@disabled <rest>`` am
    Anfang einer logischen Zeile werden als ``<rest>`` mit ``disabled=True`` geliefert
    statt als Kommentar verworfen.
    """
    phys = (content or "").splitlines()
    out: List[LogicalLine] = []
    depth = 0
    parts: List[str] = []
    start = 0
    leading_ws = False
    disabled = False
    error: Optional[str] = None

    for idx, line in enumerate(phys, start=1):
        if depth == 0:
            disabled = False
            if disabled_marker:
                m = _DISABLED_MARKER_RE.match(line)
                if m:
                    line = m.group(1)
                    disabled = True
            start = idx
            leading_ws = line[:1] in (" ", "\t")
            parts = []
            error = None
        buf: List[str] = []
        in_quote = False
        i = 0
        n = len(line)
        while i < n:
            c = line[i]
            if c == "\\":
                buf.append(line[i:i + 2])
                i += 2
                continue
            if c == '"':
                in_quote = not in_quote
                buf.append(c)
                i += 1
                continue
            if not in_quote:
                if c == ";":
                    break
                if c == "(":
                    depth += 1
                    buf.append(" ")
                    i += 1
                    continue
                if c == ")":
                    if depth > 0:
                        depth -= 1
                    elif error is None:
                        error = ERR_PAREN_UNOPENED
                    buf.append(" ")
                    i += 1
                    continue
            buf.append(c)
            i += 1
        parts.append("".join(buf))
        if depth == 0:
            text = " ".join(parts).strip()
            if text:
                out.append(LogicalLine(start, idx, text, leading_ws, error, disabled))
            parts = []

    if depth > 0:
        text = " ".join(parts).strip()
        out.append(LogicalLine(start, len(phys), text, leading_ws, ERR_PAREN_UNCLOSED, disabled))
    return out


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
