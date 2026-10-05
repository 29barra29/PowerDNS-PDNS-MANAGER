"""LDAP/Active Directory: Suche per Dienstkonto, Bind als Benutzer, eindeutige ID, Gruppen (F10 5.5, Plan S15).

Ablauf von ``authenticate`` (blockierendes ldap3 laeuft in einem eigenen Thread-Pool):

1. Verbindung zum ersten erreichbaren Server der Liste (je Server genau ein Versuch, eigene Failover-Schleife statt
   ``ServerPool`` – der wartet nach einem erfolglosen Durchlauf 10 s), LDAPS oder StartTLS mit Zertifikatspruefung
   (``CERT_REQUIRED``, Hostname-Pruefung durch ldap3, optional eigene CA, SNI).
2. Bind des Dienstkontos (ohne ``bind_dn`` anonym) und Suche mit escaptem Benutzernamen (``{username}``);
   0 Treffer -> ``None``, mehr als 1 -> ``None`` + Warnung. Verweise (``searchResRef``) werden ignoriert.
3. Bind als gefundener Benutzer; leeres Passwort wird **nie** gesendet (unauthenticated bind = "Erfolg").
4. Eindeutige ID: objectGUID -> UUID-String, sonst UTF-8 klein (Decode-Fehler: Hex), leer -> DN (lang -> Hash).
5. Gruppen: ``memberof`` (Attribut), ``search`` (Gruppensuche mit ``{user_dn}``/``{username}``) oder ``none``.

Begrenzung [S15]: eigener ``ThreadPoolExecutor(max_workers=4)``; ein Slot (``asyncio.Semaphore(4)`` je Event-Loop)
wird erst freigegeben, wenn der Thread wirklich endet – auch nach einer Zeitueberschreitung. Warten mehr als
8 Anfragen auf einen Slot, wird die naechste sofort mit ``LdapUnavailable`` (503) abgewiesen statt zu warten.

Fehler: ``LdapUnavailable`` (Netz, Timeout, TLS, Warteschlange voll) bzw. ``LdapConfigError`` (Dienstkonto-Bind,
Suchbasis, ID-Attribut, Filter). Logs enthalten nur Klassennamen bzw. ``result["description"]`` – nie Passwoerter
oder Filterwerte.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import ssl
import threading
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from ldap3 import ANONYMOUS, AUTO_BIND_NONE, NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core import exceptions as lexc
from ldap3.utils.conv import escape_filter_chars

from app.services.sso_provisioning import ExternalProfile, group_matches
from app.services.sso_settings import LdapCfg, insecure_allowed, policy_from, unique_id_attr_is_safe

logger = logging.getLogger(__name__)

LDAP_MAX_THREADS = 4
LDAP_MAX_QUEUE = 8
MAX_GROUPS = 500
MAX_USERNAME = 256
_RESULT_OK = 0
_RESULT_SIZE_LIMIT = 4
_RESULT_NO_SUCH_OBJECT = 32

ConnFactory = Callable[..., Connection]


class LdapUnavailable(Exception):
    """Verzeichnis nicht erreichbar: Netzwerk, Timeout, TLS-Fehler, Warteschlange voll (Router: 503)."""


class LdapConfigError(Exception):
    """Fehlkonfiguration: Dienstkonto-Bind, Suchbasis, ID-Attribut, Filter (Router: 503 mit Admin-Hinweis)."""


@dataclass
class LdapIdentity:
    dn: str
    profile: ExternalProfile


# ---------------------------------------------------------------------------------------------
# Thread-Pool mit Warteschlangen-Grenze [S15]
# ---------------------------------------------------------------------------------------------
class _Gate:
    """Slots eines Event-Loops: Semaphore + Zahl der Wartenden."""

    def __init__(self) -> None:
        self.sem = asyncio.Semaphore(LDAP_MAX_THREADS)
        self.waiting = 0


_GATES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate]" = weakref.WeakKeyDictionary()
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=LDAP_MAX_THREADS, thread_name_prefix="ldap")
        return _executor


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = _Gate()
        _GATES[loop] = gate
    return gate


def pool_state() -> dict:
    """Momentaufnahme fuer Diagnose/Tests (nur aus dem Event-Loop aufrufen)."""
    try:
        gate = _gate()
    except RuntimeError:
        return {"running": 0, "waiting": 0}
    return {"running": LDAP_MAX_THREADS - gate.sem._value, "waiting": gate.waiting}  # noqa: SLF001


def shutdown_for_tests() -> None:
    """Executor und Slots verwerfen (Tests)."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None
    _GATES.clear()


async def run_limited(fn: Callable[..., Any], *args: Any, timeout: float) -> Any:
    """``fn(*args)`` im LDAP-Thread-Pool, Gesamtzeit ``timeout`` (inkl. Warten auf einen Slot).

    Volle Warteschlange -> sofort ``LdapUnavailable``; Zeitueberschreitung -> ``LdapUnavailable`` (der Thread laeuft
    zu Ende und gibt erst dann seinen Slot frei).
    """
    loop = asyncio.get_running_loop()
    gate = _gate()
    if gate.sem.locked() and gate.waiting >= LDAP_MAX_QUEUE:
        raise LdapUnavailable("Warteschlange voll")
    deadline = loop.time() + timeout
    gate.waiting += 1
    try:
        await asyncio.wait_for(gate.sem.acquire(), timeout=timeout)
    except asyncio.TimeoutError:
        raise LdapUnavailable("Zeitueberschreitung beim Warten auf einen freien LDAP-Slot") from None
    finally:
        gate.waiting -= 1
    try:
        fut = loop.run_in_executor(_get_executor(), fn, *args)
    except BaseException:
        gate.sem.release()
        raise

    def _done(f: asyncio.Future) -> None:
        gate.sem.release()
        if not f.cancelled():
            f.exception()   # als abgerufen markieren (abgebrochene Wartende)

    fut.add_done_callback(_done)
    remaining = max(0.05, deadline - loop.time())
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=remaining)
    except asyncio.TimeoutError:
        raise LdapUnavailable("Zeitueberschreitung") from None


# ---------------------------------------------------------------------------------------------
# Verbindungsaufbau
# ---------------------------------------------------------------------------------------------
def _host_of(url: str) -> Optional[str]:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _tls_for(url: str, cfg: LdapCfg) -> Optional[Tls]:
    """TLS-Einstellungen fuer einen Server: ``CERT_REQUIRED`` (ausser abgeschaltet), eigene CA, SNI = Host."""
    if cfg.security == "none":
        return None
    return Tls(
        validate=ssl.CERT_REQUIRED if cfg.tls_verify else ssl.CERT_NONE,
        ca_certs_data=cfg.ca_cert or None,
        sni=_host_of(url),
    )


def _server_for(url: str, cfg: LdapCfg) -> Server:
    return Server(url, get_info=NONE, connect_timeout=cfg.timeout, tls=_tls_for(url, cfg))


def _default_factory(server: Server, **kwargs: Any) -> Connection:
    return Connection(server, **kwargs)


def _conn_kwargs(cfg: LdapCfg, user: Optional[str], password: Optional[str]) -> dict:
    return {
        "user": user or None,
        "password": password or None,
        "authentication": SIMPLE if user else ANONYMOUS,
        "auto_bind": AUTO_BIND_NONE,
        "read_only": True,
        "receive_timeout": cfg.timeout,
        "raise_exceptions": False,
        "auto_referrals": False,
    }


def _check_transport_allowed(cfg: LdapCfg) -> None:
    if (cfg.security == "none" or not cfg.tls_verify) and not insecure_allowed():
        raise LdapConfigError("Unverschluesselte LDAP-Verbindung bzw. ohne Zertifikatspruefung erfordert "
                              "SSO_ALLOW_INSECURE=true")


def _is_tls_problem(exc: BaseException) -> bool:
    if isinstance(exc, (ssl.SSLError, lexc.LDAPStartTLSError, lexc.LDAPCertificateError)):
        return True
    text = str(exc).lower()
    return "ssl" in text or "certificate" in text or "tls" in text


_UNAVAILABLE = (
    lexc.LDAPSocketOpenError, lexc.LDAPSocketReceiveError, lexc.LDAPSocketSendError,
    lexc.LDAPSessionTerminatedByServerError, lexc.LDAPServerPoolExhaustedError, lexc.LDAPStartTLSError,
    lexc.LDAPCertificateError, lexc.LDAPCommunicationError, lexc.LDAPResponseTimeoutError,
    ssl.SSLError, socket.timeout, OSError,
)
_CONFIG = (lexc.LDAPInvalidFilterError, lexc.LDAPAttributeError, lexc.LDAPInvalidDnError,
           lexc.LDAPInvalidValueError, lexc.LDAPInvalidScopeError)


def _open(cfg: LdapCfg, url: str, user: Optional[str], password: Optional[str], factory: ConnFactory) -> Connection:
    """Verbindung oeffnen (+ StartTLS). Netz-/TLS-Fehler werfen die ldap3-Ausnahme weiter."""
    conn = factory(_server_for(url, cfg), **_conn_kwargs(cfg, user, password))
    try:
        conn.open()
        if cfg.security == "starttls":
            if not conn.start_tls():
                raise lexc.LDAPStartTLSError("StartTLS abgelehnt")
    except BaseException:
        _unbind(conn)
        raise
    return conn


def _unbind(conn: Optional[Connection]) -> None:
    if conn is None:
        return
    try:
        conn.unbind()
    except Exception:  # noqa: BLE001 - Aufraeumen darf nichts ueberdecken
        pass


def _connect_service(cfg: LdapCfg, factory: ConnFactory) -> tuple[Connection, str]:
    """Erster erreichbarer Server + Bind des Dienstkontos. Rueckgabe (Verbindung, URL)."""
    if not cfg.server_urls:
        raise LdapConfigError("Kein LDAP-Server konfiguriert")
    last: Optional[BaseException] = None
    for url in cfg.server_urls:
        try:
            conn = _open(cfg, url, cfg.bind_dn or None, cfg.bind_password or None, factory)
        except _UNAVAILABLE as exc:
            logger.warning("LDAP-Server %s nicht erreichbar (%s)", url, type(exc).__name__)
            last = exc
            continue
        if not conn.bind():
            desc = str((conn.result or {}).get("description") or "unbekannt")
            _unbind(conn)
            raise LdapConfigError(f"Dienstkonto-Bind: {desc}")
        return conn, url
    raise LdapUnavailable(type(last).__name__ if last else "kein Server erreichbar")


def _entries(conn: Connection) -> list[dict]:
    return [r for r in (conn.response or []) if r.get("type") == "searchResEntry"]


def _check_search_result(conn: Connection, what: str) -> None:
    code = (conn.result or {}).get("result", _RESULT_OK)
    if code in (_RESULT_OK, _RESULT_SIZE_LIMIT) or code is None:
        return
    desc = str((conn.result or {}).get("description") or code)
    if code == _RESULT_NO_SUCH_OBJECT:
        raise LdapConfigError(f"{what}: Suchbasis nicht gefunden")
    raise LdapConfigError(f"{what}: {desc}")


def _decode(raw: Any) -> Optional[str]:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    if raw is None:
        return None
    return str(raw)


def _first(raw_attrs: dict, attr: str) -> Optional[bytes]:
    if not attr:
        return None
    for key, vals in raw_attrs.items():
        if key.lower() == attr.lower():
            if isinstance(vals, (list, tuple)) and vals:
                v = vals[0]
                return v if isinstance(v, bytes) else str(v).encode("utf-8")
            return None
    return None


def _all(raw_attrs: dict, attr: str) -> list[bytes]:
    for key, vals in raw_attrs.items():
        if key.lower() == attr.lower() and isinstance(vals, (list, tuple)):
            return [v if isinstance(v, bytes) else str(v).encode("utf-8") for v in vals]
    return []


def _unique_id(raw_attrs: dict, dn: str, cfg: LdapCfg) -> str:
    """Eindeutige ID (F10 5.5 Schritt 5): objectGUID als UUID, sonst UTF-8 klein, leer -> DN."""
    attr = (cfg.unique_id_attr or "").strip()
    if not attr:
        low = dn.lower()
        return low if len(low) <= 255 else "dn-sha256:" + hashlib.sha256(low.encode("utf-8")).hexdigest()
    raw = _first(raw_attrs, attr)
    if raw is None or raw == b"":
        raise LdapConfigError(f"Attribut {attr} fehlt beim Benutzer")
    if attr.lower() == "objectguid":
        if len(raw) == 16:
            return str(uuid.UUID(bytes_le=raw))
        try:  # manche Proxys liefern die GUID als Text
            return str(uuid.UUID(raw.decode("ascii").strip("{}")))
        except (ValueError, UnicodeDecodeError):
            raise LdapConfigError(f"Attribut {attr} hat kein GUID-Format") from None
    try:
        val = raw.decode("utf-8").strip().lower()
    except UnicodeDecodeError:
        val = raw.hex()
    if not val:
        raise LdapConfigError(f"Attribut {attr} fehlt beim Benutzer")
    return val[:255] if len(val) <= 255 else "sha256:" + hashlib.sha256(val.encode("utf-8")).hexdigest()


def _search_user(conn: Connection, cfg: LdapCfg, username: str) -> tuple[list[dict], int]:
    flt = cfg.user_filter.replace("{username}", escape_filter_chars(username))
    attrs = [a for a in (cfg.username_attr, cfg.email_attr, cfg.name_attr, cfg.unique_id_attr) if a]
    if cfg.group_mode == "memberof":
        attrs.append("memberOf")
    conn.search(cfg.user_base_dn, flt, SUBTREE, attributes=list(dict.fromkeys(attrs)), size_limit=2)
    _check_search_result(conn, "Benutzersuche")
    entries = _entries(conn)
    return entries, len(entries)


def _groups(conn: Connection, cfg: LdapCfg, entry: dict, username: str) -> Optional[list[str]]:
    """Gruppen-DNs des Benutzers (``None`` = Gruppenmodus ``none``)."""
    if cfg.group_mode == "none":
        return None
    if cfg.group_mode == "memberof":
        return [g for g in (_decode(v) for v in _all(entry.get("raw_attributes") or {}, "memberOf")) if g][:MAX_GROUPS]
    dn = entry.get("dn") or ""
    flt = (cfg.group_filter.replace("{user_dn}", escape_filter_chars(dn))
           .replace("{username}", escape_filter_chars(username)))
    conn.search(cfg.group_base_dn, flt, SUBTREE, attributes=["cn"], size_limit=MAX_GROUPS)
    _check_search_result(conn, "Gruppensuche")
    if (conn.result or {}).get("result") == _RESULT_SIZE_LIMIT:
        logger.warning("LDAP-Gruppensuche: mehr als %d Gruppen – Liste gekuerzt", MAX_GROUPS)
    return [str(e.get("dn")) for e in _entries(conn) if e.get("dn")][:MAX_GROUPS]


def _profile(cfg: LdapCfg, entry: dict, username: str, groups: Optional[list[str]]) -> ExternalProfile:
    raw_attrs = entry.get("raw_attributes") or {}
    dn = str(entry.get("dn") or "")
    hint = _decode(_first(raw_attrs, cfg.username_attr)) or username
    email = _decode(_first(raw_attrs, cfg.email_attr)) if cfg.email_attr else None
    name = _decode(_first(raw_attrs, cfg.name_attr)) if cfg.name_attr else None
    return ExternalProfile(
        source="ldap", issuer="ldap", subject=_unique_id(raw_attrs, dn, cfg),
        username_hint=(hint or username)[:255],
        email=(email.strip()[:255] or None) if email else None,
        email_verified=None,
        display_name=(name.strip()[:255] or None) if name else None,
        groups=groups, locale=None, dn=dn,
    )


def _user_bind(cfg: LdapCfg, url: str, dn: str, password: str, factory: ConnFactory) -> bool:
    conn: Optional[Connection] = None
    try:
        conn = _open(cfg, url, dn, password, factory)
        return bool(conn.bind())   # invalidCredentials, AD "data 533" (deaktiviert) usw. -> False
    finally:
        _unbind(conn)


def _map_exception(exc: BaseException) -> Exception:
    if isinstance(exc, (LdapUnavailable, LdapConfigError)):
        return exc
    if isinstance(exc, _CONFIG):
        return LdapConfigError(type(exc).__name__)
    return LdapUnavailable(type(exc).__name__)


def _authenticate_sync(cfg: LdapCfg, username: str, password: str, factory: Optional[ConnFactory]) -> Optional[LdapIdentity]:
    factory = factory or _default_factory
    service: Optional[Connection] = None
    try:
        _check_transport_allowed(cfg)
        service, url = _connect_service(cfg, factory)
        entries, count = _search_user(service, cfg, username)
        if count == 0:
            return None
        if count > 1:
            logger.warning("LDAP: Filter liefert mehrere Treffer fuer eine Anmeldung – Anmeldung abgelehnt")
            return None
        entry = entries[0]
        dn = str(entry.get("dn") or "")
        if not dn:
            return None
        if not _user_bind(cfg, url, dn, password, factory):
            return None
        groups = _groups(service, cfg, entry, username)
        return LdapIdentity(dn=dn, profile=_profile(cfg, entry, username, groups))
    except (LdapUnavailable, LdapConfigError):
        raise
    except lexc.LDAPException as exc:
        raise _map_exception(exc) from None
    except (ssl.SSLError, OSError) as exc:
        raise LdapUnavailable(type(exc).__name__) from None
    finally:
        _unbind(service)


def _overall_timeout(cfg: LdapCfg) -> float:
    return float(cfg.timeout) * 4 + 2


async def authenticate(cfg: LdapCfg, username: str, password: str, *,
                       _conn_factory: Optional[ConnFactory] = None) -> Optional[LdapIdentity]:
    """Anmeldung pruefen. ``None`` = Benutzer unbekannt/mehrdeutig oder Passwort falsch."""
    username = (username or "").strip()
    if not username or len(username) > MAX_USERNAME or not password:   # leeres Passwort NIE an den Server
        return None
    return await run_limited(_authenticate_sync, cfg, username, password, _conn_factory,
                             timeout=_overall_timeout(cfg))


# ---------------------------------------------------------------------------------------------
# Test der Konfiguration (POST /settings/sso/test)
# ---------------------------------------------------------------------------------------------
def _fail(error: str, details: dict, warnings: list[str]) -> dict:
    return {"success": False, "message": None, "error": error, "warnings": warnings, "details": details}


def _test_sync(cfg: LdapCfg, test_username: Optional[str], test_password: Optional[str],
               factory: Optional[ConnFactory]) -> dict:
    factory = factory or _default_factory
    warnings: list[str] = []
    details: dict[str, Any] = {"server": None, "security": cfg.security, "service_bind": None, "user": None}
    if cfg.security == "none" or not cfg.tls_verify:
        if not insecure_allowed():
            return _fail("Unverschlüsselte LDAP-Verbindungen bzw. das Abschalten der Zertifikatsprüfung erfordern "
                         "SSO_ALLOW_INSECURE=true", details, warnings)
        warnings.append("Unverschlüsselte Verbindung bzw. ohne Zertifikatsprüfung – nur für Tests geeignet")
    if not unique_id_attr_is_safe(cfg.unique_id_attr):
        warnings.append(f"Das ID-Attribut {cfg.unique_id_attr or 'DN'} ist änderbar – empfohlen: objectGUID, "
                        f"entryUUID, nsUniqueId oder ipaUniqueID")
    if not cfg.server_urls:
        return _fail("Kein LDAP-Server konfiguriert", details, warnings)
    if not cfg.user_base_dn and test_username:
        return _fail("Keine Suchbasis für Benutzer konfiguriert", details, warnings)

    service: Optional[Connection] = None
    url_used: Optional[str] = None
    last_url, last_exc = cfg.server_urls[0], None
    try:
        for url in cfg.server_urls:
            try:
                service = _open(cfg, url, cfg.bind_dn or None, cfg.bind_password or None, factory)
                url_used = url
                break
            except _UNAVAILABLE + (lexc.LDAPException,) as exc:
                last_url, last_exc = url, exc
                if len(cfg.server_urls) > 1:
                    warnings.append(f"Server {url} nicht erreichbar ({type(exc).__name__})")
        if service is None:
            reason = f"{type(last_exc).__name__}: {str(last_exc)[:160]}" if last_exc else "unbekannt"
            if last_exc is not None and _is_tls_problem(last_exc):
                return _fail(f"TLS-Fehler: {reason}", details, warnings)
            return _fail(f"Verbindung zu {last_url} fehlgeschlagen: {reason}", details, warnings)
        details["server"] = url_used
        if not service.bind():
            desc = str((service.result or {}).get("description") or "unbekannt")
            return _fail(f"Bind des Dienstkontos fehlgeschlagen ({desc})", details, warnings)
        details["service_bind"] = "ok" if cfg.bind_dn else "anonymous"
        username = (test_username or "").strip()
        if not username:
            return {"success": True, "message": "Verbindung und Bind des Dienstkontos erfolgreich", "error": None,
                    "warnings": warnings, "details": details}
        entries, count = _search_user(service, cfg, username)
        if count == 0:
            return _fail("Benutzer nicht gefunden", details, warnings)
        if count > 1:
            return _fail("Mehrere Benutzer passen auf den Filter – bitte Filter präzisieren", details, warnings)
        entry = entries[0]
        dn = str(entry.get("dn") or "")
        try:
            groups = _groups(service, cfg, entry, username)
            profile = _profile(cfg, entry, username, groups)
        except LdapConfigError as exc:
            return _fail(str(exc), details, warnings)
        policy = policy_from(cfg, "ldap")
        all_groups = list(groups or [])
        user: dict[str, Any] = {
            "dn": dn, "username": profile.username_hint, "email": profile.email,
            "display_name": profile.display_name, "unique_id": profile.subject,
            "groups": all_groups[:25], "groups_total": len(all_groups),
            "is_allowed": (not policy.allowed_groups) or bool(
                group_matches(policy.allowed_groups, groups, source="ldap")),
            "is_admin": bool(group_matches(policy.admin_groups, groups, source="ldap")),
            "password_checked": False, "password_ok": None,
        }
        details["user"] = user
        if groups is None and (policy.allowed_groups or policy.role_mode != "off"):
            warnings.append("Gruppenmodus „keine Gruppen“ – erlaubte Gruppen und Rollen-Zuordnung greifen nicht")
        if test_password:
            ok = _user_bind(cfg, url_used, dn, test_password, factory)
            user["password_checked"] = True
            user["password_ok"] = ok
            if not ok:
                return _fail("Passwort des Testbenutzers falsch", details, warnings)
        return {"success": True, "message": "Benutzer gefunden", "error": None, "warnings": warnings,
                "details": details}
    except LdapConfigError as exc:
        return _fail(str(exc), details, warnings)
    except _CONFIG as exc:
        return _fail(f"Konfigurationsfehler (Filter/Attribute/DN): {type(exc).__name__}", details, warnings)
    except _UNAVAILABLE + (lexc.LDAPException,) as exc:
        reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        if _is_tls_problem(exc):
            return _fail(f"TLS-Fehler: {reason}", details, warnings)
        return _fail(f"Verbindung zu {url_used or last_url} fehlgeschlagen: {reason}", details, warnings)
    finally:
        _unbind(service)


async def test_connection(cfg: LdapCfg, test_username: Optional[str] = None, test_password: Optional[str] = None,
                          *, _conn_factory: Optional[ConnFactory] = None) -> dict:
    """Schrittweiser Verbindungstest mit eigenen Fehlertexten (F10 3.3.3); Testpasswort wird nie geloggt."""
    try:
        return await run_limited(_test_sync, cfg, test_username, test_password, _conn_factory,
                                 timeout=_overall_timeout(cfg) + 2)
    except LdapUnavailable as exc:
        return {"success": False, "message": None, "error": f"LDAP nicht erreichbar: {exc}", "warnings": [],
                "details": {"server": None, "security": cfg.security, "service_bind": None, "user": None}}
