"""LUA-Records: reine Pruef-/Normalisierungsfunktionen und die Panel-Policy (F15 5.1).

Top-Level-Imports nur Standardbibliothek und ``fastapi.HTTPException`` (wird aus
``schemas/dns.py`` importiert -> keine Zirkularitaet). DB-/Auth-Abhaengigkeiten werden
lazy in den Policy-Funktionen importiert.

Server-Status (F15 5.3, Welle 3): ``get_server_lua_status(refresh)`` fragt ``GET /config`` aller geladenen
PowerDNS-Server parallel ab und gibt **nur** die LUA-relevanten Werte weiter (``parse_lua_config``-Whitelist –
die pdns.conf kann Passwoerter enthalten, z. B. ``gmysql-password``). Ergebnisse werden 60 s im Prozess
gecacht (``_status_cache``); ``refresh`` erzwingt eine neue Abfrage (der Router laesst das nur Admins zu).
Fehlertexte sind kurze deutsche Saetze ohne PowerDNS-Body oder URL.

Die Pruefung ist bewusst eine syntaktische Grobpruefung (Anfuehrungszeichen, Klammern,
Kommentare) – PowerDNS wertet den LUA-Code erst bei der Abfrage aus.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

LUA_POLICY_KEY = "lua_records_policy"
LUA_POLICIES = ("admin", "manage", "disabled")
DEFAULT_LUA_POLICY = "admin"
LUA_TARGET_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX", "SRV", "PTR", "CAA",
                    "NAPTR", "LOC", "SPF", "HTTPS", "SVCB", "SSHFP", "TLSA")
LUA_MAX_CONTENT_LENGTH = 4000  # gesamter Inhalt inkl. Ziel-Typ und Anfuehrungszeichen
# Obergrenze fuer die Vergleichs-Normalisierung (Bulk-Loeschungen, Import-Diff): laengere Inhalte werden nur
# getrimmt. PowerDNS speichert hoechstens 64000 Zeichen je Inhalt, echte Records liegen also immer darunter.
LUA_NORMALIZE_MAX_LENGTH = 65536
LUA_STATUS_CACHE_TTL = 60.0  # Sekunden (Server-Status)
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
_CHUNK_RE = re.compile(_CHUNK, re.S)
_CHUNK_CONTENT = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
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
    return f"{t} {_join_chunks(rest)}"


def _join_chunks(rest: str) -> str:
    """Mehrere ``"…"``-Abschnitte mit genau einem Leerzeichen verbinden.

    So speichert PowerDNS den Inhalt (``xfrText`` mit mehreren Abschnitten); ``rest`` muss ``_CHUNKS`` erfuellen.
    """
    return " ".join(m.group(0) for m in _CHUNK_RE.finditer(rest))


def normalize_lua_content(content: str) -> str:
    """Tolerante Normalisierung fuer Vergleiche (wirft nie): Ziel-Typ gross, genau ein Leerzeichen.

    Gueltige ``"…" "…"``-Abschnittsfolgen werden wie bei PowerDNS mit genau einem Leerzeichen verbunden.
    Laufzeit linear in der Eingabelaenge (kein Backtracking-Regex: der Inhalt kommt ungeprueft aus
    Bulk-Loeschungen und Importdateien); ab ``LUA_NORMALIZE_MAX_LENGTH`` Zeichen nur ``strip()``.
    """
    s = (content if isinstance(content, str) else str(content or "")).strip()
    if len(s) > LUA_NORMALIZE_MAX_LENGTH:
        return s
    parts = s.split(None, 1)
    if len(parts) != 2:
        return s
    typ, rest = parts  # rest ist durch strip()/split() an beiden Enden getrimmt
    if not (typ.isascii() and typ.isalnum()):
        return s
    if _CHUNKS.match(rest):
        rest = _join_chunks(rest)
    return f"{typ.upper()} {rest}"


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


# --------------------------------------------------------------------------- Server-Status (F15 5.3)
# Nur diese Werte aus ``GET /config`` verlassen das Modul (Whitelist, F15 7 "Informationsabfluss").
STATUS_FIELDS = ("lua_records", "geoip_backend", "edns_subnet_processing", "exec_limit", "health_checks_interval")

MSG_STATUS_UNREACHABLE = "Server nicht erreichbar"
MSG_STATUS_TIMEOUT = "Zeitüberschreitung beim Abruf der Konfiguration"
MSG_STATUS_KEY_REJECTED = "API-Key abgelehnt (HTTP {code})"
MSG_STATUS_UNREADABLE_HTTP = "Konfiguration nicht lesbar (HTTP {code})"
MSG_STATUS_UNREADABLE = "Konfiguration nicht lesbar"
MSG_STATUS_NOT_FOUND = "Server nicht gefunden"
MSG_STATUS_NOT_LOADED = "Server nicht geladen (API-Key unter Einstellungen → DNS-Server neu eintragen)"

_TRUE_VALUES = ("yes", "true", "1", "on")
_FALSE_VALUES = ("no", "false", "0", "off")

# Servername -> (time.monotonic() der Abfrage, Status-Dict)
_status_cache: dict[str, tuple[float, dict]] = {}


def clear_status_cache() -> None:
    """Cache leeren (Tests; nach Server-Aenderungen nicht noetig – der Cache laeuft nach 60 s ab)."""
    _status_cache.clear()


def _iso_now() -> str:
    from app.core.timeutil import iso_utc  # lazy: core.timeutil ist leicht, aber App-Paket

    return iso_utc(datetime.now(timezone.utc))


def _cfg_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in _TRUE_VALUES:
        return True
    if v in _FALSE_VALUES or v == "":
        return False
    return None


def _cfg_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def parse_lua_config(config: Any) -> dict:
    """LUA-relevante Werte aus der Antwort von ``GET /config`` (Liste von ``{name, value}``).

    ``lua_records``: ``"yes"`` | ``"shared"`` | ``"no"`` | ``None`` (Key fehlt = PowerDNS ohne LUA bzw. unbekannter
    Wert); ``geoip_backend``: ``True``, wenn ``geoip`` in ``launch`` steht (``None`` ohne ``launch``);
    ``edns_subnet_processing`` (bool), ``exec_limit``/``health_checks_interval`` (int). Andere Konfigurationswerte
    werden nie zurueckgegeben.
    """
    values: dict[str, str] = {}
    for item in config if isinstance(config, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            values[item["name"]] = str(item.get("value", "") if item.get("value") is not None else "")

    lua_raw = values.get("enable-lua-records")
    lua: Optional[str] = None
    if lua_raw is not None:
        v = lua_raw.strip().lower()
        if v == "shared":
            lua = "shared"
        elif v in _TRUE_VALUES:
            lua = "yes"
        elif v in _FALSE_VALUES or v == "":
            lua = "no"

    launch = values.get("launch")
    geoip: Optional[bool] = None
    if launch is not None:
        geoip = any(x.split(":")[0].strip().lower() == "geoip" for x in re.split(r"[,\s]+", launch) if x)

    return {
        "lua_records": lua,
        "geoip_backend": geoip,
        "edns_subnet_processing": _cfg_bool(values.get("edns-subnet-processing")),
        "exec_limit": _cfg_int(values.get("lua-records-exec-limit")),
        "health_checks_interval": _cfg_int(values.get("lua-health-checks-interval")),
    }


def _empty_status(name: str, *, reachable: bool, error: Optional[str]) -> dict:
    out = {field: None for field in STATUS_FIELDS}
    out.update({"name": name, "reachable": reachable, "error": error, "checked_at": _iso_now()})
    return out


def status_error_text(exc: BaseException) -> tuple[str, bool]:
    """(kurzer deutscher Fehlertext, reachable) fuer einen gescheiterten Konfigurationsabruf (F15 3.3)."""
    from app.services.pdns_client import PowerDNSAPIError

    if isinstance(exc, PowerDNSAPIError):
        code = int(getattr(exc, "status_code", 0) or 0)
        if code == 503:
            return MSG_STATUS_UNREACHABLE, False
        if code == 504:
            return MSG_STATUS_TIMEOUT, False
        if code == 502 or getattr(exc, "transport_error", False):
            return MSG_STATUS_UNREACHABLE, False
        if code in (401, 403):
            return MSG_STATUS_KEY_REJECTED.format(code=code), True
        return MSG_STATUS_UNREADABLE_HTTP.format(code=code), True
    return MSG_STATUS_UNREADABLE, False


async def _probe(name: str, client) -> dict:
    """Konfiguration eines Servers abfragen; wirft nie (Fehler -> ``error``)."""
    from app.services.pdns_client import STATUS_PROBE_TIMEOUT, PowerDNSAPIError

    try:
        cfg = await client.get_config(timeout=STATUS_PROBE_TIMEOUT)
    except PowerDNSAPIError as exc:
        text, reachable = status_error_text(exc)
        logger.info("LUA-Status: Konfiguration von %s nicht lesbar (HTTP %s)", name, exc.status_code)
        return _empty_status(name, reachable=reachable, error=text)
    except Exception as exc:  # noqa: BLE001 - Statusanzeige darf nie scheitern
        logger.warning("LUA-Status: Abruf der Konfiguration von %s gescheitert (%s)", name, type(exc).__name__)
        return _empty_status(name, reachable=False, error=MSG_STATUS_UNREADABLE)
    out = parse_lua_config(cfg or [])
    out.update({"name": name, "reachable": True, "error": None, "checked_at": _iso_now()})
    return out


async def _probe_named(name: str) -> dict:
    from app.services.pdns_client import pdns_manager

    try:
        client = pdns_manager.get_client(name)
    except ValueError:
        return _empty_status(name, reachable=False, error=MSG_STATUS_NOT_FOUND)
    return await _probe(name, client)


async def get_server_lua_status(refresh: bool = False) -> dict:
    """LUA-Status aller PowerDNS-Server: ``{"servers": [...], "checked_at": iso, "cached": bool}``.

    Geladene Server (``pdns_manager.list_servers()``) werden parallel abgefragt, sofern kein frischer
    Cache-Eintrag (< ``LUA_STATUS_CACHE_TTL``) existiert oder ``refresh`` gesetzt ist. Konfigurierte, aber
    nicht geladene Server (``pdns_manager.unloaded``, z. B. API-Key unlesbar [D4]) erscheinen mit Fehlertext.
    ``checked_at`` ist der Zeitpunkt des aeltesten verwendeten Eintrags; ``cached`` = mindestens ein Eintrag
    kam aus dem Cache.
    """
    from app.services.pdns_client import pdns_manager

    names = list(pdns_manager.list_servers())
    now = time.monotonic()
    todo = [n for n in names
            if refresh or n not in _status_cache or now - _status_cache[n][0] > LUA_STATUS_CACHE_TTL]
    if todo:
        results = await asyncio.gather(*(_probe_named(n) for n in todo))
        stamp = time.monotonic()
        for r in results:
            _status_cache[r["name"]] = (stamp, r)
    for stale in set(_status_cache) - set(names):
        _status_cache.pop(stale, None)

    servers = [_status_cache[n][1] for n in names if n in _status_cache]
    for name in sorted(getattr(pdns_manager, "unloaded", {}) or {}):
        if name not in names:
            servers.append(_empty_status(name, reachable=False, error=MSG_STATUS_NOT_LOADED))
    stamps = [s["checked_at"] for s in servers if s.get("checked_at")]
    return {
        "servers": servers,
        "checked_at": min(stamps) if stamps else _iso_now(),
        "cached": len(todo) < len(names),
    }
