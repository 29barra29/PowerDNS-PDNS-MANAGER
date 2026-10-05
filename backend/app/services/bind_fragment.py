"""BIND-Fragment-Parser fuer den Text-Editor des Bulk-Editors (F1 5.4).

Eigener zeilenbasierter Parser statt ``dns.zone.from_text``: sammelt **alle** Fehler mit Zeilennummern, braucht
kein SOA/NS am Apex, laesst LUA/ALIAS durch (dnspython kennt beide nicht) und versteht den Marker
``;@disabled <zeile>`` fuer deaktivierte Werte. Die logischen Zeilen (Kommentare, Klammern ueber mehrere Zeilen,
Quotes) liefert ``zone_import_diff.split_logical_lines`` – derselbe Scanner wie in der Zonen-Import-Vorschau.
Die Rdata-Pruefung laeuft ueber ``services/rrsets.canonical_content`` (dnspython, ``relativize=False`` ->
absolute Namen; LUA ueber ``lua_records.validate_lua_content``).

Rein funktional (keine I/O), damit ``services/bulk.py`` den Parser per ``asyncio.to_thread`` aufrufen kann.

Ausserdem liegen hier die Problem-Codes des Bulk-Editors (``issue``/``ISSUE_MESSAGES``), die Parser und
Plan-Builder gemeinsam verwenden. Die deutschen Texte entsprechen den DE-Texten der i18n-Keys
``bulk.issue.<code>`` (F1 6.6).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import dns.exception
import dns.name
import dns.ttl

from app.schemas.dns import ALLOWED_RECORD_TYPES
from app.services.rrsets import DNSSEC_AUTO_TYPES, canonical_content, content_key, norm_name
from app.services.zone_import_diff import split_logical_lines

TTL_MIN = 60
TTL_MAX = 604800
DEFAULT_MAX_LINES = 20_000

_GENERIC_TYPE_RE = re.compile(r"TYPE\d+")
_CLASS_UNSUPPORTED_RE = re.compile(r"^(CH|HS|CS|ANY|NONE|CLASS\d+)$", re.I)
_TOKEN_RE = re.compile(r"\S+")

# ---------------------------------------------------------------------------------------------- Problem-Codes
ISSUE_MESSAGES: dict[str, str] = {
    "parse_error": "Zeile {line}: {message}",
    "directive_unsupported": "Zeile {line}: {directive} wird nicht unterstützt.",
    "class_unsupported": "Zeile {line}: Nur die Klasse IN ist erlaubt.",
    "type_unknown": "Record-Typ {type} ist hier nicht erlaubt.",
    "soa_forbidden": ("SOA kann im Bulk-Editor nicht geändert oder gelöscht werden – bitte den Einzeleditor "
                      "verwenden."),
    "dnssec_skipped": "Zeile {line}: {type} wird von PowerDNS selbst erzeugt und wurde ignoriert.",
    "outside_zone": "{name} liegt nicht in der Zone {zone}.",
    "ttl_range": "TTL {ttl} ist ungültig (erlaubt 60–604800).",
    "owner_missing": "Zeile {line}: Die Zeile beginnt mit Leerraum, aber davor steht kein Name.",
    "too_many_lines": "Der Text ist zu lang (maximal {max} Zeilen).",
    "empty_input": "Keine Record-Zeilen gefunden.",
    "ttl_conflict": "{name} {type}: unterschiedliche TTLs im Text, verwendet wird {ttl}.",
    "duplicate_value": "{name} {type}: Wert {content} steht doppelt im Text.",
    "value_missing": "{name} {type}: Wert {content} ist nicht (mehr) vorhanden. Bitte Zone neu laden.",
    "rrset_missing": "{name} {type} ist nicht (mehr) vorhanden.",
    "cname_conflict": "{name}: Ein CNAME darf nicht neben anderen Record-Typen stehen.",
    "cname_multi": "{name}: Ein CNAME darf nur einen Wert haben.",
    "apex_cname": "Am Zonen-Apex ist kein CNAME erlaubt.",
    "apex_ns_delete": "Die NS-Records am Zonen-Apex dürfen nicht vollständig gelöscht werden.",
    "lua_forbidden": "{message}",
    "too_many_changes": "Zu viele Änderungen in einer Anfrage (maximal {max} RRsets). Bitte aufteilen.",
    "dnskey_managed": ("{type} wird bei aktivem DNSSEC von PowerDNS verwaltet – manuelle Änderungen können die "
                       "Signatur brechen."),
    "scope_invalid": "{name} {type} aus dem geladenen Bereich ist ungültig und wurde ignoriert.",
}


def issue(code: str, severity: str = "error", *, line: Optional[int] = None, name: Optional[str] = None,
          type: Optional[str] = None, text: Optional[str] = None, **params: Any) -> dict:  # noqa: A002
    """Problem-Eintrag im Format ``BulkIssue`` (``message`` = deutscher Text mit eingesetzten Werten).

    ``name``/``type`` landen sowohl in den gleichnamigen Feldern als auch in ``params`` (fuer die i18n-Texte).
    ``text`` ersetzt den Text aus ``ISSUE_MESSAGES`` (sonst mit ``params`` formatiert). Ein Parameter
    ``message`` ist der Detailtext von ``parse_error``/``lua_forbidden`` (i18n: ``{{message}}``).
    """
    values: dict[str, Any] = dict(params)
    if name is not None:
        values.setdefault("name", name)
    if type is not None:
        values.setdefault("type", type)
    if line is not None:
        values.setdefault("line", line)
    message = text
    if message is None:
        template = ISSUE_MESSAGES.get(code, code)
        try:
            message = template.format(**values)
        except (KeyError, IndexError, ValueError):
            message = template
    out_params = {k: v for k, v in values.items() if k != "line"}
    return {"code": code, "severity": severity, "message": message, "line": line, "name": name, "type": type,
            "params": out_params}


def has_errors(issues: list[dict]) -> bool:
    return any(i.get("severity") == "error" for i in issues)


# ---------------------------------------------------------------------------------------------- Ergebnis
@dataclass
class ParsedValue:
    content: str     # kanonischer Inhalt (dnspython-Darstellung, absolute Namen; LUA normalisiert)
    disabled: bool
    key: str         # rrsets.content_key – Vergleichsschluessel
    line: int


@dataclass
class ParsedRRset:
    name: str                 # lower-case FQDN mit Punkt
    type: str                 # upper-case
    ttl: Optional[int]        # erste explizite TTL (Zeile oder $TTL), sonst None
    values: list[ParsedValue] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.type)


@dataclass
class ParsedFragment:
    rrsets: dict[tuple[str, str], ParsedRRset] = field(default_factory=dict)  # Einfuegereihenfolge
    issues: list[dict] = field(default_factory=list)
    line_count: int = 0

    @property
    def has_errors(self) -> bool:
        return has_errors(self.issues)

    @property
    def warnings(self) -> list[dict]:
        return [i for i in self.issues if i.get("severity") != "error"]


# ---------------------------------------------------------------------------------------------- Parser
def _parse_ttl(token: str) -> int:
    """BIND-TTL (``300``, ``1h``, ``1h30m``, ``2W``) -> Sekunden; ``ValueError`` bei ungueltiger Angabe."""
    try:
        return int(dns.ttl.from_text(token.lower()))
    except (dns.exception.DNSException, ValueError) as exc:
        raise ValueError(f"TTL '{token}' ist ungültig") from exc


def _parse_name(token: str, origin: dns.name.Name) -> dns.name.Name:
    try:
        return dns.name.from_text(token, origin=origin)
    except Exception as exc:  # dns.exception.*, UnicodeError (IDNA)
        raise ValueError(f"Name '{token}' ist ungültig ({exc})") from exc


def _name_text(n: dns.name.Name) -> str:
    return norm_name(n.to_text())


def parse_bind_fragment(zone_name: str, content: str, *, max_lines: int = DEFAULT_MAX_LINES) -> ParsedFragment:
    """Zerlegt BIND-Zeilen in RRsets (F1 5.4). Sammelt alle Probleme mit Zeilennummer, wirft nie.

    Zeilenformat: ``owner [TTL] [IN] TYPE content`` (TTL und Klasse in beliebiger Reihenfolge); fuehrender
    Leerraum uebernimmt den vorherigen Owner; ``@`` = aktueller Origin; Namen ohne Punkt sind relativ zum
    Origin. Direktiven: ``$ORIGIN`` (muss in der Zone liegen) und ``$TTL`` (gilt als explizite TTL);
    ``$INCLUDE``/``$GENERATE`` u. a. -> ``directive_unsupported``.
    """
    out = ParsedFragment()
    zone_norm = norm_name(zone_name)
    text = content or ""
    phys = text.splitlines()
    out.line_count = len(phys)
    if len(phys) > max_lines:
        out.issues.append(issue("too_many_lines", max=max_lines))
        return out

    try:
        zone_origin = dns.name.from_text(zone_norm)
    except Exception as exc:  # noqa: BLE001 - Zonenname kommt aus der URL
        out.issues.append(issue("parse_error", line=1, message=f"Zonenname ungültig: {exc}"))
        return out
    origin = zone_origin
    default_ttl: Optional[int] = None  # $TTL
    last_owner: Optional[str] = None
    record_lines = 0

    def perr(line: int, message: str) -> None:
        out.issues.append(issue("parse_error", line=line, message=message))

    for ll in split_logical_lines(text, disabled_marker=True):
        line = ll.start
        if ll.error:
            perr(line, ll.error)
            continue
        body = ll.text
        tokens = list(_TOKEN_RE.finditer(body))
        if not tokens:
            continue

        # ---- Direktiven
        if body.startswith("$"):
            directive = tokens[0].group(0).upper()
            if ll.disabled:
                perr(line, f"{directive} kann nicht mit ;@disabled deaktiviert werden.")
                continue
            arg = tokens[1].group(0) if len(tokens) > 1 else ""
            if directive == "$ORIGIN":
                if not arg:
                    perr(line, "$ORIGIN ohne Namen.")
                    continue
                try:
                    new_origin = _parse_name(arg, origin)
                except ValueError as exc:
                    perr(line, str(exc))
                    continue
                if not new_origin.is_subdomain(zone_origin):
                    out.issues.append(issue("outside_zone", line=line, name=_name_text(new_origin),
                                            zone=zone_norm))
                    continue
                origin = new_origin
                continue
            if directive == "$TTL":
                if not arg:
                    perr(line, "$TTL ohne Wert.")
                    continue
                try:
                    ttl = _parse_ttl(arg)
                except ValueError as exc:
                    perr(line, str(exc))
                    continue
                if not (TTL_MIN <= ttl <= TTL_MAX):
                    out.issues.append(issue("ttl_range", line=line, ttl=ttl))
                    continue
                default_ttl = ttl
                continue
            out.issues.append(issue("directive_unsupported", line=line, directive=directive))
            continue

        # ---- Kopf: Owner, TTL/Klasse, Typ
        idx = 0
        if ll.leading_ws:
            if last_owner is None:
                out.issues.append(issue("owner_missing", line=line))
                continue
            owner_name = last_owner
        else:
            owner_token = tokens[0].group(0)
            idx = 1
            try:
                owner = origin if owner_token == "@" else _parse_name(owner_token, origin)
            except ValueError as exc:
                perr(line, str(exc))
                continue
            owner_name = _name_text(owner)
            if not owner.is_subdomain(zone_origin):
                out.issues.append(issue("outside_zone", line=line, name=owner_name, zone=zone_norm))
                last_owner = None
                continue
            last_owner = owner_name

        line_ttl: Optional[int] = None
        seen_ttl = seen_class = False
        bad = False
        for _ in range(2):
            if idx >= len(tokens):
                break
            tok = tokens[idx].group(0)
            if tok.upper() == "IN" and not seen_class:
                seen_class = True
                idx += 1
                continue
            if _CLASS_UNSUPPORTED_RE.match(tok) and not seen_class:
                out.issues.append(issue("class_unsupported", line=line, **{"class": tok.upper()}))
                bad = True
                break
            if tok[:1].isdigit() and not seen_ttl:
                try:
                    line_ttl = _parse_ttl(tok)
                except ValueError as exc:
                    perr(line, str(exc))
                    bad = True
                    break
                seen_ttl = True
                idx += 1
                continue
            break
        if bad:
            continue
        if idx >= len(tokens):
            perr(line, "Record-Typ fehlt.")
            continue
        type_match = tokens[idx]
        rtype = type_match.group(0).upper()
        rest = body[type_match.end():].strip()

        if rtype == "SOA":
            out.issues.append(issue("soa_forbidden", line=line, name=owner_name, type=rtype))
            continue
        if rtype in DNSSEC_AUTO_TYPES:
            out.issues.append(issue("dnssec_skipped", "warning", line=line, name=owner_name, type=rtype))
            continue
        if _GENERIC_TYPE_RE.fullmatch(rtype) or rtype not in ALLOWED_RECORD_TYPES:
            out.issues.append(issue("type_unknown", line=line, name=owner_name, type=rtype))
            continue
        if not rest:
            perr(line, "Inhalt fehlt.")
            continue

        ttl = line_ttl if line_ttl is not None else default_ttl
        if line_ttl is not None and not (TTL_MIN <= line_ttl <= TTL_MAX):
            out.issues.append(issue("ttl_range", line=line, name=owner_name, type=rtype, ttl=line_ttl))
            continue

        origin_text = origin.to_text()
        try:
            canonical = canonical_content(rtype, rest, origin_text)
        except ValueError as exc:
            perr(line, f"Inhalt für {rtype} ungültig: {exc}")
            continue
        key = content_key(rtype, canonical, origin_text)
        record_lines += 1

        rr = out.rrsets.get((owner_name, rtype))
        if rr is None:
            rr = ParsedRRset(name=owner_name, type=rtype, ttl=ttl)
            out.rrsets[(owner_name, rtype)] = rr
        elif ttl is not None:
            if rr.ttl is None:
                rr.ttl = ttl
            elif rr.ttl != ttl:
                out.issues.append(issue("ttl_conflict", "warning", line=line, name=owner_name, type=rtype,
                                        ttl=rr.ttl))
        if any(v.key == key for v in rr.values):
            out.issues.append(issue("duplicate_value", "warning", line=line, name=owner_name, type=rtype,
                                    content=canonical))
            continue
        rr.values.append(ParsedValue(content=canonical, disabled=ll.disabled, key=key, line=line))
        rr.lines.append(line)

    if not out.rrsets and not out.has_errors:
        out.issues.append(issue("empty_input"))
    return out
