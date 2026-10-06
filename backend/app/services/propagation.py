"""Propagations-Check (F12 5.7, Bauplan WS-F12F13-BE [D9, D14]).

Vergleicht auf Knopfdruck, ob eine Zone ueberall angekommen ist:

- **Panel-Server** ueber die PowerDNS-API (``serial``, ``edited_serial``, ``notified_serial``, ``kind``); Referenz
  ist der Server aus der URL. Ausgelieferte Serial = ``edited_serial`` (SOA-EDIT), sonst ``serial``.
- **Autoritative Nameserver** der Zone (Apex-NS, Glue aus der Zone bevorzugt) per DNS mit RD=0,
- **oeffentliche Resolver** (Admin-Liste) per DNS mit RD=1.

Externe DNS-Abfragen laufen nur nach Admin-Opt-in (``propagation_enabled``); der API-Vergleich der Panel-Server
ist immer verfuegbar.

Getrennte Backends [D9]: Liegt die Zone auf mehr als einem *schreibbaren* Panel-Server, sind abweichende Serials
normal (eigenes SOA-EDIT-API je Datenbank). Dann entscheidet bei Peers mit abweichender Serial der
Inhalts-Fingerprint (``rrsets.zone_fingerprint``, ohne SOA/DNSSEC-Typen), die Serial ist nur Info (Notiz
``separate_backend``). DNS-Zeilen gelten als ``match``, wenn ihr SOA-Serial der Serial (``edited_serial`` oder
``serial``) irgendeines Panel-Servers mit der Zone entspricht; bei Record-Checks entscheidet dort ``record_match``.
Strenger Serial-Vergleich bleibt fuer Peers mit gleicher Serial (gemeinsame DB), fuer Secondaries (``kind`` Slave
oder Consumer) und wenn die Zone nur auf einem schreibbaren Server liegt. Secondaries zaehlen nie als getrenntes
Backend (auch wenn ihr Server schreibbar ist) und ihre Serial gilt nicht als gueltige Panel-Serial fuer DNS-Zeilen:
ein AXFR-Secondary, der hinterherhinkt, darf einen ebenso veralteten Nameserver nicht als ``match`` erscheinen lassen.

Zeitbudget [D14]: Gesamtbudget ``TOTAL_TIMEOUT`` (8 s) je Check. Die Referenzzone wird per
``asyncio.wait_for`` hoechstens ``REFERENCE_TIMEOUT`` (4 s) abgewartet und laesst dabei mindestens
``NS_MIN_BUDGET`` (2 s) fuer die Nameserver-Pruefung uebrig. Nameserver mit Glue in der Zone werden sofort gefragt,
die Aufloesung der uebrigen NS-Namen laeuft parallel dazu, und jede Adresse wird gefragt, sobald sie bekannt ist.
Am Ende werden offene Abfragen hart abgebrochen (``timeout``/``deadline``).

Schutz (F12 7.2): nur IP-Literale bzw. Adressen aus Zonendaten, Port 53, keine Loopback/Link-Local/Multicast/
reservierten Ziele, hoechstens 12 NS-Adressen, 24 parallele Abfragen, 10 Checks je Benutzer und Minute (gecachte
Ergebnisse zaehlen nicht), 4 parallele Checks im Prozess, Ergebnis-Cache 10 s. Antworten gehen nur als Serial bzw.
normalisierte Werte zurueck.

Oeffentliche Helfer fuer andere Workstreams: ``load_settings(db)`` und ``query_dns(name, rdtype, server, timeout)``
(F4-C nutzt sie fuer die DNSKEY-Pruefung; ``DnsAnswer.key_tags`` liefert die Key-Tags einer DNSKEY-Antwort),
``consume_rate(user_id)`` (gemeinsames Limit je Benutzer fuer alle DNS-Pruefungen) und ``dns_slot(db)``.

DB-Verbindung und Wartezeit (Review-Fund L6): Wer auf die globale Semaphore oder auf das Netz wartet, darf keine
Verbindung aus dem DB-Pool halten. ``release_db(db)`` beendet deshalb die offene Transaktion der Request-Session
(Commit; die Session ist mit ``expire_on_commit=False`` danach weiter nutzbar und holt sich bei der naechsten
Abfrage eine neue Verbindung). ``dns_slot(db)`` = ``release_db`` + globale Semaphore; ``check_zone(..., db=db)``
nutzt denselben Ablauf. Aufrufer lesen alles Noetige aus der DB *vor* dem Slot und schreiben erst *danach*.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Optional

import dns.asyncquery
import dns.asyncresolver
import dns.dnssec
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.rcode
import dns.rdataclass
import dns.rdatatype

from app.core import metrics as prom
from app.core.names import normalize_zone_name
from app.core.timeutil import iso_utc, utcnow
from app.schemas.propagation import (
    PropagationExternal,
    PropagationRecordInfo,
    PropagationResponse,
    PropagationSource,
    PropagationSummary,
)
from app.services import rrsets
from app.services.fanout import zone_not_found_for
from app.services.pdns_client import PowerDNSAPIError, pdns_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------------------------------------------------
REFERENCE_TIMEOUT = 4.0  # Referenzzone (volle Zone)
PANEL_TIMEOUT = 3.0  # Peers
NS_RESOLVE_TIMEOUT = 2.0  # Aufloesung der NS-Hostnamen
NS_MIN_BUDGET = 2.0  # so viel bleibt nach der Referenz mindestens fuer die NS-Pruefung [D14]
DNS_QUERY_TIMEOUT = 3.0  # je DNS-Abfrage
TOTAL_TIMEOUT = 8.0  # Gesamtbudget je Check
MAX_NS_TARGETS = 12
MAX_RESOLVERS = 10
DNS_CONCURRENCY = 24  # parallele DNS-Abfragen je Check
RESULT_CACHE_TTL = 10.0
RESULT_CACHE_MAX = 200
RATE_WINDOW = 60.0
RATE_MAX = 10  # nicht gecachte Checks je Benutzer und Minute
GLOBAL_CONCURRENCY = 4  # parallele Checks im Prozess
DNS_PAYLOAD = 1232

DEFAULT_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
KNOWN_RESOLVER_LABELS = {
    "1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "2606:4700:4700::1111": "Cloudflare",
    "2606:4700:4700::1001": "Cloudflare",
    "8.8.8.8": "Google", "8.8.4.4": "Google", "2001:4860:4860::8888": "Google", "2001:4860:4860::8844": "Google",
    "9.9.9.9": "Quad9", "149.112.112.112": "Quad9", "2620:fe::fe": "Quad9", "2620:fe::9": "Quad9",
}

# Reihenfolge wie die Typ-Liste im Frontend (ALL_RECORD_TYPE_KEYS); SOA wird ohnehin geprueft, ALIAS/LUA sind
# dynamisch, DNSKEY steht nicht in den API-RRsets, RRSIG/NSEC* werden signiert erzeugt.
COMPARABLE_TYPES = (
    "A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "PTR", "TLSA", "SSHFP", "HTTPS", "SVCB", "NAPTR",
    "DNAME", "LOC", "SPF", "OPENPGPKEY", "DS",
)

ERROR_TEXTS = {
    "timeout": "Zeitüberschreitung",
    "deadline": "Gesamtzeit (8 s) überschritten",
    "unreachable": "Nicht erreichbar",
    "zone_missing": "Zone auf diesem Server nicht vorhanden",
    "not_loaded": "Server nicht geladen (API-Key fehlt oder ist nicht lesbar)",
    "api_error": "PowerDNS-API-Fehler",
    "refused": "Anfrage abgelehnt (REFUSED) – Server ist für die Zone nicht zuständig",
    "servfail": "Serverfehler (SERVFAIL)",
    "nxdomain": "Zone existiert dort nicht (NXDOMAIN)",
    "not_authoritative": "Antwort nicht autoritativ – möglicherweise Lame Delegation",
    "no_soa": "Keine SOA-Antwort erhalten",
    "bad_response": "Ungültige DNS-Antwort",
    "network_error": "Netzwerkfehler",
    "ns_unresolvable": "Nameserver-Name nicht auflösbar",
    "ipv6_disabled": "IPv6-Abfragen deaktiviert",
    "address_not_allowed": "Adresse wird nicht abgefragt (Loopback/Link-Local/Multicast/reserviert)",
}
_ERROR_STATUS = {"timeout": "timeout", "deadline": "timeout", "zone_missing": "skipped", "not_loaded": "skipped",
                 "ipv6_disabled": "skipped", "address_not_allowed": "skipped"}

NOTE_CODES = (
    "notify_pending", "separate_backend", "content_same_serial_differs", "resolver_cache", "secondary_lagging",
    "lua_not_comparable", "record_query_failed", "truncated_targets", "ns_from_glue",
)

KEY_ENABLED = "propagation_enabled"
KEY_AUTH = "propagation_check_authoritative"
KEY_IPV6 = "propagation_ipv6"
KEY_RESOLVERS = "propagation_resolvers"
SETTING_KEYS = (KEY_ENABLED, KEY_AUTH, KEY_IPV6, KEY_RESOLVERS)

# Typen, die beim Inhaltsvergleich zwischen Panel-Servern nicht zaehlen (Serial/Signaturen/Schluessel sind je
# Backend verschieden) – identisch mit rrsets.zone_fingerprint, damit content_match und Fingerprint uebereinstimmen.
CONTENT_EXCLUDE = frozenset(rrsets.ZONE_FINGERPRINT_EXCLUDE) | rrsets.DNSSEC_AUTO_TYPES

_MSG_BAD_RESOLVER = "Ungültige Resolver-Adresse: {v} (nur IPv4/IPv6-Adressen erlaubt)."
_MSG_BLOCKED_RESOLVER = "Loopback-, Multicast-, Link-Local- und unspezifische Adressen sind als Resolver nicht erlaubt: {v}"
_MSG_TOO_MANY = "Höchstens 10 Resolver erlaubt."


# ---------------------------------------------------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PropagationSettings:
    enabled: bool = False
    check_authoritative: bool = True
    ipv6: bool = False
    resolvers: tuple[str, ...] = tuple(DEFAULT_RESOLVERS)

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "check_authoritative": self.check_authoritative, "ipv6": self.ipv6,
                "resolvers": list(self.resolvers)}


@dataclass
class DnsAnswer:
    rcode: Optional[str] = None
    aa: Optional[bool] = None
    soa_serial: Optional[int] = None
    ttl: Optional[int] = None
    values: Optional[list[str]] = None  # Record-Abfrage: normalisierte Werte; [] bei NXDOMAIN/NODATA
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None
    key_tags: Optional[list[int]] = None  # nur DNSKEY-Abfragen (F4-C)


class PropagationReferenceError(Exception):
    """Referenz-Server liefert die Zone nicht (kind: zone_missing|unreachable|timeout|api_error)."""

    def __init__(self, kind: str, exc: Optional[PowerDNSAPIError] = None):
        super().__init__(kind)
        self.kind = kind
        self.exc = exc

    @property
    def pdns_message(self) -> str:
        return self.exc.pdns_message if self.exc is not None else ""


class PropagationRateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = int(retry_after)


# ---------------------------------------------------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------------------------------------------------
def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() == "true"


def _ip(value: str):
    """``ipaddress``-Objekt; IPv4-gemappte IPv6-Adressen werden fuer die Sperrpruefung entpackt."""
    ip = ipaddress.ip_address(value)
    return ip


def _effective(ip):
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _is_blocked_resolver(ip) -> bool:
    e = _effective(ip)
    return any(x.is_loopback or x.is_multicast or x.is_link_local or x.is_unspecified for x in (ip, e))


def validate_resolver_list(values: Optional[Iterable[Any]]) -> list[str]:
    """Normalisierte, deduplizierte Resolver-Liste (Reihenfolge bleibt); ``ValueError`` mit deutschem Text."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        v = raw.strip() if isinstance(raw, str) else str(raw if raw is not None else "").strip()
        if not v:
            continue
        shown = v[:100]
        try:
            ip = _ip(v)
        except ValueError:
            raise ValueError(_MSG_BAD_RESOLVER.format(v=shown)) from None
        if getattr(ip, "scope_id", None):
            raise ValueError(_MSG_BAD_RESOLVER.format(v=shown))
        if _is_blocked_resolver(ip):
            raise ValueError(_MSG_BLOCKED_RESOLVER.format(v=shown))
        s = str(ip)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    if len(out) > MAX_RESOLVERS:
        raise ValueError(_MSG_TOO_MANY)
    return out


def _parse_resolvers(raw: Optional[str]) -> tuple[str, ...]:
    if raw is None:
        return tuple(DEFAULT_RESOLVERS)
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("keine Liste")
    except (ValueError, TypeError):
        logger.warning("Einstellung %s ist kein gueltiges JSON – nutze die Standard-Resolver", KEY_RESOLVERS)
        return tuple(DEFAULT_RESOLVERS)
    out: list[str] = []
    for item in data:
        try:
            for v in validate_resolver_list([item]):
                if v not in out:
                    out.append(v)
        except ValueError as exc:
            logger.warning("Gespeicherter Resolver uebersprungen: %s", exc)
    if len(out) > MAX_RESOLVERS:
        logger.warning("Mehr als %d Resolver gespeichert – nur die ersten werden gefragt", MAX_RESOLVERS)
        out = out[:MAX_RESOLVERS]
    return tuple(out)


async def load_settings(db) -> PropagationSettings:
    """Aktuelle Einstellungen (fehlende Keys = Defaults; fehlerhafte Resolver werden uebersprungen, nie geworfen)."""
    from app.services.system_settings import get_settings

    vals = await get_settings(db, list(SETTING_KEYS))
    return PropagationSettings(
        enabled=_parse_bool(vals.get(KEY_ENABLED), False),
        check_authoritative=_parse_bool(vals.get(KEY_AUTH), True),
        ipv6=_parse_bool(vals.get(KEY_IPV6), False),
        resolvers=_parse_resolvers(vals.get(KEY_RESOLVERS)),
    )


async def save_settings(db, *, enabled: Optional[bool] = None, check_authoritative: Optional[bool] = None,
                        ipv6: Optional[bool] = None, resolvers: Optional[list[str]] = None) -> dict[str, dict]:
    """Validiert und schreibt (nur flush). Rueckgabe: ``{feld: {"from": alt, "to": neu}}`` der geaenderten Felder."""
    from app.services.system_settings import set_settings

    new_resolvers = validate_resolver_list(resolvers) if resolvers is not None else None
    current = await load_settings(db)
    changed: dict[str, dict] = {}
    writes: dict[str, object] = {}
    for fld, key, new in (("enabled", KEY_ENABLED, enabled), ("check_authoritative", KEY_AUTH, check_authoritative),
                          ("ipv6", KEY_IPV6, ipv6)):
        old = getattr(current, fld)
        if new is not None and bool(new) != old:
            changed[fld] = {"from": old, "to": bool(new)}
            writes[key] = bool(new)
    if new_resolvers is not None and list(current.resolvers) != new_resolvers:
        changed["resolvers"] = {"from": list(current.resolvers), "to": new_resolvers}
        writes[KEY_RESOLVERS] = json.dumps(new_resolvers)
    if writes:
        await set_settings(db, writes)
        invalidate_caches()
    return changed


# ---------------------------------------------------------------------------------------------------------------------
# Reine Helfer
# ---------------------------------------------------------------------------------------------------------------------
def serial_relation(a: int, b: int) -> str:
    """Serial ``a`` relativ zu ``b`` (erwartet) nach RFC 1982: ``equal`` | ``behind`` | ``ahead``."""
    if a == b:
        return "equal"
    d = (int(a) - int(b)) % (2 ** 32)
    return "ahead" if 0 < d < 2 ** 31 else "behind"


def resolve_record_name(name: str, zone_norm: str) -> str:
    """``@``/leer -> Zone; relativ -> ``name.zone``; FQDN lower + Punkt. ``ValueError`` (Text fuer 422)."""
    n = (name or "").strip()
    if n in ("", "@"):
        return zone_norm
    if not n.endswith("."):
        n = f"{n}.{zone_norm}"
    fqdn = n.lower()
    if fqdn != zone_norm and not fqdn.endswith("." + zone_norm):
        raise ValueError(f"Der Name {fqdn} liegt nicht in der Zone {zone_norm}")  # zone_norm endet mit Punkt
    try:
        dns.name.from_text(fqdn)
    except dns.exception.DNSException:
        raise ValueError(f"Der Name {fqdn} ist kein gültiger DNS-Name.") from None
    return fqdn


def target_allowed(ip: str, ipv6: bool) -> Optional[str]:
    """``None`` (erlaubt) | ``ipv6_disabled`` | ``address_not_allowed``. Private Adressen (RFC 1918/ULA) sind erlaubt."""
    try:
        addr = _ip(ip)
    except ValueError:
        return "address_not_allowed"
    if addr.version == 6 and not ipv6:
        return "ipv6_disabled"
    e = _effective(addr)
    for x in (addr, e):
        if x.is_loopback or x.is_link_local or x.is_multicast or x.is_unspecified or x.is_reserved:
            return "address_not_allowed"
    return None


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def query_dns(name: str, rdtype: str, server: str, timeout: float = DNS_QUERY_TIMEOUT, *,
                    recursion: bool = False, origin: Optional[str] = None, port: int = 53) -> DnsAnswer:
    """Eine DNS-Abfrage (UDP, bei TC automatisch TCP) an ``server`` (IP-Literal), ohne Exceptions.

    ``recursion=False`` loescht das RD-Bit (autoritative Ziele). Ergebnis: RCODE, AA-Flag, SOA-Serial/TTL (SOA)
    bzw. normalisierte Werte (``rrsets.content_key``) des RRsets ``(name, rdtype)``; bei DNSKEY zusaetzlich die
    Key-Tags. Fehler: ``error_code`` ``timeout`` | ``network_error`` | ``bad_response``. Ungueltige Namen/Typen
    werfen ``ValueError`` (Programmierfehler, nicht Nutzereingabe).
    """
    t = (rdtype or "").strip().upper()
    qname = dns.name.from_text(name)
    rdt = dns.rdatatype.from_text(t)
    q = dns.message.make_query(qname, rdt, use_edns=0, payload=DNS_PAYLOAD)
    if not recursion:
        q.flags &= ~dns.flags.RD
    t0 = time.monotonic()
    try:
        resp, _tcp = await asyncio.wait_for(
            dns.asyncquery.udp_with_fallback(q, server, timeout=timeout, port=port), timeout + 0.5
        )
    except (dns.exception.Timeout, asyncio.TimeoutError):
        return DnsAnswer(error_code="timeout", latency_ms=_ms(t0))
    except OSError:
        return DnsAnswer(error_code="network_error", latency_ms=_ms(t0))
    except (dns.exception.DNSException, ValueError):
        return DnsAnswer(error_code="bad_response", latency_ms=_ms(t0))
    latency = _ms(t0)
    try:
        ans = DnsAnswer(rcode=dns.rcode.to_text(resp.rcode()), aa=bool(resp.flags & dns.flags.AA), latency_ms=latency)
        rr = resp.get_rrset(resp.answer, qname, dns.rdataclass.IN, rdt)
        if t == "SOA":
            if rr is not None and len(rr):
                ans.soa_serial = int(rr[0].serial)
                ans.ttl = int(rr.ttl)
            return ans
        if ans.rcode in ("NOERROR", "NXDOMAIN"):
            o = origin or name
            ans.values = sorted(rrsets.content_key(t, rd.to_text(), o) for rd in rr) if rr is not None else []
            if rr is not None:
                ans.ttl = int(rr.ttl)
                if t == "DNSKEY":
                    ans.key_tags = sorted(int(dns.dnssec.key_id(rd)) for rd in rr)
        return ans
    except Exception:  # noqa: BLE001 - kaputte Antwort nie als 500 an den Nutzer
        return DnsAnswer(error_code="bad_response", latency_ms=latency)


async def resolve_ns_addresses(ns_name: str, *, ipv6: bool, timeout: float) -> tuple[list[str], Optional[str]]:
    """A (und AAAA bei ``ipv6``) eines NS-Namens ueber den System-Resolver; leer -> ``([], "ns_unresolvable")``."""
    try:
        res = dns.asyncresolver.Resolver()
    except Exception as exc:  # noqa: BLE001 - z. B. keine /etc/resolv.conf
        logger.debug("Kein System-Resolver fuer die NS-Aufloesung: %s", type(exc).__name__)
        return [], "ns_unresolvable"
    types = ["A", "AAAA"] if ipv6 else ["A"]
    results = await asyncio.gather(
        *(res.resolve(ns_name, t, lifetime=timeout, search=False) for t in types), return_exceptions=True
    )
    ips: list[str] = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        for rd in r:
            addr = getattr(rd, "address", None)
            if addr and addr not in ips:
                ips.append(addr)
    return (ips, None) if ips else ([], "ns_unresolvable")


def _active_contents(snap: Optional[dict]) -> list[str]:
    return [r["content"] for r in (snap or {}).get("records", []) if not r.get("disabled")]


def extract_reference(zone_json: dict, zone_norm: str) -> dict:
    """Serials, Art, Apex-NS (sortiert) und Glue (``{ns: [ips]}`` fuer NS innerhalb der Zone) aus der Referenzzone."""
    serial_raw = int(zone_json.get("serial") or 0)
    expected = int(zone_json.get("edited_serial") or serial_raw)
    notified = zone_json.get("notified_serial")
    ns = sorted({normalize_zone_name(c) for c in _active_contents(rrsets.rrset_snapshot(zone_json, zone_norm, "NS"))})
    glue: dict[str, list[str]] = {}
    for n in ns:
        if n == zone_norm or n.endswith("." + zone_norm):
            ips: list[str] = []
            for t in ("A", "AAAA"):
                for c in _active_contents(rrsets.rrset_snapshot(zone_json, n, t)):
                    c = c.strip()
                    if c and c not in ips:
                        ips.append(c)
            if ips:
                glue[n] = ips
    return {
        "serial_raw": serial_raw,
        "expected": expected,
        "notified": int(notified) if isinstance(notified, (int, float)) else None,
        "kind": zone_json.get("kind"),
        "nameservers": ns,
        "glue": glue,
    }


def _record_values(zone_json: Optional[dict], fqdn: str, rtype: str, origin: str) -> list[str]:
    snap = rrsets.rrset_snapshot(zone_json, fqdn, rtype)
    return sorted(rrsets.content_key(rtype, c, origin) for c in _active_contents(snap))


def expected_record(zone_json: dict, fqdn: str, rtype: str, origin: str) -> tuple[list[str], bool]:
    """Erwartete Werte (normalisiert, ohne deaktivierte) und ob DNS-Antworten vergleichbar sind.

    Nicht vergleichbar: ein LUA-Record am Namen fuer diesen Typ bzw. ein ALIAS am Namen fuer A/AAAA (die Antwort
    wird dann zur Laufzeit berechnet).
    """
    values = _record_values(zone_json, fqdn, rtype, origin)
    comparable = True
    for c in _active_contents(rrsets.rrset_snapshot(zone_json, fqdn, "LUA")):
        if c.strip().upper().startswith(rtype.upper() + " "):
            comparable = False
    if rtype in ("A", "AAAA") and _active_contents(rrsets.rrset_snapshot(zone_json, fqdn, "ALIAS")):
        comparable = False
    return values, comparable


def zone_content_index(zone_json: Optional[dict], origin: str) -> dict[tuple[str, str], str]:
    """``{(name, TYPE): fingerprint}`` ohne SOA und DNSSEC-Typen (wie ``rrsets.zone_fingerprint``)."""
    return {
        key: rrsets.snapshot_fingerprint(snap, key[1], origin)
        for key, snap in rrsets.index_rrsets(zone_json).items()
        if key[1] not in CONTENT_EXCLUDE
    }


def compare_content(ref_idx: dict, peer_idx: dict) -> tuple[bool, int, list[str]]:
    differing = sorted(k for k in set(ref_idx) | set(peer_idx) if ref_idx.get(k) != peer_idx.get(k))
    return (not differing, len(differing), [f"{n} {t}" for n, t in differing[:5]])


def _classify_pdns_error(exc: PowerDNSAPIError) -> str:
    if zone_not_found_for(exc):
        return "zone_missing"
    if exc.status_code == 504:
        return "timeout"
    if exc.status_code == 503 or getattr(exc, "transport_error", False):
        return "unreachable"
    return "api_error"


def _error_text(code: str, *, is_admin: bool = False, exc: Optional[PowerDNSAPIError] = None) -> str:
    text = ERROR_TEXTS.get(code, code)
    if code == "api_error" and is_admin and exc is not None and exc.pdns_message:
        text = f"{text}: {exc.pdns_message[:200]}"
    return text


# ---------------------------------------------------------------------------------------------------------------------
# Prozesszustand (Cache, Rate-Limit, Semaphore je Event-Loop)
# ---------------------------------------------------------------------------------------------------------------------
_cache: "OrderedDict[tuple, tuple[float, PropagationResponse]]" = OrderedDict()
_rate: dict[int, deque] = {}
_semaphores: dict[int, asyncio.Semaphore] = {}


def _global_semaphore() -> asyncio.Semaphore:
    """Semaphore je Event-Loop (Tests wechseln die Loop; nie ueber Loops teilen)."""
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(id(loop))
    if sem is None:
        _semaphores.clear()
        sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)
        _semaphores[id(loop)] = sem
    return sem


def invalidate_caches() -> None:
    """Ergebnis-Cache leeren (nach Aenderung der Einstellungen)."""
    _cache.clear()


def reset_for_tests() -> None:
    _cache.clear()
    _rate.clear()
    _semaphores.clear()


def _cache_get(key: tuple) -> Optional[PropagationResponse]:
    hit = _cache.get(key)
    if hit is None:
        return None
    if time.monotonic() - hit[0] >= RESULT_CACHE_TTL:
        _cache.pop(key, None)
        return None
    return hit[1]


def _cache_put(key: tuple, value: PropagationResponse) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > RESULT_CACHE_MAX:
        _cache.popitem(last=False)


async def release_db(db) -> None:
    """Gibt die Pool-Verbindung der Request-Session vor einer Warte-/Netzphase frei (L6).

    Commit statt Close: bisher Gelesenes bleibt gueltig (``expire_on_commit=False``), spaetere Abfragen und
    Schreibzugriffe laufen in einer neuen Transaktion. Ohne offene Transaktion passiert nichts. Nur aufrufen,
    solange der Handler noch nichts geschrieben hat, das bei einem spaeteren Fehler zurueckgerollt werden muesste.
    """
    if db is None:
        return
    in_tx = getattr(db, "in_transaction", None)
    if callable(in_tx) and not in_tx():
        return
    await db.commit()


@asynccontextmanager
async def dns_slot(db=None):
    """Platz fuer eine DNS-Pruefung (globale Parallelitaet ``GLOBAL_CONCURRENCY``); gibt vorher die DB frei (L6)."""
    await release_db(db)
    async with _global_semaphore():
        yield


def consume_rate(user_id: int) -> None:
    """Zaehlt eine nicht gecachte DNS-Pruefung des Benutzers; ``PropagationRateLimited`` ueber dem Limit."""
    _rate_check(user_id)


def _rate_check(user_id: int) -> None:
    now = time.monotonic()
    q = _rate.setdefault(int(user_id), deque())
    while q and now - q[0] >= RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_MAX:
        raise PropagationRateLimited(max(1, math.ceil(RATE_WINDOW - (now - q[0]))))
    q.append(now)
    if len(_rate) > 10_000:  # Speicher begrenzen: leere Eintraege entfernen
        for uid in [u for u, d in _rate.items() if not d]:
            _rate.pop(uid, None)


# ---------------------------------------------------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class _Target:
    kind: str  # "authoritative" | "resolver"
    source: str
    ip: Optional[str] = None
    note: Optional[str] = None
    skip_reason: Optional[str] = None
    error_code: Optional[str] = None
    soa: Optional[DnsAnswer] = None
    rec: Optional[DnsAnswer] = None
    done: bool = False


@dataclass
class _Peer:
    name: str
    client: Any
    first: Optional[asyncio.Task] = None
    full_task: Optional[asyncio.Task] = None
    data: Optional[dict] = None
    full: Optional[dict] = None
    error: Optional[str] = None
    exc: Optional[PowerDNSAPIError] = None
    full_error: Optional[str] = None
    full_exc: Optional[PowerDNSAPIError] = None


@dataclass
class _Run:
    server_name: str
    zone_norm: str
    record_fqdn: Optional[str]
    rtype: Optional[str]
    content_flag: bool
    settings: PropagationSettings
    is_admin: bool
    writable: Optional[frozenset]
    loop: asyncio.AbstractEventLoop = None  # type: ignore[assignment]
    deadline: float = 0.0
    sem: asyncio.Semaphore = None  # type: ignore[assignment]
    targets: list = field(default_factory=list)
    ns_pending: dict = field(default_factory=dict)
    seen_ips: set = field(default_factory=set)
    truncated: bool = False

    def remaining(self, cap: float) -> float:
        return max(0.05, min(cap, self.deadline - self.loop.time()))

    def left(self) -> float:
        return max(0.0, self.deadline - self.loop.time())


async def _fetch_peer(client, zone_norm: str, record_fqdn: Optional[str], rtype: Optional[str],
                      content_flag: bool, timeout: float) -> dict:
    if content_flag:
        return await client.get_zone(zone_norm, timeout=timeout)
    if record_fqdn and rtype:
        return await client.get_zone_rrset(zone_norm, record_fqdn, rtype, timeout=timeout)
    return await client.get_zone_meta(zone_norm, timeout=timeout)


async def _query_target(run: _Run, tg: _Target, *, recursion: bool) -> None:
    async def one(qname: str, t: str) -> DnsAnswer:
        async with run.sem:
            return await query_dns(qname, t, tg.ip, run.remaining(DNS_QUERY_TIMEOUT), recursion=recursion,
                                   origin=run.zone_norm)

    jobs = [one(run.zone_norm, "SOA")]
    if run.record_fqdn and run.rtype:
        jobs.append(one(run.record_fqdn, run.rtype))
    res = await asyncio.gather(*jobs)
    tg.soa = res[0]
    tg.rec = res[1] if len(res) > 1 else None
    tg.done = True


def _add_ns_target(run: _Run, ns: str, ip: str, note: Optional[str]) -> Optional[_Target]:
    """Ziel registrieren (Dedupe nach IP, Obergrenze ``MAX_NS_TARGETS``); ``None`` = nicht abfragen."""
    if ip in run.seen_ips:
        return None
    if len(run.seen_ips) >= MAX_NS_TARGETS:
        run.truncated = True
        return None
    run.seen_ips.add(ip)
    tg = _Target(kind="authoritative", source=ns, ip=ip, note=note)
    tg.skip_reason = target_allowed(ip, run.settings.ipv6)
    run.targets.append(tg)
    return None if tg.skip_reason else tg


async def _resolve_and_query(run: _Run, ns: str) -> None:
    ips, err = await resolve_ns_addresses(ns, ipv6=run.settings.ipv6, timeout=run.remaining(NS_RESOLVE_TIMEOUT))
    run.ns_pending.pop(ns, None)
    if err:
        run.targets.append(_Target(kind="authoritative", source=ns, error_code=err, done=True))
        return
    todo = [tg for tg in (_add_ns_target(run, ns, ip, None) for ip in ips) if tg is not None]
    if todo:
        await asyncio.gather(*(_query_target(run, tg, recursion=False) for tg in todo))


def _peer_serials(data: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(data, dict) or data.get("serial") is None:
        return None, None
    raw = int(data.get("serial") or 0)
    return int(data.get("edited_serial") or raw), raw


async def _wait(tasks: Iterable[asyncio.Task], run: _Run) -> None:
    pending = [t for t in tasks if t is not None and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=run.left())


# Zonenarten, deren Inhalt per AXFR von einem Primary kommt (Katalog-Mitglieder: Consumer) [D9]
SECONDARY_KINDS = frozenset({"Slave", "Consumer"})


def is_secondary_kind(kind: Any) -> bool:
    return (kind or "") in SECONDARY_KINDS


def _peer_needs_full(peer: _Peer, expected: int, separate: bool, content_flag: bool) -> bool:
    if content_flag or not separate or peer.data is None:
        return False
    serial, _raw = _peer_serials(peer.data)
    return serial is not None and serial != expected and not is_secondary_kind(peer.data.get("kind"))


def _collect_first(p: _Peer) -> None:
    """Ergebnis der ersten Peer-Abfrage uebernehmen (nur einmal, nur wenn fertig)."""
    t = p.first
    if t is None or not t.done() or p.data is not None or p.error is not None:
        return
    if t.cancelled():
        p.error = "deadline"
        return
    exc = t.exception()
    if isinstance(exc, PowerDNSAPIError):
        p.error, p.exc = _classify_pdns_error(exc), exc
    elif exc is not None:
        logger.warning("Propagation: Peer %s lieferte einen unerwarteten Fehler: %s", p.name, type(exc).__name__)
        p.error = "api_error"
    else:
        res = t.result()
        p.data = res if isinstance(res, dict) else None
        if p.data is None:
            p.error = "api_error"


async def _run_check(run: _Run) -> PropagationResponse:
    loop = asyncio.get_running_loop()
    run.loop = loop
    run.deadline = loop.time() + TOTAL_TIMEOUT
    run.sem = asyncio.Semaphore(DNS_CONCURRENCY)
    t_start = time.monotonic()
    st = run.settings
    zone = run.zone_norm
    ext_auth = st.enabled and st.check_authoritative

    ref_client = pdns_manager.get_client(run.server_name)  # ValueError -> Router 404
    peers = [_Peer(n, c) for n, c in sorted(pdns_manager.get_all_clients().items()) if n != run.server_name]
    unloaded = {n: r for n, r in sorted((getattr(pdns_manager, "unloaded", {}) or {}).items())
                if n != run.server_name and n not in pdns_manager.clients}
    all_tasks: list[asyncio.Task] = []

    def spawn(coro) -> asyncio.Task:
        t = loop.create_task(coro)
        all_tasks.append(t)
        return t

    async def cancel_all() -> None:
        for t in all_tasks:
            if not t.done():
                t.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    # Phase A: Referenz, Peers und Resolver parallel
    ref_budget = REFERENCE_TIMEOUT
    if ext_auth:
        ref_budget = min(REFERENCE_TIMEOUT, max(0.05, run.left() - NS_MIN_BUDGET))
    ref_task = spawn(ref_client.get_zone(zone, timeout=run.remaining(ref_budget)))
    for p in peers:
        p.first = spawn(_fetch_peer(p.client, zone, run.record_fqdn, run.rtype, run.content_flag,
                                    run.remaining(PANEL_TIMEOUT)))
    resolver_targets: list[_Target] = []
    resolver_tasks: list[asyncio.Task] = []
    if st.enabled:
        for ip in st.resolvers:
            tg = _Target(kind="resolver", source=ip, ip=ip, skip_reason=target_allowed(ip, st.ipv6))
            resolver_targets.append(tg)
            if not tg.skip_reason:
                resolver_tasks.append(spawn(_query_target(run, tg, recursion=True)))

    try:
        ref_json = await asyncio.wait_for(ref_task, timeout=run.remaining(ref_budget))
    except asyncio.TimeoutError:
        await cancel_all()
        raise PropagationReferenceError("timeout", None) from None
    except PowerDNSAPIError as exc:
        await cancel_all()
        raise PropagationReferenceError(_classify_pdns_error(exc), exc) from None
    except BaseException:
        await cancel_all()
        raise
    if not isinstance(ref_json, dict):
        await cancel_all()
        raise PropagationReferenceError("api_error", None)

    ref = extract_reference(ref_json, zone)
    expected = ref["expected"]
    expected_vals: Optional[list[str]] = None
    comparable = True
    if run.record_fqdn and run.rtype:
        expected_vals, comparable = expected_record(ref_json, run.record_fqdn, run.rtype, zone)
    ref_idx = zone_content_index(ref_json, zone) if run.content_flag else None

    # Phase B: Nameserver – Glue sofort fragen, uebrige Namen parallel aufloesen [D14]
    ns_tasks: list[asyncio.Task] = []
    if ext_auth:
        for ns in ref["nameservers"]:
            glue = ref["glue"].get(ns)
            if glue:
                for ip in glue:
                    tg = _add_ns_target(run, ns, ip, "ns_from_glue")
                    if tg is not None:
                        ns_tasks.append(spawn(_query_target(run, tg, recursion=False)))
        for ns in ref["nameservers"]:
            if ns not in ref["glue"]:
                run.ns_pending[ns] = True
                ns_tasks.append(spawn(_resolve_and_query(run, ns)))

    # Phase C1: erste Antworten der Peers (DNS laeuft weiter im Hintergrund)
    await _wait([p.first for p in peers], run)
    for p in peers:
        _collect_first(p)

    # Getrennte Backends = mehrere schreibbare Primaries mit eigener DB; Secondaries (Slave/Consumer) zaehlen nicht,
    # auch wenn ihr Server schreibbar ist (ueblicher Aufbau Master auf ns1 + AXFR-Secondary auf ns2) [D9]
    writable = run.writable
    ref_writable = (writable is None or run.server_name in writable) and not is_secondary_kind(ref["kind"])
    writable_with_zone = (1 if ref_writable else 0) + sum(
        1 for p in peers
        if p.data is not None and (writable is None or p.name in writable) and not is_secondary_kind(p.data.get("kind"))
    )
    separate = writable_with_zone > 1

    # Phase C2: bei getrennten Backends fehlt fuer Peers mit abweichender Serial noch die volle Zone [D9]
    for p in peers:
        if _peer_needs_full(p, expected, separate, run.content_flag):
            p.full_task = spawn(p.client.get_zone(zone, timeout=run.remaining(PANEL_TIMEOUT)))

    open_tasks = [t for t in all_tasks if not t.done()]
    await _wait(open_tasks, run)
    timed_out = False
    for t in all_tasks:
        if not t.done():
            t.cancel()
            timed_out = True
    if timed_out:
        await asyncio.gather(*all_tasks, return_exceptions=True)

    for p in peers:
        _collect_first(p)
        if p.data is None and p.error is None:
            p.error = "deadline"
        t = p.full_task
        if t is None:
            continue
        if t.cancelled():
            p.full_error = "deadline"
            continue
        exc = t.exception()
        if isinstance(exc, PowerDNSAPIError):
            p.full_error, p.full_exc = _classify_pdns_error(exc), exc
        elif exc is not None:
            p.full_error = "api_error"
        else:
            res = t.result()
            p.full = res if isinstance(res, dict) else None
            if p.full is None:
                p.full_error = "api_error"

    # Serials aller Panel-Server mit der Zone (fuer DNS-Zeilen bei getrennten Backends); Secondaries nicht, sonst
    # liesse ein hinterherhinkender Secondary einen ebenso veralteten Nameserver als ``match`` erscheinen [D9]
    panel_serials = {expected, ref["serial_raw"]}
    for p in peers:
        if p.data is None or is_secondary_kind(p.data.get("kind")):
            continue
        s, raw = _peer_serials(p.data)
        if s is not None:
            panel_serials.update({s, raw})

    # ----------------------------------------------------------------------------------------------- Zeilen
    sources: list[PropagationSource] = []
    ref_notes: list[str] = []
    if (ref["kind"] in ("Master", "Producer") and ref["notified"] is not None and ref["notified"] < expected):
        ref_notes.append("notify_pending")
    if run.truncated:
        ref_notes.append("truncated_targets")
    sources.append(PropagationSource(
        source=run.server_name, kind="panel-api", is_reference=True, status="ok", serial=expected,
        serial_raw=ref["serial_raw"], notified_serial=ref["notified"], zone_kind=ref["kind"], match=True,
        serial_relation="equal",
        record_values=expected_vals if run.record_fqdn else None,
        record_match=True if run.record_fqdn else None,
        content_match=True if run.content_flag else None,
        content_diff_count=0 if run.content_flag else None,
        notes=ref_notes,
    ))

    panel_rows: list[PropagationSource] = []
    for p in peers:
        panel_rows.append(_peer_row(run, p, expected, expected_vals, ref_json, ref_idx, separate))
    for name, _reason in unloaded.items():
        panel_rows.append(PropagationSource(source=name, kind="panel-api", status="skipped",
                                            error_code="not_loaded", error=ERROR_TEXTS["not_loaded"]))
    sources.extend(sorted(panel_rows, key=lambda s: s.source))

    for ns in list(run.ns_pending):
        run.targets.append(_Target(kind="authoritative", source=ns, error_code="deadline", done=True))
    auth_rows = [_dns_row(run, tg, expected, expected_vals, comparable, separate, panel_serials)
                 for tg in run.targets]
    auth_rows.sort(key=lambda s: (s.source, s.target or ""))
    sources.extend(auth_rows)
    sources.extend(_dns_row(run, tg, expected, expected_vals, comparable, separate, panel_serials)
                   for tg in resolver_targets)
    if any(s.error_code == "deadline" for s in sources):
        timed_out = True

    ok = sum(1 for s in sources if s.status == "ok")
    mismatch = sum(1 for s in sources if s.status == "mismatch")
    failed = sum(1 for s in sources if s.status in ("error", "timeout"))
    skipped = sum(1 for s in sources if s.status == "skipped")
    summary = PropagationSummary(total=ok + mismatch + failed, ok=ok, mismatch=mismatch, failed=failed,
                                 skipped=skipped, in_sync=(mismatch == 0 and failed == 0))
    prom.record_propagation("in_sync" if summary.in_sync else ("out_of_sync" if mismatch else "failed"))

    duration_ms = int((time.monotonic() - t_start) * 1000)
    logger.info("Propagations-Check %s auf %s: %s/%s aktuell (%d ms)", zone, run.server_name, ok, summary.total,
                duration_ms)
    record_info = None
    if run.record_fqdn and run.rtype:
        record_info = PropagationRecordInfo(name=run.record_fqdn, type=run.rtype, expected_values=expected_vals or [],
                                            comparable=comparable)
    return PropagationResponse(
        zone=zone, server=run.server_name, checked_at=iso_utc(utcnow()), cached=False, duration_ms=duration_ms,
        timed_out=timed_out, expected_serial=expected, reference_serial_raw=ref["serial_raw"],
        separate_backends=separate, nameservers=ref["nameservers"], record=record_info,
        content_compared=bool(run.content_flag),
        external=PropagationExternal(enabled=st.enabled, authoritative=ext_auth,
                                     resolvers=list(st.resolvers) if st.enabled else [], ipv6=st.ipv6),
        comparable_types=list(COMPARABLE_TYPES), sources=sources, summary=summary,
    )


def _error_row(kind: str, source: str, code: str, **kw) -> PropagationSource:
    return PropagationSource(source=source, kind=kind, status=_ERROR_STATUS.get(code, "error"), error_code=code,
                             error=kw.pop("error", None) or ERROR_TEXTS.get(code, code), **kw)


def _peer_row(run: _Run, p: _Peer, expected: int, expected_vals: Optional[list[str]], ref_json: dict,
              ref_idx: Optional[dict], separate: bool) -> PropagationSource:
    if p.data is None:
        code = p.error or "api_error"
        return _error_row("panel-api", p.name, code, error=_error_text(code, is_admin=run.is_admin, exc=p.exc))
    z = p.data
    serial, raw = _peer_serials(z)
    kind = z.get("kind")
    notified = z.get("notified_serial")
    notified = int(notified) if isinstance(notified, (int, float)) else None
    row = PropagationSource(source=p.name, kind="panel-api", status="ok", serial=serial, serial_raw=raw,
                            notified_serial=notified, zone_kind=kind)
    if serial is None:
        row.status, row.error_code, row.error = "error", "api_error", ERROR_TEXTS["api_error"]
        return row
    match = serial == expected
    row.match = match
    row.serial_relation = serial_relation(serial, expected)
    if run.record_fqdn and run.rtype:
        row.record_values = _record_values(z, run.record_fqdn, run.rtype, run.zone_norm)
        row.record_match = row.record_values == (expected_vals or [])
    rec_ok = row.record_match in (True, None)
    if kind in ("Master", "Producer") and notified is not None and notified < serial:
        row.notes.append("notify_pending")

    if run.content_flag:
        peer_idx = zone_content_index(z, run.zone_norm)
        row.content_match, row.content_diff_count, row.content_diff_sample = compare_content(ref_idx or {}, peer_idx)
        row.status = "ok" if row.content_match and rec_ok else "mismatch"
        if not match and row.content_match:
            row.notes.append("content_same_serial_differs")
        if not match and separate and not is_secondary_kind(kind):
            row.notes.append("separate_backend")
        return row
    if match:
        row.status = "ok" if rec_ok else "mismatch"
        return row
    if is_secondary_kind(kind):
        # Secondary (Slave/Consumer): strenger Serial-Vergleich (NOTIFY/AXFR noch nicht angekommen)
        row.status = "mismatch"
        if row.serial_relation == "behind":
            row.notes.append("secondary_lagging")
        return row
    if not separate:
        row.status = "mismatch"
        return row
    # getrennte Backends: Inhalts-Fingerprint entscheidet, Serial nur Info [D9]
    row.notes.append("separate_backend")
    if p.full is None:
        code = p.full_error or "deadline"
        row.status = _ERROR_STATUS.get(code, "error") if code != "zone_missing" else "error"
        row.error_code = code
        row.error = _error_text(code, is_admin=run.is_admin, exc=p.full_exc)
        return row
    same = rrsets.zone_fingerprint(ref_json) == rrsets.zone_fingerprint(p.full)
    _m, row.content_diff_count, row.content_diff_sample = compare_content(
        zone_content_index(ref_json, run.zone_norm), zone_content_index(p.full, run.zone_norm))
    row.content_match = same
    row.status = "ok" if same and rec_ok else "mismatch"
    return row


def _dns_row(run: _Run, tg: _Target, expected: int, expected_vals: Optional[list[str]], comparable: bool,
             separate: bool, panel_serials: set) -> PropagationSource:
    label = KNOWN_RESOLVER_LABELS.get(tg.ip or "") if tg.kind == "resolver" else None
    notes = [tg.note] if tg.note else []
    if tg.skip_reason:
        return _error_row(tg.kind, tg.source, tg.skip_reason, target=tg.ip, label=label, notes=notes)
    if tg.error_code:
        return _error_row(tg.kind, tg.source, tg.error_code, target=tg.ip, label=label, notes=notes)
    if not tg.done or tg.soa is None:
        return _error_row(tg.kind, tg.source, "deadline", target=tg.ip, label=label, notes=notes)
    soa = tg.soa
    base = dict(target=tg.ip, label=label, notes=notes, rcode=soa.rcode, authoritative=soa.aa,
                latency_ms=soa.latency_ms, ttl=soa.ttl)
    if soa.error_code:
        return _error_row(tg.kind, tg.source, soa.error_code, **base)
    rcode_err = {"REFUSED": "refused", "SERVFAIL": "servfail", "NXDOMAIN": "nxdomain"}.get(soa.rcode or "")
    if rcode_err:
        return _error_row(tg.kind, tg.source, rcode_err, **base)
    if soa.rcode != "NOERROR" or soa.soa_serial is None:
        return _error_row(tg.kind, tg.source, "no_soa" if soa.rcode == "NOERROR" else "bad_response", **base)

    serial = soa.soa_serial
    match = serial == expected or (separate and serial in panel_serials)
    row = PropagationSource(source=tg.source, kind=tg.kind, status="ok", serial=serial, match=match,
                            serial_relation="equal" if match else serial_relation(serial, expected), **base)
    rec = tg.rec
    if run.record_fqdn and run.rtype:
        if rec is None or rec.error_code or rec.values is None:
            row.notes.append("record_query_failed")
        elif not comparable:
            row.record_values = rec.values
            row.notes.append("lua_not_comparable")
        else:
            row.record_values = rec.values
            row.record_match = rec.values == (expected_vals or [])
            if rec.ttl is not None:
                row.ttl = rec.ttl
    if not match and row.serial_relation == "behind":
        row.notes.append("resolver_cache" if tg.kind == "resolver" else "secondary_lagging")
    if tg.kind == "authoritative" and soa.aa is False:
        row.status, row.error_code, row.error = "error", "not_authoritative", ERROR_TEXTS["not_authoritative"]
        return row
    if separate and row.record_match is not None:
        row.status = "ok" if row.record_match else "mismatch"  # getrennte Backends: Record entscheidet [D9]
    else:
        row.status = "ok" if match and row.record_match in (True, None) else "mismatch"
    return row


async def check_zone(*, user_id: int, is_admin: bool, server_name: str, zone_norm: str,
                     record_fqdn: Optional[str], rtype: Optional[str], compare_content_flag: bool,
                     settings: PropagationSettings,
                     writable_servers: Optional[Collection[str]] = None, db=None) -> PropagationResponse:
    """Fuehrt einen Check aus (Cache, Rate-Limit, globale Parallelitaet; Ablauf siehe Modul-Docstring).

    ``writable_servers``: Namen der schreibbaren Panel-Server (``fanout.writable_server_names``); ``None`` = alle
    gelten als schreibbar. ``db``: Request-Session; wird vor dem Warten auf die Semaphore freigegeben (L6), der
    Check selbst braucht keine DB. Fehler: ``ValueError`` (Server unbekannt), ``PropagationRateLimited``,
    ``PropagationReferenceError``.
    """
    writable = frozenset(writable_servers) if writable_servers is not None else None
    key = (server_name, zone_norm, record_fqdn, rtype, bool(compare_content_flag), settings, bool(is_admin), writable)
    hit = _cache_get(key)
    if hit is not None:
        prom.record_propagation("cached")
        return hit.model_copy(update={"cached": True})
    _rate_check(user_id)
    async with dns_slot(db):
        hit = _cache_get(key)  # paralleler identischer Check ist gerade fertig geworden
        if hit is not None:
            prom.record_propagation("cached")
            return hit.model_copy(update={"cached": True})
        run = _Run(server_name=server_name, zone_norm=zone_norm, record_fqdn=record_fqdn, rtype=rtype,
                   content_flag=bool(compare_content_flag), settings=settings, is_admin=bool(is_admin),
                   writable=writable)
        result = await _run_check(run)
    _cache_put(key, result)
    return result
