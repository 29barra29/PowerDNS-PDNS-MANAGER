"""LUA-Records: reine Pruef-/Normalisierungsfunktionen und die Panel-Policy (F15 5.1).

Top-Level-Imports nur Standardbibliothek und ``fastapi.HTTPException`` (wird aus
``schemas/dns.py`` importiert -> keine Zirkularitaet). DB-/Auth-Abhaengigkeiten werden
lazy in den Policy-Funktionen importiert. Der Server-Status (PowerDNS-Konfiguration je
Server, F15 5.3) folgt in Welle 3.

Die Pruefung ist bewusst eine syntaktische Grobpruefung (Anfuehrungszeichen, Klammern,
Kommentare) – PowerDNS wertet den LUA-Code erst bei der Abfrage aus.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

LUA_POLICY_KEY = "lua_records_policy"
LUA_POLICIES = ("admin", "manage", "disabled")
DEFAULT_LUA_POLICY = "admin"
LUA_TARGET_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX", "SRV", "PTR", "CAA",
                    "NAPTR", "LOC", "SPF", "HTTPS", "SVCB", "SSHFP", "TLSA")
LUA_MAX_CONTENT_LENGTH = 4000  # gesamter Inhalt inkl. Ziel-Typ und Anfuehrungszeichen
LUA_STATUS_CACHE_TTL = 60.0  # Sekunden (Server-Status, Welle 3)
GEO_FUNCTIONS = ("pickclosest", "country", "countryCode", "continent", "continentCode",
                 "region", "regionCode", "latlon", "latlonloc", "closestMagic", "asnum")

MSG_DENIED_ADMIN = "LUA-Records dürfen nur Administratoren anlegen oder ändern."
MSG_DENIED_DISABLED = "LUA-Records sind in diesem Panel deaktiviert (Einstellungen → DNS-Optionen)."

MSG_EMPTY = "LUA-Inhalt fehlt."
MSG_TOO_LONG = f"LUA-Inhalt ist zu lang (max. {LUA_MAX_CONTENT_LENGTH} Zeichen)."
MSG_CONTROL = "LUA-Inhalt darf keine Zeilenumbrüche, Tabulatoren oder Steuerzeichen enthalten."
MSG_NO_TYPE = "LUA-Inhalt muss mit dem Ziel-Typ beginnen, z. B. A \"ifportup(443, {'192.0.2.1'})\"."
MSG_TYPE_NOT_ALLOWED = "Ziel-Typ {t} ist für LUA nicht erlaubt. Erlaubt: " + ", ".join(LUA_TARGET_TYPES)
MSG_NOT_QUOTED = "Der LUA-Code muss in doppelten Anführungszeichen stehen."
MSG_QUOTES_UNBALANCED = "Anführungszeichen im LUA-Inhalt sind nicht ausgeglichen."
MSG_ESCAPED_QUOTE = ("Doppelte Anführungszeichen im LUA-Code sind nicht erlaubt – "
                     "bitte einfache (') oder [[…]] verwenden.")
MSG_CODE_EMPTY = "Der LUA-Code ist leer."
MSG_UNTERMINATED_STRING = "Eine Zeichenkette im LUA-Code ist nicht geschlossen."
MSG_UNTERMINATED_COMMENT = "Ein Block-Kommentar (--[[ … ]]) im LUA-Code ist nicht geschlossen."
MSG_UNBALANCED = "Klammern im LUA-Code sind nicht ausgeglichen ((), {}, [])."

_TYPE_RE = re.compile(r"^([A-Za-z0-9]+)\s+(\S.*)$", re.S)
_CHUNK = r'"[^"\\]*(?:\\.[^"\\]*)*"'
_CHUNKS = re.compile(r"^" + _CHUNK + r"(?:\s+" + _CHUNK + r")*$", re.S)
_CHUNK_CONTENT = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NORMALIZE_RE = re.compile(r"^\s*([A-Za-z0-9]+)\s+(.*?)\s*$", re.S)
_GEO_RE = re.compile(r"\b(" + "|".join(GEO_FUNCTIONS) + r")\s*\(")
LONG_OPEN = re.compile(r"\[(=*)\[")
_PAIRS = {")": "(", "]": "[", "}": "{"}

_invalid_policy_warned = False


# --------------------------------------------------------------------------- reine Funktionen
def split_lua_content(content: str) -> tuple[str, str]:
    """``'A "code"'`` -> ``("A", '"code"')``; ValueError, wenn der Ziel-Typ fehlt."""
    m = _TYPE_RE.match((content or "").strip())
    if not m:
        raise ValueError(MSG_NO_TYPE)
    return m.group(1).upper(), m.group(2)


def lua_code_chunks(rest: str) -> list[str]:
    """Inhalte der in doppelten Anfuehrungszeichen stehenden Abschnitte; ValueError bei unausgeglichenen Quotes."""
    if not _CHUNKS.match(rest or ""):
        raise ValueError(MSG_QUOTES_UNBALANCED)
    return _CHUNK_CONTENT.findall(rest)


def check_lua_brackets(code: str) -> Optional[str]:
    """Tokenizer fuer Klammern/Strings/Kommentare.

    Rueckgabe ``None`` (in Ordnung) | ``"unbalanced"`` | ``"unterminated_string"`` |
    ``"unterminated_comment"``. Identische Logik im Frontend (``lib/luaRecord.js``).
    """
    stack: list[str] = []
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if code.startswith("--", i):
            m = LONG_OPEN.match(code, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = code.find(close, m.end())
                if j < 0:
                    return "unterminated_comment"
                i = j + len(close)
                continue
            # Zeilenkommentar: Rest ist Kommentar (Code ist einzeilig)
            return "unbalanced" if stack else None
        if c in "'\"":
            q = c
            i += 1
            while i < n and code[i] != q:
                i += 2 if code[i] == "\\" else 1
            if i >= n:
                return "unterminated_string"
            i += 1
            continue
        if c == "[":
            m = LONG_OPEN.match(code, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = code.find(close, m.end())
                if j < 0:
                    return "unterminated_string"
                i = j + len(close)
                continue
        if c in "([{":
            stack.append(c)
        elif c in ")]}":
            if not stack or stack.pop() != _PAIRS[c]:
                return "unbalanced"
        i += 1
    return "unbalanced" if stack else None


def validate_lua_content(content: str) -> str:
    """Prueft einen LUA-Record-Inhalt und liefert ihn normalisiert (``f"{TYP} {rest}"``).

    Wirft ``ValueError`` mit deutscher Meldung (Reihenfolge laut F15 5.1).
    """
    s = (content or "").strip()
    if not s:
        raise ValueError(MSG_EMPTY)
    if len(s) > LUA_MAX_CONTENT_LENGTH:
        raise ValueError(MSG_TOO_LONG)
    if _CONTROL_RE.search(s):
        raise ValueError(MSG_CONTROL)
    t, rest = split_lua_content(s)
    if t not in LUA_TARGET_TYPES:
        raise ValueError(MSG_TYPE_NOT_ALLOWED.format(t=t))
    if not rest.startswith('"'):
        raise ValueError(MSG_NOT_QUOTED)
    chunks = lua_code_chunks(rest)
    if any('\\"' in ch for ch in chunks):
        raise ValueError(MSG_ESCAPED_QUOTE)
    code = " ".join(chunks)
    if not code.strip():
        raise ValueError(MSG_CODE_EMPTY)
    problem = check_lua_brackets(code)
    if problem == "unterminated_string":
        raise ValueError(MSG_UNTERMINATED_STRING)
    if problem == "unterminated_comment":
        raise ValueError(MSG_UNTERMINATED_COMMENT)
    if problem == "unbalanced":
        raise ValueError(MSG_UNBALANCED)
    return f"{t} {rest}"


def normalize_lua_content(content: str) -> str:
    """Tolerante Normalisierung fuer Vergleiche (wirft nie): Ziel-Typ gross, genau ein Leerzeichen."""
    s = content if isinstance(content, str) else str(content or "")
    m = _NORMALIZE_RE.match(s)
    if not m:
        return s.strip()
    return f"{m.group(1).upper()} {m.group(2)}"


def uses_geo_functions(content: str) -> bool:
    """True, wenn der Inhalt Geo-Funktionen nutzt (brauchen das GeoIP-Backend)."""
    return bool(_GEO_RE.search(content or ""))


def validate_lua_items(rtype: str, items: Iterable[Any]) -> Iterable[Any]:
    """Normalisiert ``RecordItem``-artige Objekte (Attribut oder Dict-Key ``content``) bei Typ LUA.

    Fuer Schemas mit ``type`` + ``records`` (F1-Bulk-Items, RecordCreate). Andere Typen
    bleiben unveraendert; ungueltiger Inhalt -> ``ValueError``.
    """
    if (rtype or "").strip().upper() != "LUA":
        return items
    for item in items:
        if isinstance(item, dict):
            item["content"] = validate_lua_content(item.get("content") or "")
        else:
            item.content = validate_lua_content(getattr(item, "content", "") or "")
    return items


# --------------------------------------------------------------------------- Policy
async def get_lua_policy(db) -> str:
    """Aktuelle Policy aus ``system_settings`` (``admin`` | ``manage`` | ``disabled``), Default ``admin``."""
    global _invalid_policy_warned
    from app.services.system_settings import get_setting

    raw = await get_setting(db, LUA_POLICY_KEY)
    v = (raw or "").strip().lower()
    if v in LUA_POLICIES:
        return v
    if v and not _invalid_policy_warned:
        _invalid_policy_warned = True
        logger.warning("Ungueltiger Wert fuer %s (%r) – verwende '%s'", LUA_POLICY_KEY, raw, DEFAULT_LUA_POLICY)
    return DEFAULT_LUA_POLICY


def lua_denied_message(policy: str) -> str:
    return MSG_DENIED_DISABLED if policy == "disabled" else MSG_DENIED_ADMIN


def lua_policy_allows(policy: str, user) -> bool:
    """``disabled`` -> nie; ``manage`` -> jeder mit Schreibrecht auf die Zone (vorher gepruefte ACL);
    ``admin`` -> nur effektive Admins (Panel-Token ohne Admin-Freigabe zaehlt nicht)."""
    if policy == "disabled":
        return False
    if policy == "manage":
        return True
    from app.core.auth import is_effective_admin  # lazy: core.auth importiert Models/DB

    return bool(is_effective_admin(user))


async def lua_allowed_for(db, user) -> bool:
    """Darf ``user`` LUA-Records anlegen/aendern? (Signatur wie von F1 erwartet)"""
    return lua_policy_allows(await get_lua_policy(db), user)


async def assert_lua_write_allowed(db, user) -> None:
    """403 mit Policy-Text, wenn ``user`` keine LUA-Records schreiben darf (Signatur wie von F7 erwartet)."""
    policy = await get_lua_policy(db)
    if not lua_policy_allows(policy, user):
        raise HTTPException(status_code=403, detail=lua_denied_message(policy))
