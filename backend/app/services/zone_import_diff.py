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
    """(Token-Index, Typ gross) einer RR-Zeile: Owner (ohne fuehrenden Leerraum), dann beliebig viele TTL-/Klassen-
    Tokens, dann der Typ. ``None`` fuer leere Zeilen und Direktiven (``$ORIGIN`` usw.).

    PowerDNS ueberspringt beliebig viele ``IN`` (``www 300 IN IN LUA …`` ist dort ein LUA-Record); frueher zaehlte
    der Scanner hoechstens zwei Tokens und meldete dann ``IN`` als Typ (Review Welle 3).
    """
    if not ll.text or ll.text.startswith("$"):
        return None
    toks = _TOKEN_RE.findall(ll.text)
    k = 0 if ll.leading_ws else 1
    while k < len(toks) and (_TTL_RE.fullmatch(toks[k]) or _CLASS_RE.fullmatch(toks[k])):
        k += 1
    if k >= len(toks):
        return None
    return k, toks[k].upper()


# Einzelne UTF-16-Surrogate (aus JSON-Escapes wie "\\ud800") lassen sich nicht als UTF-8 kodieren (W3-L4)
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def lone_surrogate_line(content: Optional[str]) -> Optional[int]:
    """Erste (physische) Zeile mit einem einzelnen UTF-16-Surrogat oder ``None``. Fuer eine 400 statt 500 (W3-L4)."""
    if not content:
        return None
    m = _LONE_SURROGATE_RE.search(content)
    if m is None:
        return None
    return content.count("\n", 0, m.start()) + 1


def _wire_text(rtype: str, data: bytes) -> Optional[str]:
    """RFC-3597-Daten im Wire-Format von PowerDNS -> Praesentationstext; ``None``, wenn es kein Wire-Format ist (W3-L5).

    LUA (65402): 2 Byte Ziel-Typ + Zeichenketten mit Laengenbyte -> ``<Typ> "<Code>"``. ALIAS (65401): Domainname im
    Wire-Format. Die Panel-eigene Schreibweise (Praesentationstext als Hex, siehe ``rewrite_passthrough_records``)
    beginnt mit einem ASCII-Buchstaben (Typ-Code >= 0x4100, kein bekannter Typ) und faellt hier durch.
    """
    if rtype == "LUA":
        if len(data) < 3:
            return None
        mnemonic = dns.rdatatype.to_text(int.from_bytes(data[:2], "big"))
        if mnemonic.startswith("TYPE"):
            return None
        parts: List[bytes] = []
        i = 2
        while i < len(data):
            n = data[i]
            i += 1
            if i + n > len(data):
                return None
            parts.append(data[i:i + n])
            i += n
        try:
            code = b"".join(parts).decode("utf-8")
        except UnicodeDecodeError:
            return None
        return f'{mnemonic} "{code}"'
    if rtype == "ALIAS":
        try:
            name, used = dns.name.from_wire(data, 0)
        except Exception:  # noqa: BLE001 - kein gueltiger Name im Wire-Format
            return None
        if used != len(data):
            return None
        return name.to_text()
    return None


def _generic_rdata(rest: str, rtype: str) -> Optional[Tuple[str, bool]]:
    """``\\# <len> <hex …>`` (RFC 3597) -> (Text, war Wire-Format) bzw. ``None``.

    Wire-Format (z. B. aus einem PowerDNS-Export) wird wie PowerDNS gelesen (W3-L5); sonst die Panel-Schreibweise:
    UTF-8-Text, Ersatzzeichen bei Fehlern.
    """
    parts = rest.split()
    if len(parts) < 2 or parts[0] != "\\#":
        return None
    try:
        data = bytes.fromhex("".join(parts[2:]))
    except ValueError:
        return None
    wire = _wire_text(rtype, data)
    if wire is not None:
        return wire, True
    return data.decode("utf-8", "replace"), False


def _generic_line(code: int, text: str) -> str:
    """Panel-Schreibweise fuer dnspython: ``TYPE<code> \\# <len> <hex(UTF-8-Text)>``. Wirft nie (W3-L4)."""
    data = text.encode("utf-8", "surrogatepass")
    return f"TYPE{code} \\# {len(data)} {data.hex()}" if data else f"TYPE{code} \\# 0"


def rewrite_passthrough_records(content: str, zone_name: str) -> Tuple[str, List[PassthroughLine]]:
    """Ersetzt LUA-/ALIAS-Zeilen durch RFC-3597-Generic-RRs, damit dnspython sie liest. Wirft nie.

    Rueckgabe: (umgeschriebener Text mit gleicher Zeilenzahl, gefundene Zeilen). Zeilen mit Klammerfehler
    bleiben unveraendert (dnspython meldet den Fehler selbst). ``$ORIGIN`` wird fuer relative ALIAS-Ziele
    verfolgt; Owner, TTL und Klasse der Zeile bleiben stehen. Bereits generisch geschriebene Zeilen
    (``TYPE65402 \\# …``) zaehlen als LUA bzw. ALIAS; in der Panel-Schreibweise bleiben sie stehen, echtes
    Wire-Format (z. B. aus einem PowerDNS-Export) wird in die Panel-Schreibweise umgeschrieben (W3-L5).
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
            t = _GENERIC_TO_PASSTHROUGH[rtype]
            got = _generic_rdata(rest, t)
            if got is None:
                continue
            decoded, wire = got
            found.append(PassthroughLine(ll.start, t, normalize_passthrough_content(t, decoded)))
            if wire and ll.start - 1 < len(lines):
                # Wire-Format in die Panel-Schreibweise bringen, damit die Vorschau denselben Text vergleicht (W3-L5)
                lines[ll.start - 1] = (("\t" if ll.leading_ws else "") + ll.text[:toks[k].start()]
                                       + _generic_line(PASSTHROUGH_TYPE_CODES[t], decoded))
                for x in range(ll.start, min(ll.end, len(lines))):
                    lines[x] = ""
            continue
        if rtype not in PASSTHROUGH_TYPE_CODES:
            continue
        if rtype == "ALIAS" and rest:
            try:
                rest = dns.name.from_text(rest, origin).to_text().lower()
            except Exception:  # noqa: BLE001 - PowerDNS meldet ungueltige Ziele selbst
                pass
        generic = _generic_line(PASSTHROUGH_TYPE_CODES[rtype], rest)
        if ll.start - 1 >= len(lines):
            continue
        lines[ll.start - 1] = ("\t" if ll.leading_ws else "") + ll.text[:toks[k].start()] + generic
        for x in range(ll.start, min(ll.end, len(lines))):
            lines[x] = ""  # Zeilennummern bleiben erhalten
        found.append(PassthroughLine(ll.start, rtype, normalize_passthrough_content(rtype, rest)))
    return "\n".join(lines) + "\n", found


_PASSTHROUGH_TOKENS = {**{t: t for t in PASSTHROUGH_TYPE_CODES}, **_GENERIC_TO_PASSTHROUGH}


def _passthrough_hits(content: str) -> List[Tuple[int, str]]:
    """``[(erste physische Zeile, "LUA"|"ALIAS")]`` nach dem Panel-Scanner (logische Zeilen; wirft nie)."""
    out: List[Tuple[int, str]] = []
    try:
        logical = split_logical_lines(content or "")
    except Exception:  # noqa: BLE001
        return out
    for ll in logical:
        if ll.text.upper().startswith("$GENERATE"):
            for tok in _TOKEN_RE.findall(ll.text)[2:]:
                t = _PASSTHROUGH_TOKENS.get(tok.upper())
                if t:
                    out.append((ll.start, t))
                    break
            continue
        hit = _record_type_token(ll)
        if hit is None:
            continue
        t = _PASSTHROUGH_TOKENS.get(hit[1])
        if t:
            out.append((ll.start, t))
    return out


def count_passthrough_records(content: str) -> Dict[str, int]:
    """``{"LUA": n, "ALIAS": m}`` einer Zonendatei (Vorschau-Zaehler, F15 3.6; wirft nie).

    Zaehlt auch Zeilen mit Klammerfehlern, die Generic-Schreibweise ``TYPE65402`` und ``$GENERATE``-Vorlagen mit
    LUA/ALIAS. Das Import-Gate bei Policy ``disabled`` nutzt zusaetzlich ``lua_gate_lines`` (PowerDNS-Sicht).
    """
    out = {t: 0 for t in PASSTHROUGH_TYPE_CODES}
    for _, t in _passthrough_hits(content):
        out[t] += 1
    return out


# ---------------------------------------------------------------------------
# Import-Gate bei Policy "disabled" (F15 3.6; Review Welle 3)
# ---------------------------------------------------------------------------
# Der Panel-Scanner oben deutet die Zeilenstruktur wie BIND (verschachtelte Klammern ueber Zeilen, Quotes).
# PowerDNS (ZoneParserTNG, mit pdnsutil 4.9 nachgeprueft) liest den Kopf eines Records anders: nur die erste
# physische Zeile, Tokens nur an Leerzeichen/Tab getrennt (Quotes egal, ``"x`` ist ein gueltiger Owner),
# beliebig viele ``IN``, eine TTL; die API trennt Zeilen an ``\n`` und ``\r``; Klammern wirken erst im Inhalt.
# Der Typ wird case-insensitiv erkannt, ``TYPE`` + Zahl ueber ``stoll`` (fuehrende Nullen, ``+``, Leerraum und
# Text nach der Zahl erlaubt: ``TYPE065402x`` ist LUA). ``$GENERATE`` ersetzt ``$``/``${…}`` auch im Typ
# (``LU${9,1,X}`` ergibt ``LUA``), ``$INCLUDE`` liest eine Datei auf dem PowerDNS-Server.
# ``lua_gate_lines`` vereinigt deshalb drei Sichten und meldet lieber zu viel als zu wenig (Fehlalarme treffen
# nur Policy ``disabled``): (a) PowerDNS-Kopf je physischer Zeile (beide Tokenisierungen, Owner-Frage im
# Zweifel beidseitig, Uebermenge der uebersprungenen TTL-/Klassen-Tokens), (b) jedes Token ausserhalb von
# Quotes und Kommentaren ausser dem Owner, (c) der Panel-Scanner (``_passthrough_hits``).
MAX_GATE_LINES = 50
_GATE_TYPE_RE = re.compile(r"LUA|TYPE\s*\+?0*65402(?:\D.*)?", re.I | re.S)
_GATE_SKIP_RE = re.compile(r"[0-9smhdw]+|IN|CH|HS|CS|ANY|NONE|CLASS\d+", re.I)
_PDNS_TOKEN_RE = re.compile(r"[^ \t]+")


def _gate_head_hit(tokens: List[str], start: int, *, template: bool = False) -> bool:
    """Kopf ab ``tokens[start]``: TTL-/Klassen-Tokens ueberspringen, dann ist das naechste Token der Typ.

    ``template=True`` ($GENERATE): jedes Token mit ``$`` kann zu LUA expandieren -> Treffer.
    """
    for tok in tokens[start:]:
        semi = tok.find(";")
        if semi == 0:
            return False
        if semi > 0:
            tok = tok[:semi]
        if template and "$" in tok:
            return True
        if _GATE_TYPE_RE.fullmatch(tok):
            return True
        if semi > 0 or not _GATE_SKIP_RE.fullmatch(tok):
            return False
    return False


def _gate_pdns_head(line: str) -> bool:
    """Sicht (a): Kopf einer physischen Zeile wie ZoneParserTNG, mit Uebermengen an den Unsicherheitsstellen."""
    if not line:
        return False
    if line[0] == "$":
        toks = line.split()
        cmd = toks[0].upper()
        if cmd == "$INCLUDE":
            return True  # Inhalt der eingebundenen Datei ist nicht pruefbar
        if cmd == "$GENERATE":
            # $GENERATE <range> <lhs> [ttl] [class] <type> <rhs>; lhs wird zum Owner
            return any(_gate_head_hit(t, 3, template=True) for t in (toks, _PDNS_TOKEN_RE.findall(line)))
        return False
    if line[0] in " \t":
        starts: Tuple[int, ...] = (0,)  # Owner vom Vorgaenger
    elif line[0].isspace():
        starts = (0, 1)  # \v, \f, Unicode-Leerraum: PowerDNS sieht hier ggf. einen Owner
    else:
        starts = (1,)
    for toks in (_PDNS_TOKEN_RE.findall(line), line.split()):
        if not toks or toks[0].startswith(";"):
            continue
        if any(_gate_head_hit(toks, k) for k in starts):
            return True
    return False


def _gate_unquoted_tokens(line: str) -> bool:
    """Sicht (b): irgendein Token ausserhalb von Quotes/Kommentaren (ohne Owner) ist ``LUA``/``TYPE65402``."""
    if not line or line[0] == "$":
        return False
    buf: List[str] = []
    in_quote = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            if not in_quote:
                buf.append(line[i:i + 2])
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
            buf.append(" ")
        elif not in_quote:
            if c == ";":
                break
            buf.append(c)
        i += 1
    text = "".join(buf)
    owner = 0 if line[0].isspace() else 1
    for variant in (re.sub(r"[()]", " ", text), re.sub(r"[()]", "", text)):
        toks = variant.split()
        if any(_GATE_TYPE_RE.fullmatch(t) for t in toks[owner:]):
            return True
    return False


def lua_gate_lines(content: str) -> List[int]:
    """Zeilennummern (1-basiert, an ``\n`` gezaehlt), die bei Policy ``disabled`` den Import sperren (wirft nie).

    Leere Liste = PowerDNS legt aus dieser Datei keinen LUA-Record an. Laufzeit linear in der Dateilaenge.
    """
    text = content if isinstance(content, str) else str(content or "")
    hits: Set[int] = set()
    for no, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip(" \t\r\n\x1a")
        variants = [line]
        if "\r" in line:
            variants.extend(line.split("\r"))  # die PowerDNS-API trennt Zeilen auch an \r
        for v in variants:
            if _gate_pdns_head(v) or _gate_unquoted_tokens(v):
                hits.add(no)
                break
    panel = [no for no, t in _passthrough_hits(text) if t == "LUA"]
    if panel:
        # Panel-Scanner zaehlt Zeilen mit str.splitlines() (auch \r, \v, \f, \u2028 …) -> auf \n-Zaehlung abbilden
        to_nl: List[int] = []
        nl = 1
        for part in text.splitlines(keepends=True):
            to_nl.append(nl)
            if part.endswith("\n"):
                nl += 1
        for no in panel:
            hits.add(to_nl[no - 1] if 0 < no <= len(to_nl) else nl)
    return sorted(hits)


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
