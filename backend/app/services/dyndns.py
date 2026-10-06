"""DynDNS: Tokens, Pruefungen und Update-Logik (F9 5.9, Bauplan WS-F9F11-BE [S5, S6, D5]).

Ein DynDNS-Token (``dnsmgr_ddns_`` + 256 Bit) darf nur A/AAAA der gelisteten Hostnamen setzen – mit den Rechten
seines Besitzers zum Zeitpunkt des Updates. Gespeichert werden nur SHA-256 und Prefix.

Ablauf eines Updates (Router ``routers/dyndns.py``):

1. Cross-Site-Browser -> 403 ``badauth``; 2. ``dyndns_enabled`` aus -> 403 ``badauth``; 3. Geheimnis im Query ->
   401 ``badauth`` (ein gueltiger Token im Query wird sofort deaktiviert);
4. **Token pruefen** (Hash-Lookup). Gueltiger Token -> nur das Token-Limit: mehr als 30 Anfragen in 5 min -> 429
   ``911`` mit ``Retry-After`` (Router wiederholen spaeter), ab 300 in 5 min -> 429 ``abuse`` [S6].
   Fehlender/ungueltiger Token -> nur hier gilt die IP-Sperre (20 Fehlversuche in 15 min bzw. 120 Anfragen in
   5 min -> 429 ``911``), sonst 401 ``badauth`` und der Fehlversuch zaehlt [S6]. Eine gesperrte IP kann mit einem
   gueltigen Token also weiter aktualisieren.
5. Hostnamen und IPs parsen, je Hostname :func:`perform_update`.

``perform_update`` liest den Ist-Stand **je schreibbarem Server mit der Zone** parallel (3 s). ``nochg`` nur, wenn
alle erreichbaren Server den Zielzustand haben; sonst wird genau auf die abweichenden Server geschrieben
(``fanout.apply_rrsets(..., targets=<abweichend>, require_primary=False)``). Fehlerhafte oder nicht erreichbare
Server stehen persistent in ``dyndns_tokens.stale_servers`` (``{hostname: [server]}``); jede Reparatur eines
veralteten Peers schreibt ein ``DYNDNS_UPDATE``-Audit mit ``repair: true``, auch wenn die Antwort ``nochg`` lautet
[D5]. Ob ``good`` oder ``nochg`` zurueckgeht, entscheidet der Stand des ersten erreichbaren Servers.

Rate-Limiter, Dedupe der Fehler-Audits und der Zonen-Cache sind prozesslokal (ein Worker-Prozess, wie das Panel).
Logs nennen nur den Token-Prefix, nie den Token.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import logging
import re
import secrets
import time
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_network
from typing import Any, Iterable, Literal, Optional

from fastapi import HTTPException, Request
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import has_zone_access
from app.core.rate_limit import SlidingWindowLimiter
from app.core.timeutil import iso_utc, utcnow
from app.models.models import DynDnsToken, User
from app.services import fanout, ptr, zone_index
from app.services.acme import find_matching_zone
from app.services.audit import write_audit, write_audit_detached
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

TOKEN_PREFIX = "dnsmgr_ddns_"
_TOKEN_BYTES = 32
KEY_ENABLED = "dyndns_enabled"
KEY_ALLOW_PRIVATE = "dyndns_allow_private_ips"
ALLOWED_TYPES = ("A", "AAAA")
MIN_TTL, MAX_TTL, DEFAULT_TTL = 60, 86400, 60
MAX_HOSTNAMES = 20
MAX_TOKENS_PER_USER = 50
SECRET_QUERY_PARAMS = frozenset({"password", "pass", "pwd", "pw", "token", "key", "secret", "apikey", "api_key", "auth"})
SECRET_VALUE_PREFIX = "dnsmgr_"
PANEL_TOKEN_PREFIX = "dnsmgr_usr_"
# Bewusst NICHT ipaddress.is_private/is_global: die werten Dokunetze (192.0.2.0/24, 203.0.113.0/24, 2001:db8::/32)
# als privat – die sollen fuer Tests und Beispiele erlaubt bleiben.
PRIVATE_NETS = tuple(ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                                              "fc00::/7"))
# schlechtester Code zuerst
RESULT_ORDER = ("badauth", "abuse", "911", "dnserr", "nohost", "notfqdn", "numhost", "badip", "good", "nochg")

READ_TIMEOUT = 3.0  # Ist-Stand je Server [D5]
WRITE_TIMEOUT = 15.0
RETRY_AFTER_SERVER_ERROR = 300

# Rate-Limits [S6]
TOKEN_LIMIT, TOKEN_WINDOW = 30, 300.0          # gueltiger Token: > 30 / 5 min -> 911
TOKEN_ABUSE_THRESHOLD = 300                     # gueltiger Token: >= 300 / 5 min -> abuse
AUTH_FAIL_LIMIT, AUTH_FAIL_WINDOW = 20, 900.0   # ungueltiger Token: 20 Fehlversuche / 15 min je IP (IPv6 /64)
IP_REQUEST_LIMIT, IP_REQUEST_WINDOW = 120, 300.0  # ungueltiger Token: 120 Anfragen / 5 min je IP
FAILURE_AUDIT_TTL = 3600.0  # Fehler-Audits je (Token, Hostname, Code) hoechstens einmal pro Stunde
AUTH_AUDIT_TTL = 900.0      # DYNDNS_AUTH_FAILED je IP hoechstens alle 15 min
_DEDUPE_MAX_KEYS = 10_000

_auth_fail_by_ip = SlidingWindowLimiter(AUTH_FAIL_LIMIT, AUTH_FAIL_WINDOW)
_req_by_ip = SlidingWindowLimiter(IP_REQUEST_LIMIT, IP_REQUEST_WINDOW)
_req_by_token = SlidingWindowLimiter(TOKEN_LIMIT, TOKEN_WINDOW)
_failure_audited: dict[tuple, float] = {}

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_PLACEHOLDER_RE = re.compile(r"^<[^>]*>$")

# Texte (deutsch, F9 3.3)
MSG_NOT_IN_SCOPE = "Hostname nicht im Token-Scope"
MSG_NO_ZONE = "Keine vom Panel verwaltete Zone fuer {h}"
MSG_NO_WRITE_ZONE = "Kein Schreibrecht auf Zone {z}"
MSG_NO_WRITE_HOST = "Kein Schreibrecht auf die Zone dieses Hostnamens"
MSG_PDNS_REJECTED = "PowerDNS hat die Aenderung abgelehnt: {d}"
MSG_CNAME = "Am Namen existiert ein CNAME – A/AAAA nicht moeglich"
MSG_UNREACHABLE = "PowerDNS nicht erreichbar"
MSG_NO_TYPE = "Keine IP-Adresse eines erlaubten Typs (A/AAAA) angegeben"
MSG_INVALID_HOST = "Kein gueltiger Hostname angegeben"


def reset_state_for_tests() -> None:
    """Rate-Limiter und Dedupe zuruecksetzen (wie ein Neustart des Prozesses)."""
    _auth_fail_by_ip.reset()
    _req_by_ip.reset()
    _req_by_token.reset()
    _failure_audited.clear()


class DynDnsError(Exception):
    """Fachlicher DynDNS-Fehler mit Antwortcode (``badip``, ``notfqdn`` ...) und deutschem Text."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# =====================================================================================================================
# Tokens
# =====================================================================================================================
def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


# Entwertetes Secret nach einer Sicherheitssperre (Token im Query, Zugangs-Widerruf): kein SHA-256-Hex wie bei
# ``hash_token``, daher passt nie wieder ein Klartext. Der Praefix ist zugleich die persistente Markierung
# "aus Sicherheitsgruenden gesperrt" (ohne Schemaaenderung): ``PUT is_active=true`` wird dafuer abgelehnt, nur
# ``rotate`` (neues Secret) macht den Token wieder nutzbar.
REVOKED_HASH_PREFIX = "revoked:"


def is_secret_revoked(token: DynDnsToken) -> bool:
    return str(token.token_hash or "").startswith(REVOKED_HASH_PREFIX)


def revoke_secret(token: DynDnsToken) -> None:
    """Deaktiviert den Token und entwertet sein Secret (nur Attribute, kein flush)."""
    token.is_active = False
    token.token_hash = REVOKED_HASH_PREFIX + secrets.token_hex(32)


def token_prefix(plain: str) -> str:
    return plain[: len(TOKEN_PREFIX) + 4]


def token_hostnames(token: DynDnsToken) -> list[str]:
    return [norm_name(str(h)) for h in (token.hostnames or []) if str(h).strip()]


def token_types(token: DynDnsToken) -> list[str]:
    types = [str(t).upper() for t in (token.allowed_types or ALLOWED_TYPES)]
    return [t for t in ALLOWED_TYPES if t in types]


# =====================================================================================================================
# Parsen und Pruefen (rein)
# =====================================================================================================================
def normalize_hostname(raw: str) -> str:
    """Hostname -> ``lower`` + Trailing-Dot. ``ValueError("wildcard")`` bei ``*``, sonst ``ValueError`` bei Unsinn.

    IDN wird nach Punycode umgewandelt; mindestens zwei Labels, hoechstens 253 Zeichen, Labels LDH (1-63 Zeichen,
    kein Bindestrich am Rand).
    """
    if not isinstance(raw, str):
        raise ValueError("invalid")
    h = raw.strip().rstrip(".").lower()
    if not h:
        raise ValueError("empty")
    if "*" in h:
        raise ValueError("wildcard")
    if not h.isascii():
        try:
            h = h.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("idna") from exc
    if len(h) > 253:
        raise ValueError("too_long")
    labels = h.split(".")
    if len(labels) < 2:
        raise ValueError("single_label")
    for label in labels:
        if not _LABEL_RE.fullmatch(label):
            raise ValueError("label")
    return h + "."


def extract_credentials(request: Request) -> Optional[str]:
    """Token aus ``Authorization: Basic`` (Passwort, ersatzweise Benutzer mit Prefix) oder ``Bearer``."""
    hdr = (request.headers.get("authorization") or "").strip()
    if not hdr:
        return None
    scheme, _, value = hdr.partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme == "bearer":
        return value or None
    if scheme != "basic" or not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    user, sep, password = text.partition(":")
    if not sep:
        return None
    if password.startswith(TOKEN_PREFIX):
        return password
    if user.startswith(TOKEN_PREFIX):
        return user
    return password or None


def query_secret_values(request: Request) -> Optional[list[str]]:
    """``None`` = Query sauber; sonst die verdaechtigen Werte (Geheimnis-Parameter oder Wert mit ``dnsmgr_``)."""
    found: list[str] = []
    flagged = False
    for key, value in request.query_params.multi_items():
        k = (key or "").strip().lower()
        v = value or ""
        if k in SECRET_QUERY_PARAMS:
            flagged = True
            found.append(v)
        elif v.strip().startswith(SECRET_VALUE_PREFIX):
            flagged = True
            found.append(v)
    return found if flagged else None


def check_ip_allowed(ip, *, allow_private: bool) -> None:
    """``DynDnsError("badip")`` fuer Loopback, Unspecified, Multicast, Link-Local, Reserved, Broadcast, IPv6 mit
    Scope-ID; private Netze (RFC 1918, CGNAT, ULA) nur mit ``allow_private``."""
    text = str(ip)
    if isinstance(ip, IPv6Address) and ip.scope_id:
        raise DynDnsError("badip", f"IP-Adresse {text} ist nicht erlaubt")
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
        raise DynDnsError("badip", f"IP-Adresse {text} ist nicht erlaubt")
    if isinstance(ip, IPv4Address) and text == "255.255.255.255":
        raise DynDnsError("badip", f"IP-Adresse {text} ist nicht erlaubt")
    if not allow_private and any(ip in net for net in PRIVATE_NETS if net.version == ip.version):
        raise DynDnsError("badip", f"IP-Adresse {text} ist nicht erlaubt (privat)")


@dataclass
class ParsedIps:
    v4: Optional[IPv4Address]
    v6: Optional[IPv6Address]
    source: Literal["param", "client_ip"]

    @property
    def values(self) -> list[str]:
        return [str(x) for x in (self.v4, self.v6) if x is not None]


def _split_values(*raw: Optional[str]) -> list[str]:
    out: list[str] = []
    for r in raw:
        for part in (r or "").split(","):
            v = part.strip()
            if not v or _PLACEHOLDER_RE.fullmatch(v):
                continue
            out.append(v)
    return out


def _parse_one(value: str):
    try:
        ip = ipaddress.ip_address(value.strip("[]"))
    except ValueError as exc:
        raise DynDnsError("badip", f"Ungueltige IP-Adresse: {value[:64]}") from exc
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def parse_ips(myip: Optional[str], myipv4: Optional[str], myipv6: Optional[str], client_ip: Optional[str], *,
              allow_private: bool) -> ParsedIps:
    """IPs aus ``myip``/``myipv4``/``myipv6`` (Komma-Listen, Platzhalter ``<...>`` ignoriert), sonst die Client-IP."""
    values = _split_values(myip, myipv4, myipv6)
    if len(values) > 4:
        raise DynDnsError("badip", "Zu viele IP-Adressen angegeben")
    v4: Optional[IPv4Address] = None
    v6: Optional[IPv6Address] = None
    for value in values:
        ip = _parse_one(value)
        if isinstance(ip, IPv4Address):
            if v4 is not None and v4 != ip:
                raise DynDnsError("badip", "Mehrere IPv4-Adressen angegeben")
            v4 = ip
        else:
            if v6 is not None and v6 != ip:
                raise DynDnsError("badip", "Mehrere IPv6-Adressen angegeben")
            v6 = ip
    if values:
        for ip in (v4, v6):
            if ip is not None:
                check_ip_allowed(ip, allow_private=allow_private)
        return ParsedIps(v4, v6, "param")
    if not client_ip:
        raise DynDnsError("badip", "Keine IP-Adresse angegeben und Client-IP unbekannt")
    try:
        ip = _parse_one(client_ip)
        check_ip_allowed(ip, allow_private=allow_private)
    except DynDnsError as exc:
        raise DynDnsError(
            "badip",
            f"Automatisch erkannte Client-IP {client_ip[:64]} ist nicht oeffentlich – myip angeben oder "
            "TRUST_PROXY_HEADERS pruefen",
        ) from exc
    if isinstance(ip, IPv4Address):
        return ParsedIps(ip, None, "client_ip")
    return ParsedIps(None, ip, "client_ip")


def worst(codes: Iterable[str]) -> str:
    """Schlechtester Code nach ``RESULT_ORDER`` (unbekannte Codes gelten als schlechtester)."""
    rank = {c: i for i, c in enumerate(RESULT_ORDER)}
    lst = [c for c in codes if c]
    if not lst:
        return "nochg"
    return min(lst, key=lambda c: rank.get(c, -1))


# =====================================================================================================================
# Rate-Limits [S6]
# =====================================================================================================================
def _token_key(token_id: int) -> str:
    return f"t{int(token_id)}"


def token_rate_state(token_id: int) -> tuple[Optional[str], int]:
    """Zaehlt eine Anfrage eines gueltigen Tokens. ``(None, 0)`` = erlaubt, sonst ``("911"|"abuse", Retry-After)``.

    Ab ``TOKEN_ABUSE_THRESHOLD`` Anfragen im Fenster wird nicht weiter gezaehlt (begrenzter Speicher).
    """
    key = _token_key(token_id)
    if _req_by_token.count(key) >= TOKEN_ABUSE_THRESHOLD:
        return "abuse", _req_by_token.retry_after(key)
    if _req_by_token.hit(key):
        return "911", _req_by_token.retry_after(key)
    return None, 0


def ip_lock_retry_after(ipk: str) -> Optional[int]:
    """Nur fuer Anfragen ohne gueltigen Token: gesperrte IP -> Sekunden bis zum naechsten Versuch, sonst ``None``.

    Zaehlt die Anfrage im IP-Fenster (120 / 5 min); ueber dem Limit wird nicht weiter gezaehlt.
    """
    if _auth_fail_by_ip.is_limited(ipk):
        return _auth_fail_by_ip.retry_after(ipk)
    if _req_by_ip.count(ipk) >= IP_REQUEST_LIMIT:
        return _req_by_ip.retry_after(ipk)
    _req_by_ip.hit(ipk)
    return None


def record_auth_failure(ipk: str) -> None:
    _auth_fail_by_ip.hit(ipk)


# =====================================================================================================================
# DB-Helfer
# =====================================================================================================================
async def is_enabled(db: AsyncSession) -> bool:
    return await get_bool_setting(db, KEY_ENABLED, True)


async def allow_private(db: AsyncSession) -> bool:
    return await get_bool_setting(db, KEY_ALLOW_PRIVATE, False)


async def find_token_by_plaintext(db: AsyncSession, plaintext: Optional[str]) -> Optional[DynDnsToken]:
    """Token zum Klartext (ohne ``is_active``-Filter, ohne Seiteneffekte)."""
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    res = await db.execute(select(DynDnsToken).where(DynDnsToken.token_hash == hash_token(plaintext)))
    return res.scalar_one_or_none()


async def verify_token(db: AsyncSession, plaintext: Optional[str], *, remote_ip: Optional[str]) -> Optional[DynDnsToken]:
    """Aktiver Token zum Klartext; setzt ``last_used_at``/``last_used_ip`` (nur flush)."""
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    res = await db.execute(
        select(DynDnsToken).where(DynDnsToken.token_hash == hash_token(plaintext), DynDnsToken.is_active.is_(True))
    )
    token = res.scalar_one_or_none()
    if token is None:
        return None
    token.last_used_at = utcnow()
    token.last_used_ip = (remote_ip or "")[:64] or None
    await db.flush()
    return token


async def load_owner(db: AsyncSession, token: Optional[DynDnsToken]) -> Optional[User]:
    """Besitzer des Tokens oder ``None`` (fehlt oder deaktiviert)."""
    if token is None:
        return None
    res = await db.execute(select(User).where(User.id == token.user_id))
    user = res.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def revoke_tokens_in_query(db: AsyncSession, values: list[str], *, client_ip: Optional[str]) -> int:
    """Gueltige DynDNS-Tokens im Query sofort deaktivieren (stehen bereits in Access-/Proxy-Logs) + Audit.

    Panel-Tokens im Query werden nur geloggt. Rueckgabe: Anzahl deaktivierter Tokens.
    """
    revoked = 0
    for value in values:
        v = (value or "").strip()
        if v.startswith(PANEL_TOKEN_PREFIX):
            logger.warning("DynDNS: Panel-Token im Query-String gesendet (gehoert nie in URLs)")
            continue
        token = await find_token_by_plaintext(db, v)
        if token is None or not token.is_active:
            continue
        revoke_secret(token)  # Klartext steht in Logs -> nie wieder gueltig, auch nicht nach Reaktivieren
        token.last_result = "badauth"
        revoked += 1
        logger.warning("DynDNS: Token %s im Query-String gesendet – deaktiviert", token.token_prefix)
        await write_audit(
            db, "DYNDNS_TOKEN_REVOKED", "dyndns_token", token.name, user_id=token.user_id,
            details={"token_id": token.id, "token_prefix": token.token_prefix, "reason": "token_in_query",
                     "ip": client_ip},
        )
    return revoked


async def maybe_audit_failure(*, key: tuple, ttl_sec: float, **audit_kwargs) -> None:
    """Fehler-Audit (eigene Session) hoechstens einmal je ``key`` und ``ttl_sec`` – kein Audit-Spam durch Router."""
    now = time.monotonic()
    last = _failure_audited.get(key)
    if last is not None and now - last < ttl_sec:
        return
    if len(_failure_audited) >= _DEDUPE_MAX_KEYS:
        cutoff = now - max(FAILURE_AUDIT_TTL, AUTH_AUDIT_TTL)
        for k in [k for k, ts in _failure_audited.items() if ts < cutoff]:
            _failure_audited.pop(k, None)
        while len(_failure_audited) >= _DEDUPE_MAX_KEYS:
            _failure_audited.pop(next(iter(_failure_audited)))
    _failure_audited[key] = now
    audit_kwargs.setdefault("status", "error")
    action = audit_kwargs.pop("action")
    resource_type = audit_kwargs.pop("resource_type")
    resource_name = audit_kwargs.pop("resource_name", None)
    await write_audit_detached(action, resource_type, resource_name, **audit_kwargs)


async def audit_auth_failure(*, ipk: str, client_ip: Optional[str], reason: str, hostname: Optional[str],
                             user_id: Optional[int]) -> None:
    await maybe_audit_failure(
        key=("auth", ipk), ttl_sec=AUTH_AUDIT_TTL, action="DYNDNS_AUTH_FAILED", resource_type="dyndns",
        resource_name=(hostname or "")[:255] or None, user_id=user_id,
        details={"ip": client_ip, "reason": reason}, error_message="DynDNS-Anmeldung fehlgeschlagen",
    )


# =====================================================================================================================
# Hostnamen und Token-Verwaltung
# =====================================================================================================================
async def validate_hostnames_for_user(db: AsyncSession, user: User, raw_hostnames: list[str]) -> list[dict]:
    """Normalisiert und prueft Hostnamen gegen Zonen-Index und Schreibrecht von ``user``.

    Fehler -> ``HTTPException`` (400 Format/Doppelt/keine Zone, 403 kein Schreibrecht). Den Zonennamen nennt der
    403-Text nur, wenn ``user`` die Zone lesen darf [S5]. Rueckgabe ``[{"hostname", "zone"}]``.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_hostnames or []:
        shown = str(raw or "").strip()[:100]
        try:
            h = normalize_hostname(str(raw or ""))
        except ValueError as exc:
            if str(exc) == "wildcard":
                raise HTTPException(status_code=400, detail=f"Wildcard-Hostnamen sind nicht erlaubt: {shown}")
            raise HTTPException(status_code=400, detail=f"Ungueltiger Hostname: {shown}")
        if h in seen:
            raise HTTPException(status_code=400, detail=f"Hostname doppelt angegeben: {h}")
        seen.add(h)
        match = await zone_index.find_zone(db, h)
        if match is None:
            raise HTTPException(
                status_code=400,
                detail=f"Hostname {h} liegt in keiner vom Panel verwalteten Zone (nur Server mit 'Speichern: Ja')",
            )
        if not await has_zone_access(db, user, match.zone, write=True):
            if await has_zone_access(db, user, match.zone, write=False):
                detail = f"Kein Schreibrecht auf Zone {match.zone} (Hostname {h})"
            else:
                detail = f"Keine Berechtigung für diesen Hostnamen: {h}"
            raise HTTPException(status_code=403, detail=detail)
        out.append({"hostname": h, "zone": match.zone})
    return out


async def zone_names_snapshot(db: AsyncSession) -> Optional[set[str]]:
    """Alle Zonen der schreibbaren Server (einmal je Anfrage, fuer :func:`hostname_status` vieler Tokens)."""
    try:
        zmap = await zone_index.writable_zone_map(db)
    except Exception:  # noqa: BLE001 - Anzeige, darf die Liste nicht brechen
        logger.debug("DynDNS: Zonen-Index nicht lesbar", exc_info=True)
        return None
    return set().union(*zmap.values()) if zmap else set()


async def hostname_status(db: AsyncSession, token: DynDnsToken, owner: User, *,
                          zone_names: Optional[set[str]] = None,
                          acl_cache: Optional[dict[str, tuple[bool, bool]]] = None) -> list[dict]:
    """Zustand je Hostname: ``ok`` | ``no_zone`` | ``forbidden``; ``zone`` nur mit Leserecht des Besitzers [S5].

    ``zone_names`` (aus :func:`zone_names_snapshot`) und ``acl_cache`` vermeiden bei Listen wiederholte
    Index- und Rechteabfragen.
    """
    if zone_names is None:
        zone_names = await zone_names_snapshot(db)
    cache = acl_cache if acl_cache is not None else {}
    out: list[dict] = []
    for h in token_hostnames(token):
        z = find_matching_zone(h, zone_names or ())
        if not z:
            out.append({"hostname": h, "zone": None, "status": "no_zone"})
            continue
        if z not in cache:
            can_write = await has_zone_access(db, owner, z, write=True)
            can_read = can_write or await has_zone_access(db, owner, z, write=False)
            cache[z] = (can_write, can_read)
        can_write, can_read = cache[z]
        out.append({"hostname": h, "zone": z if can_read else None, "status": "ok" if can_write else "forbidden"})
    return out


def stale_server_list(token: DynDnsToken) -> list[str]:
    """Vereinigung aller veralteten Server des Tokens (fuer die Anzeige)."""
    raw = token.stale_servers
    if not isinstance(raw, dict):
        return []
    out: set[str] = set()
    for servers in raw.values():
        if isinstance(servers, list):
            out.update(str(s) for s in servers)
    return sorted(out)


def serialize_token(t: DynDnsToken, *, hostname_status: Optional[list[dict]] = None,
                    owner_username: Optional[str] = None) -> dict:
    """API-Darstellung eines Tokens – **nie** Hash oder Klartext."""
    out: dict[str, Any] = {
        "id": t.id,
        "name": t.name,
        "token_prefix": t.token_prefix,
        "hostnames": token_hostnames(t),
        "allowed_types": token_types(t),
        "ttl": t.ttl,
        "update_ptr": bool(t.update_ptr),
        "is_active": bool(t.is_active),
        "created_at": iso_utc(t.created_at),
        "updated_at": iso_utc(t.updated_at),
        "last_used_at": iso_utc(t.last_used_at),
        "last_used_ip": t.last_used_ip,
        "last_ip_v4": t.last_ip_v4,
        "last_ip_v6": t.last_ip_v6,
        "last_result": t.last_result,
        "last_changed_at": iso_utc(t.last_changed_at),
        "stale_servers": stale_server_list(t),
        "secret_revoked": is_secret_revoked(t),
        "owner_user_id": t.user_id,
    }
    if hostname_status is not None:
        out["hostname_status"] = hostname_status
    if owner_username is not None:
        out["owner_username"] = owner_username
    return out


def audit_token_details(t: DynDnsToken) -> dict:
    """Audit-Details der Token-Aktionen (nie Klartext/Hash)."""
    return {"token_id": t.id, "token_prefix": t.token_prefix, "owner_user_id": t.user_id,
            "hostnames": token_hostnames(t), "allowed_types": token_types(t), "ttl": t.ttl,
            "update_ptr": bool(t.update_ptr), "is_active": bool(t.is_active)}


async def count_tokens_of_user(db: AsyncSession, user_id: int) -> int:
    return int((await db.execute(select(func.count(DynDnsToken.id)).where(DynDnsToken.user_id == user_id))).scalar()
               or 0)


async def create_token(db: AsyncSession, *, user: User, name: str, hostnames: list[str], allowed_types: list[str],
                       ttl: int, update_ptr: bool) -> tuple[DynDnsToken, str]:
    """Legt einen Token an (nur flush). Rueckgabe ``(token, klartext)`` – der Klartext wird nie gespeichert."""
    plain = generate_token()
    now = utcnow()
    token = DynDnsToken(
        user_id=user.id, name=name.strip()[:100], token_prefix=token_prefix(plain), token_hash=hash_token(plain),
        hostnames=[norm_name(h) for h in hostnames], allowed_types=[t for t in ALLOWED_TYPES if t in allowed_types],
        ttl=int(ttl), update_ptr=bool(update_ptr), is_active=True, created_at=now,
    )
    db.add(token)
    await db.flush()
    return token, plain


async def rotate_token(db: AsyncSession, token: DynDnsToken) -> str:
    """Neues Secret fuer denselben Datensatz; der alte Token ist sofort ungueltig (nur flush)."""
    plain = generate_token()
    token.token_hash = hash_token(plain)
    token.token_prefix = token_prefix(plain)
    token.last_result = None
    token.updated_at = utcnow()
    await db.flush()
    return plain


async def delete_tokens_of_user(db: AsyncSession, user_id: int) -> int:
    """Loescht alle DynDNS-Tokens eines Benutzers (Hard-Delete, wie ``delete_user`` bisher inline). Rueckgabe: Anzahl.

    Fuer ``routers/auth.py::delete_user`` (WS-F10-APP-BE); nur flush, kein Commit, kein Audit (der Aufrufer
    schreibt ``deleted_dyndns_tokens`` in seine Details).
    """
    res = await db.execute(sql_delete(DynDnsToken).where(DynDnsToken.user_id == user_id))
    await db.flush()
    return int(res.rowcount or 0)


async def revoke_tokens_of_user(db: AsyncSession, user_id: int) -> int:
    """Sicherheitssperre aller DynDNS-Tokens eines Benutzers (Kontouebernahme, ``access_revocation.revoke_all``).

    Deaktiviert jeden Token und entwertet sein Secret (:func:`revoke_secret`), auch bereits pausierte Tokens – sonst
    liesse sich ein vom Angreifer angelegter oder gelesener Token spaeter wieder aktivieren. Rueckgabe: Anzahl der
    neu gesperrten Tokens (bereits entwertete zaehlen nicht). Nur flush, kein Commit, kein Audit (der Aufrufer
    schreibt den Audit-Eintrag).
    """
    rows = (await db.execute(select(DynDnsToken).where(DynDnsToken.user_id == user_id))).scalars().all()
    n = 0
    for token in rows:
        if is_secret_revoked(token):
            continue
        revoke_secret(token)
        token.updated_at = utcnow()
        n += 1
    await db.flush()
    return n


def prune_stale(token: DynDnsToken) -> None:
    """``stale_servers``-Eintraege fuer Hostnamen entfernen, die der Token nicht mehr hat."""
    raw = token.stale_servers
    if not isinstance(raw, dict):
        token.stale_servers = None
        return
    keep = set(token_hostnames(token))
    cur = {h: v for h, v in raw.items() if h in keep and v}
    token.stale_servers = cur or None


# =====================================================================================================================
# Update eines Hostnamens
# =====================================================================================================================
def _result(hostname: str) -> dict:
    return {"hostname": hostname, "zone": None, "result": "nochg", "ips": [], "changes": [], "fanout": None,
            "ptr": None, "detail": None}


def _stale_for(token: DynDnsToken, hostname: str) -> set[str]:
    """Persistent als veraltet bekannte Server eines Hostnamens."""
    raw = token.stale_servers
    if not isinstance(raw, dict):
        return set()
    servers = raw.get(hostname)
    return {str(s) for s in servers} if isinstance(servers, list) else set()


def _set_stale(token: DynDnsToken, hostname: str, servers: Iterable[str]) -> None:
    cur = dict(token.stale_servers) if isinstance(token.stale_servers, dict) else {}
    lst = sorted(set(servers))
    if lst:
        cur[hostname] = lst
    else:
        cur.pop(hostname, None)
    token.stale_servers = cur or None


def _remember_ips(token: DynDnsToken, wanted: dict[str, str], *, changed: bool) -> None:
    if "A" in wanted:
        token.last_ip_v4 = wanted["A"][:15]
    if "AAAA" in wanted:
        token.last_ip_v6 = wanted["AAAA"][:45]
    if changed:
        token.last_changed_at = utcnow()


async def _read_states(zone: str, hostname: str, servers: Iterable[str]) -> tuple[dict[str, list], list[str]]:
    """Ist-Stand am Namen je Server (parallel, 3 s). Rueckgabe ``(states, unreachable)``; Server ohne Zone fehlen in
    beiden (die Zone ist dort inzwischen verschwunden)."""

    async def one(srv: str):
        try:
            client = pdns_manager.get_client(srv)
            return srv, "ok", await client.get_rrsets(zone, hostname, timeout=READ_TIMEOUT)
        except PowerDNSAPIError as exc:
            if fanout.zone_not_found_for(exc):
                return srv, "missing", None
            logger.debug("DynDNS: Server %s nicht lesbar (%s)", srv, exc.status_code)
            return srv, "error", None
        except Exception as exc:  # noqa: BLE001 - nicht geladen, Timeout, ...
            logger.debug("DynDNS: Server %s nicht lesbar (%s)", srv, type(exc).__name__)
            return srv, "error", None

    results = await asyncio.gather(*(one(s) for s in servers))
    states = {srv: data for srv, kind, data in results if kind == "ok"}
    unreachable = [srv for srv, kind, _ in results if kind == "error"]
    return states, unreachable


def _matches(state: list, hostname: str, rtype: str, value: str, ttl: int) -> bool:
    snap = rrset_snapshot(state, hostname, rtype)
    if not snap or len(snap["records"]) != 1:
        return False
    rec = snap["records"][0]
    return ptr.canonical_ip(rec["content"]) == value and not rec["disabled"] and int(snap["ttl"]) == int(ttl)


async def _not_loaded_info(db: AsyncSession, zone: str, primary: str) -> dict[str, str]:
    """Nur die ``not loaded``-Hinweise des Fan-outs (konfigurierte, nicht geladene Server [D4])."""
    try:
        _, info = await fanout.writable_targets_for_zone(db, zone, primary)
    except Exception:  # noqa: BLE001 - nur Hinweis
        return {}
    return {k: v for k, v in info.items() if str(v).startswith("not loaded")}


async def _fail(res: dict, code: str, detail: str, *, token: DynDnsToken, owner: User, client_ip: Optional[str],
                reason: Optional[str] = None, zone: Optional[str] = None) -> dict:
    res.update(result=code, detail=detail)
    if code in ("nohost", "badip", "dnserr", "911"):
        await maybe_audit_failure(
            key=(token.id, res["hostname"], code), ttl_sec=FAILURE_AUDIT_TTL, action="DYNDNS_UPDATE",
            resource_type="record", resource_name=res["hostname"], user_id=owner.id, zone_name=zone,
            error_message=detail,
            details={"token_id": token.id, "token_name": token.name, "token_prefix": token.token_prefix,
                     "hostname": res["hostname"], "client_ip": client_ip, "result": code, "reason": reason},
        )
    return res


async def perform_update(db: AsyncSession, *, token: DynDnsToken, owner: User, hostname_raw: str, ips: ParsedIps,
                         client_ip: Optional[str]) -> dict:
    """Aktualisiert einen Hostnamen (Ergebnis im Format ``DynDnsHostResult``). Wirft nur bei unerwarteten Fehlern."""
    res = _result(str(hostname_raw or "")[:255])
    ctx = {"token": token, "owner": owner, "client_ip": client_ip}
    try:
        h = normalize_hostname(str(hostname_raw or ""))
    except ValueError:
        res["result"], res["detail"] = "notfqdn", MSG_INVALID_HOST
        return res
    res["hostname"] = h
    if h not in token_hostnames(token):
        return await _fail(res, "nohost", MSG_NOT_IN_SCOPE, reason="not_in_scope", **ctx)

    allowed = token_types(token)
    wanted: dict[str, str] = {}
    skipped: list[dict] = []
    for rtype, ip in (("A", ips.v4), ("AAAA", ips.v6)):
        if ip is None:
            continue
        if rtype in allowed:
            wanted[rtype] = str(ip)
        else:
            skipped.append({"type": rtype, "status": "skipped", "old": [], "new": str(ip), "reason": "type_not_allowed"})
    res["ips"] = list(wanted.values())
    res["changes"] = list(skipped)
    if not wanted:
        return await _fail(res, "badip", MSG_NO_TYPE, reason="type_not_allowed", **ctx)

    match = await zone_index.find_zone(db, h)
    if match is None or not match.servers:
        return await _fail(res, "nohost", MSG_NO_ZONE.format(h=h), reason="no_zone", **ctx)
    zone = match.zone
    if not await has_zone_access(db, owner, zone, write=True):
        readable = await has_zone_access(db, owner, zone, write=False)
        if readable:
            res["zone"] = zone
        detail = MSG_NO_WRITE_ZONE.format(z=zone) if readable else MSG_NO_WRITE_HOST
        return await _fail(res, "nohost", detail, reason="forbidden", zone=zone, **ctx)
    res["zone"] = zone

    # [D5] Ist-Stand je schreibbarem Server mit der Zone
    states, unreachable = await _read_states(zone, h, match.servers)
    if not states:
        return await _fail(res, "911", MSG_UNREACHABLE, reason="unreachable", zone=zone, **ctx)
    if any(rrset_snapshot(state, h, "CNAME") for state in states.values()):
        return await _fail(res, "dnserr", MSG_CNAME, reason="cname_conflict", zone=zone, **ctx)

    ttl = int(token.ttl or DEFAULT_TTL)
    ordered = [s for s in match.servers if s in states]
    # Referenz fuer good/nochg, changes[].old und PTR: der erste erreichbare Server, der nicht als veraltet bekannt
    # ist (sonst wuerde die Reparatur eines veralteten ersten Servers als echte Aenderung gemeldet) [D5].
    known_stale = _stale_for(token, h)
    ref = states[next((s for s in ordered if s not in known_stale), ordered[0])]
    changes = []
    for rtype, value in wanted.items():
        before = rrset_snapshot(ref, h, rtype)
        same = _matches(ref, h, rtype, value, ttl)
        changes.append({"type": rtype, "status": "unchanged" if same else "updated",
                        "old": [r["content"] for r in (before or {}).get("records") or []], "new": value})
    res["changes"] = changes + skipped
    real_types = [c["type"] for c in changes if c["status"] == "updated"]
    deviating = [s for s in ordered if any(not _matches(states[s], h, t, v, ttl) for t, v in wanted.items())]

    if not deviating:
        _set_stale(token, h, unreachable)
        _remember_ips(token, wanted, changed=False)
        res["result"] = "nochg"
        return res

    repair = not real_types
    dev_types = [t for t in wanted if any(not _matches(states[s], h, t, wanted[t], ttl) for s in deviating)]
    rrsets = [{"name": h, "type": t, "ttl": ttl, "changetype": "REPLACE",
               "records": [{"content": wanted[t], "disabled": False}]} for t in dev_types]
    primary = deviating[0]
    targets = [(s, pdns_manager.get_client(s)) for s in deviating]
    before_of = {s: {(h, t): rrset_snapshot(states[s], h, t) for t in dev_types} for s in deviating}
    fan = await fanout.apply_rrsets(
        db, primary, zone, rrsets, targets=targets, info=await _not_loaded_info(db, zone, primary),
        require_primary=False, timeout=WRITE_TIMEOUT, before_state=before_of[primary],
    )
    errored = [s for s, st in fan.results.items() if str(st).startswith("error")]
    _set_stale(token, h, list(unreachable) + errored)
    summary = fan.summary
    res["fanout"] = summary
    if repair:
        res["repair"] = True

    if not fan.any_success:
        if fan.all_4xx:
            first = next(iter(fan.errors.values()))
            code, detail = "dnserr", MSG_PDNS_REJECTED.format(d=first.pdns_message[:300])
        else:
            code, detail = "911", MSG_UNREACHABLE
        if repair:
            # Der Referenzstand stimmt bereits – fuer den Client unveraendert; die gescheiterte Reparatur wird
            # (dedupliziert) protokolliert und beim naechsten Ping erneut versucht.
            await maybe_audit_failure(
                key=(token.id, h, "repair"), ttl_sec=FAILURE_AUDIT_TTL, action="DYNDNS_UPDATE",
                resource_type="record", resource_name=h, user_id=owner.id, zone_name=zone, error_message=detail,
                details={"token_id": token.id, "token_name": token.name, "token_prefix": token.token_prefix,
                         "hostname": h, "client_ip": client_ip, "result": "nochg", "reason": "repair_failed",
                         "repair": True, "fanout": summary},
            )
            _remember_ips(token, wanted, changed=False)
            res["result"] = "nochg"
            return res
        return await _fail(res, code, detail, reason="pdns_error", zone=zone, **ctx)

    # Audit v2 aus Sicht des ersten erfolgreich geschriebenen Servers
    audit_server = primary if fan.results.get(primary) in fanout.SUCCESS_STATUSES else next(
        s for s in deviating if fan.results.get(s) in fanout.SUCCESS_STATUSES)
    keys = [(h, t) for t in dev_types]
    before_map = before_of[audit_server]
    payload = fan.per_server_rrsets.get(audit_server) or rrsets
    after, src = await capture_after(pdns_manager.get_client(audit_server), zone, keys,
                                     fallback=simulate_patch(before_map, rrsets=payload))
    if audit_server == primary and fan.primary_outcome == "verified_after_timeout":
        src = AFTER_REREAD
    changes_v2 = build_changes(before_map, after, origin=zone)

    ptr_results = None
    if not repair and token.update_ptr:
        ops: list = []
        for rtype in real_types:
            before = rrset_snapshot(ref, h, rtype)
            before_vals = [(r["content"], r["disabled"]) for r in (before or {}).get("records") or []]
            ops += ptr.ops_for_rrset_change(h, before_vals, [(wanted[rtype], False)], ttl)
        ptr_results = await ptr.sync_ptrs(
            db, ops, acl_user=owner, actor_user_id=owner.id,
            source={"action": "DYNDNS_UPDATE", "zone": zone, "name": h, "server": None},
        )
        res["ptr"] = ptr_results

    extra: dict[str, Any] = {
        "primary_outcome": fan.primary_outcome if audit_server == primary else "ok",
        "token_id": token.id, "token_name": token.name, "token_prefix": token.token_prefix,
        "client_ip": client_ip, "ip_source": ips.source,
    }
    if repair:
        extra["repair"] = True
    if ptr_results is not None:
        extra["ptr"] = ptr.compact(ptr_results)
    details = history_details(zone, changes_v2, fanout=summary, after_source=src,
                              legacy={"type": ",".join(dev_types)}, extra=extra)
    audit = await write_audit(db, "DYNDNS_UPDATE", "record", h, user_id=owner.id, details=details,
                              server_name=audit_server, zone_name=zone)
    if not repair:
        from app.services.webhook_outbox import enqueue_event

        data: dict[str, Any] = {
            "server": audit_server, "zone": zone, "hostname": h, "name": h, "type": ",".join(real_types),
            "ttl": ttl, "old": sorted({v for c in changes if c["status"] == "updated" for v in c["old"]}),
            "new": [wanted[t] for t in real_types],
            "token": {"id": token.id, "prefix": token.token_prefix, "name": token.name},
            **webhook_changes(changes_v2), "fanout": summary,
        }
        if ptr_results is not None:
            data["ptr"] = ptr.compact(ptr_results)
        await enqueue_event(db, "dyndns.updated", actor=owner, zone=zone, server=audit_server, data=data,
                            audit_log_id=audit.id if audit else None)
    _remember_ips(token, wanted, changed=not repair)
    res["result"] = "nochg" if repair else "good"
    return res
