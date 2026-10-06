"""Vergleicht BIND-Zone-Datei mit existierendem Zonen-JSON (PowerDNS-API-Format).

LUA/ALIAS (F15 5.7): dnspython kennt beide Typen nicht (``unknown rdatatype``). Vor dem Parsen ersetzt
``rewrite_passthrough_records`` solche Zeilen durch die RFC-3597-Generic-Schreibweise
(``TYPE65402 \\# <len> <hex>``); dnspython liest sie verlustfrei, Zeilennummern in Fehlermeldungen bleiben
gleich (mehrzeilige Eintraege werden durch eine Zeile plus Leerzeilen ersetzt). Inhalte werden fuer den
Vergleich normalisiert (LUA: ``lua_records.normalize_lua_content``, ALIAS: absoluter Name klein).
``build_import_diff`` meldet zusaetzlich ``lua_count`` und ``lua_issues`` (Strukturpruefung je LUA-Zeile).
"""
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

# PowerDNS-Typcodes der Typen, die dnspython nicht kennt (F15 5.7)
PASSTHROUGH_TYPE_CODES = {"LUA": 65402, "ALIAS": 65401}
_GENERIC_TO_PASSTHROUGH = {f"TYPE{c}": t for t, c in PASSTHROUGH_TYPE_CODES.items()}
MAX_LUA_ISSUES = 50

_TTL_RE = re.compile(r"\d+[smhdw]?(?:\d+[smhdw])*", re.I)
_CLASS_RE = re.compile(r"IN|CH|HS|CS|ANY|NONE|CLASS\d+", re.I)
_TOKEN_RE = re.compile(r"\S+")


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


@dataclass
class PassthroughLine:
    """Eine LUA-/ALIAS-Zeile der Zonendatei (``line`` = erste physische Zeile, 1-basiert)."""

    line: int
    type: str
    content: str  # normalisiert (LUA: normalize_lua_content; ALIAS: absoluter Name klein mit Punkt)


def normalize_passthrough_content(rtype: str, content: str) -> str:
    """Vergleichsform eines LUA-/ALIAS-Inhalts (wirft nie)."""
    t = (rtype or "").strip().upper()
    c = content if isinstance(content, str) else str(content or "")
    if t == "LUA":
        from app.services.lua_records import normalize_lua_content

        return normalize_lua_content(c)
    if t == "ALIAS":
        n = c.strip().lower()
        if n and not n.endswith("."):
            n += "."
        return n
    return c.strip()


def _record_type_token(ll: LogicalLine) -> Optional[Tuple[int, str]]:
    """(Token-Index, Typ gross) einer RR-Zeile: Owner (ohne fuehrenden Leerraum), dann bis zu zwei TTL-/Klassen-
    Tokens, dann der Typ. ``None`` fuer leere Zeilen und Direktiven (``$ORIGIN`` usw.)."""
    if not ll.text or ll.text.startswith("$"):
        return None
    toks = _TOKEN_RE.findall(ll.text)
    k = 0 if ll.leading_ws else 1
    skipped = 0
    while k < len(toks) and skipped < 2 and (_TTL_RE.fullmatch(toks[k]) or _CLASS_RE.fullmatch(toks[k])):
        k += 1
        skipped += 1
    if k >= len(toks):
        return None
    return k, toks[k].upper()


def _generic_rdata_text(rest: str) -> Optional[str]:
    """``\\# <len> <hex …>`` (RFC 3597) -> Text (UTF-8, Ersatzzeichen bei Fehlern); sonst ``None``."""
    parts = rest.split()
    if len(parts) < 2 or parts[0] != "\\#":
        return None
    try:
        data = bytes.fromhex("".join(parts[2:]))
    except ValueError:
        return None
    return data.decode("utf-8", "replace")


def rewrite_passthrough_records(content: str, zone_name: str) -> Tuple[str, List[PassthroughLine]]:
    """Ersetzt LUA-/ALIAS-Zeilen durch RFC-3597-Generic-RRs, damit dnspython sie liest. Wirft nie.

    Rueckgabe: (umgeschriebener Text mit gleicher Zeilenzahl, gefundene Zeilen). Zeilen mit Klammerfehler
    bleiben unveraendert (dnspython meldet den Fehler selbst). ``$ORIGIN`` wird fuer relative ALIAS-Ziele
    verfolgt; Owner, TTL und Klasse der Zeile bleiben stehen. Bereits generisch geschriebene Zeilen
    (``TYPE65402 \\# …``) bleiben stehen und zaehlen als LUA bzw. ALIAS.
    """
    text = content or ""
    lines = text.splitlines()
    found: List[PassthroughLine] = []
    try:
        origin = dns.name.from_text(_norm_name(zone_name, zone_name) or ".")
    except Exception:  # noqa: BLE001
        origin = dns.name.root
    try:
        logical = split_logical_lines(text)
    except Exception:  # noqa: BLE001 - defensiv, der Scanner wirft nicht
        return text, found
    for ll in logical:
        if ll.error:
            continue
        if ll.text.upper().startswith("$ORIGIN"):
            parts = ll.text.split()
            if len(parts) > 1:
                try:
                    origin = dns.name.from_text(parts[1], origin)
                except Exception:  # noqa: BLE001
                    pass
            continue
        hit = _record_type_token(ll)
        if hit is None:
            continue
        k, rtype = hit
        toks = list(_TOKEN_RE.finditer(ll.text))
        rest = ll.text[toks[k].end():].strip()
        if rtype in _GENERIC_TO_PASSTHROUGH:
            decoded = _generic_rdata_text(rest)
            if decoded is not None:
                t = _GENERIC_TO_PASSTHROUGH[rtype]
                found.append(PassthroughLine(ll.start, t, normalize_passthrough_content(t, decoded)))
            continue
        if rtype not in PASSTHROUGH_TYPE_CODES:
            continue
        if rtype == "ALIAS" and rest:
            try:
                rest = dns.name.from_text(rest, origin).to_text().lower()
            except Exception:  # noqa: BLE001 - PowerDNS meldet ungueltige Ziele selbst
                pass
        data = rest.encode("utf-8")
        code = PASSTHROUGH_TYPE_CODES[rtype]
        generic = f"TYPE{code} \\# {len(data)} {data.hex()}" if data else f"TYPE{code} \\# 0"
        if ll.start - 1 >= len(lines):
            continue
        lines[ll.start - 1] = ("\t" if ll.leading_ws else "") + ll.text[:toks[k].start()] + generic
        for x in range(ll.start, min(ll.end, len(lines))):
            lines[x] = ""  # Zeilennummern bleiben erhalten
        found.append(PassthroughLine(ll.start, rtype, normalize_passthrough_content(rtype, rest)))
    return "\n".join(lines) + "\n", found


_PASSTHROUGH_TOKENS = {**{t: t for t in PASSTHROUGH_TYPE_CODES}, **_GENERIC_TO_PASSTHROUGH}


def count_passthrough_records(content: str) -> Dict[str, int]:
    """``{"LUA": n, "ALIAS": m}`` einer Zonendatei fuer das Import-Gate (F15 3.6; wirft nie).

    Bewusst grosszuegig: zaehlt auch Zeilen mit Klammerfehlern, die Generic-Schreibweise ``TYPE65402`` und
    ``$GENERATE``-Vorlagen mit LUA/ALIAS – das Gate darf keinen Weg offen lassen, auf dem PowerDNS trotz
    deaktivierter Policy LUA-Records anlegt.
    """
    out = {t: 0 for t in PASSTHROUGH_TYPE_CODES}
    try:
        logical = split_logical_lines(content or "")
    except Exception:  # noqa: BLE001
        return out
    for ll in logical:
        if ll.text.upper().startswith("$GENERATE"):
            for tok in _TOKEN_RE.findall(ll.text)[2:]:
                t = _PASSTHROUGH_TOKENS.get(tok.upper())
                if t:
                    out[t] += 1
                    break
            continue
        hit = _record_type_token(ll)
        if hit is None:
            continue
        t = _PASSTHROUGH_TOKENS.get(hit[1])
        if t:
            out[t] += 1
    return out


def lua_issues(found: List[PassthroughLine]) -> List[Dict[str, Any]]:
    """Strukturfehler der LUA-Zeilen: ``[{"line", "message"}]`` (hoechstens ``MAX_LUA_ISSUES``)."""
    from app.services.lua_records import validate_lua_content

    out: List[Dict[str, Any]] = []
    for p in found:
        if p.type != "LUA":
            continue
        try:
            validate_lua_content(p.content)
        except ValueError as exc:
            out.append({"line": p.line, "message": str(exc)})
            if len(out) >= MAX_LUA_ISSUES:
                break
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


def _rrsets_from_bind(zone_name: str, content: str, *, rewritten: Optional[str] = None) -> List[RecordKey]:
    """RRs der Zonendatei; ``rewritten`` = bereits umgeschriebener Text (sonst wird hier umgeschrieben)."""
    zname = _norm_name(zone_name, zone_name)
    o = zone_name if zone_name.endswith(".") else zone_name + "."
    origin = dns.name.from_text(o)
    if rewritten is None:
        rewritten, _ = rewrite_passthrough_records(content, zone_name)
    content = rewritten
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
            rtype = _GENERIC_TO_PASSTHROUGH.get(rtype, rtype)
            # relativisiert: vollqualifizierter Name = Teilzone + $ORIGIN
            absn = name + origin
            fq = absn.to_text(omit_final_dot=False).lower()
            if not fq.endswith("."):
                fq += "."
            for rdata in rdataset:
                if rtype in PASSTHROUGH_TYPE_CODES:
                    raw = getattr(rdata, "data", None)
                    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else rdata.to_text()
                    c = normalize_passthrough_content(rtype, text)
                else:
                    # Namen im RDATA absolut (dnspython relativiert sie beim Lesen; PowerDNS liefert sie absolut).
                    # Ohne origin erschienen NS/SOA/MX/CNAME bisher immer als Unterschied (Export-Roundtrip, F15 9.2-6).
                    c = rdata.to_text(origin=origin, relativize=False).strip()
                out.append((_norm_name(fq, zname), rtype, c))
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
            if t in PASSTHROUGH_TYPE_CODES:
                c = normalize_passthrough_content(t, c)
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

    Zusaetzlich (F15 5.7, in beiden Rueckgabezweigen): ``lua_count`` (LUA-Zeilen der Datei) und
    ``lua_issues`` (``[{"line", "message"}]``, hoechstens 50; blockieren nicht – PowerDNS prueft beim Import).
    """
    rewritten, passthrough = rewrite_passthrough_records(bind_content, zone_name)
    lua_info = {
        "lua_count": sum(1 for p in passthrough if p.type == "LUA"),
        "lua_issues": lua_issues(passthrough),
    }
    try:
        new_recs = _rrsets_from_bind(zone_name, bind_content, rewritten=rewritten)
    except Exception as e:
        # Kein Traceback mit Zoneninhalt im Log; die Meldung geht an den Admin zurueck
        logger.info("Zonendatei nicht lesbar (%s)", type(e).__name__)
        return {
            "parse_error": str(e)[:500],
            "import_rrset_count": 0,
            "existing_rrset_count": 0,
            "would_add": [],
            "would_remove": [],
            "unchanged_count": 0,
            **lua_info,
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
        **lua_info,
    }
