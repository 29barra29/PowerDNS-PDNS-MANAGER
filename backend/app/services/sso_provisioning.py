"""Externe Konten (OIDC/LDAP): Freigabe, JIT-Anlage, Verknuepfung, Rollen aus Gruppen (F10 5.6).

Grundsaetze (Bauplan WS-F10-SVC, Kritik S1/S5):

- Konten werden **nur** ueber die externe Identitaet gefunden bzw. verknuepft:
  OIDC ``(iss, sub)``, LDAP ``("ldap", <eindeutige ID>)`` – nie ueber Benutzername oder E-Mail.
- Gruppenvergleich: LDAP-Gruppen ausschliesslich als **vollstaendige DN** (normalisierte RDN-Folge:
  Attributnamen klein, Werte entschluesselt, getrimmt, Leerzeichenfolgen zusammengefasst, klein).
  ``CN=pdns-admins,OU=Other,DC=x`` trifft ``CN=pdns-admins,OU=Groups,DC=x`` nicht. OIDC-Gruppen sind
  normalisierte Strings (klein, getrimmt, fuehrendes ``/`` von Keycloak-Pfaden entfernt).
- JIT ist nur erlaubt, wenn erlaubte Gruppen bzw. (OIDC) E-Mail-Domains gesetzt sind oder der Admin
  ``jit_allow_any_account`` ausdruecklich bestaetigt hat (``ProvisioningPolicy.jit_permitted``). Steht in
  der Datenbank ein JIT ohne diese Bedingung (z. B. von Hand gesetzt), wird kein Konto angelegt.
- Fehlende Gruppeninformation aendert nie die Rolle (fail-safe), verweigert aber die Anmeldung, wenn eine
  Gruppen-Freigabe gesetzt ist (fail-closed).
- Rollen aus Gruppen stufen den letzten aktiven Admin nie herab (Audit ``USER_ROLE_SYNC`` mit
  ``skipped: last_admin``).

Die Schutzregeln fuer Admin-Konten liegen in ``services/user_guard.py`` (WS-F10-APP-BE hat sie dorthin verschoben):
``count_active_admins`` (letzter aktiver Admin beim Rollenabgleich), ``count_active_local_admins`` und
``assert_keeps_local_admin`` (Notfallzugang). Die Namen bleiben hier als Aliase erhalten.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password
from app.models.models import User, WebAuthnCredential
from app.services import user_guard
from app.services.audit import write_audit
from app.services.user_guard import LAST_LOCAL_ADMIN_DETAIL, assert_keeps_local_admin, count_active_local_admins  # noqa: F401

logger = logging.getLogger(__name__)

SOURCES = ("oidc", "ldap")
ROLE_MODES = ("off", "promote", "sync")
ROLE_ADMIN = "admin"
ROLE_USER = "user"
LDAP_ISSUER = "ldap"

# Obergrenzen gegen ueberlange IdP-Antworten
MAX_GROUPS = 500
MAX_FIELD_LEN = 255
USERNAME_MAX = 64
_USERNAME_RETRIES = 3

PROVISIONING_ERROR_CODES = ("not_allowed", "no_account", "account_disabled", "link_conflict")


# ---------------------------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------------------------
@dataclass
class ExternalProfile:
    """Vom Anbieter gelieferte, bereits gepruefte Identitaet."""

    source: str                  # "oidc" | "ldap"
    issuer: str                  # OIDC iss bzw. "ldap"
    subject: str                 # OIDC sub bzw. LDAP-ID (objectGUID/entryUUID/... oder DN)
    username_hint: str
    email: Optional[str]
    email_verified: Optional[bool]
    display_name: Optional[str]
    groups: Optional[list[str]]  # None = Quelle liefert keine Gruppen (Claim fehlt / group_mode none)
    locale: Optional[str] = None
    dn: Optional[str] = None


@dataclass(frozen=True)
class ProvisioningPolicy:
    """Regeln einer Quelle (aus ``sso_settings.policy_from``)."""

    source: str
    jit_enabled: bool
    allowed_groups: tuple[str, ...] = ()
    admin_groups: tuple[str, ...] = ()
    role_mode: str = "off"
    allowed_email_domains: tuple[str, ...] = ()   # nur OIDC; greift nur bei email_verified=True
    jit_allow_any_account: bool = False

    @property
    def restricted(self) -> bool:
        """Ist die Anmeldung auf Gruppen bzw. E-Mail-Domains beschraenkt?"""
        return bool(self.allowed_groups) or bool(self.allowed_email_domains)

    @property
    def jit_permitted(self) -> bool:
        """JIT nur mit Beschraenkung oder ausdruecklicher Freigabe fuer alle Konten [S5]."""
        return bool(self.jit_enabled) and (self.restricted or bool(self.jit_allow_any_account))


class ProvisioningError(Exception):
    """Abgelehnte Anmeldung/Verknuepfung. ``code``: not_allowed | no_account | account_disabled | link_conflict."""

    def __init__(self, code: str, user_id: Optional[int] = None):
        super().__init__(code)
        self.code = code
        self.user_id = user_id


@dataclass
class ProvisionResult:
    user: User
    created: bool
    role_change: Optional[tuple[str, str]]
    profile_updated: list[str] = field(default_factory=list)
    issuer: Optional[str] = None
    source: Optional[str] = None

    @property
    def audit_extra(self) -> dict:
        """Zusatzfelder fuer das LOGIN-Audit (``complete_login(audit_extra=...)``)."""
        extra: dict = {"jit": bool(self.created)}
        if self.source == "oidc" and self.issuer:
            extra["issuer"] = self.issuer
        if self.profile_updated:
            extra["profile_updated"] = list(self.profile_updated)
        if self.role_change:
            extra["role_changed"] = {"from": self.role_change[0], "to": self.role_change[1]}
        return extra


# ---------------------------------------------------------------------------------------------
# Gruppen
# ---------------------------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")
_HEX = "0123456789abcdefABCDEF"


def normalize_group(group: object) -> str:
    """OIDC-Gruppe: getrimmt, klein, fuehrendes ``/`` (Keycloak-Pfad) entfernt."""
    s = str(group or "").strip().lower()
    return s[1:] if s.startswith("/") else s


def _unescape_dn_value(value: str) -> str:
    """RFC 4514: ``\\,`` und ``\\2C`` -> ``,``; Hex-Folgen werden als UTF-8 dekodiert."""
    if value.startswith("#"):
        return value.lower()  # BER-kodierter Wert: nur Gross/klein vereinheitlichen
    out = bytearray()
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in _HEX and i + 2 < len(value) and value[i + 2] in _HEX:
                out.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            out.extend(nxt.encode("utf-8"))
            i += 2
            continue
        out.extend(c.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def dn_key(dn: object) -> Optional[tuple[tuple[tuple[str, str], ...], ...]]:
    """Normalisierte RDN-Folge einer DN oder ``None`` (keine gueltige DN).

    Je RDN: sortierte (Attribut klein, Wert entschluesselt/getrimmt/Leerzeichen zusammengefasst/klein).
    Mehrwertige RDNs (``+``) sind reihenfolgeunabhaengig. Leerzeichen um ``,``/``=`` sind egal.
    """
    from ldap3.core.exceptions import LDAPInvalidDnError
    from ldap3.utils.dn import parse_dn

    if not isinstance(dn, str):
        return None
    raw = dn.strip()
    if not raw or len(raw) > 2048:
        return None
    try:
        parts = parse_dn(raw, escape=False, strip=True)
    except (LDAPInvalidDnError, ValueError, TypeError, AttributeError):
        return None
    rdns: list[tuple[tuple[str, str], ...]] = []
    current: list[tuple[str, str]] = []
    for attr, value, sep in parts:
        if not attr or not isinstance(value, str) or not value:
            return None
        val = _WS_RE.sub(" ", _unescape_dn_value(value)).strip().lower()
        if not val:
            return None
        current.append((attr.strip().lower(), val))
        if sep != "+":
            rdns.append(tuple(sorted(current)))
            current = []
    if current:
        rdns.append(tuple(sorted(current)))
    return tuple(rdns) if rdns else None


def is_valid_dn(dn: object) -> bool:
    return dn_key(dn) is not None


def group_matches(configured: Iterable[str], user_groups: Optional[Iterable[str]], *, source: str) -> list[str]:
    """Konfigurierte Eintraege, die eine Gruppe des Nutzers treffen (Originalschreibweise, Reihenfolge der Konfiguration).

    ``source == "ldap"``: Vergleich nur ueber vollstaendige DN (``dn_key``); Eintraege ohne gueltige DN treffen
    nie (beim Speichern bereits abgewiesen) [S1]. Sonst (OIDC): ``normalize_group``-Stringvergleich.
    """
    conf = [c for c in (configured or ()) if isinstance(c, str) and c.strip()]
    if not conf or not user_groups:
        return []
    groups = [g for g in user_groups if isinstance(g, str)]
    if source == "ldap":
        have = {k for k in (dn_key(g) for g in groups) if k is not None}
        return [c for c in conf if (k := dn_key(c)) is not None and k in have]
    have_s = {s for s in (normalize_group(g) for g in groups) if s}
    return [c for c in conf if normalize_group(c) and normalize_group(c) in have_s]


def email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return dom or None


def matched_email_domain(policy: ProvisioningPolicy, profile: ExternalProfile) -> Optional[str]:
    """Erlaubte E-Mail-Domain des Profils – nur bei ausdruecklich bestaetigter E-Mail (``email_verified is True``)."""
    if not policy.allowed_email_domains or profile.email_verified is not True:
        return None
    dom = email_domain(profile.email)
    if dom and dom in {d.lower() for d in policy.allowed_email_domains}:
        return dom
    return None


def check_allowed(policy: ProvisioningPolicy, profile: ExternalProfile) -> None:
    """Freigabe pruefen: ohne Beschraenkung ok, sonst Gruppen-Treffer **oder** erlaubte (bestaetigte) E-Mail-Domain."""
    if not policy.restricted:
        return
    if policy.allowed_groups:
        if profile.groups is None:
            logger.warning("SSO (%s): Gruppen fehlen (Claim/Gruppenmodus pruefen)", policy.source)
        elif group_matches(policy.allowed_groups, profile.groups, source=policy.source):
            return
    if matched_email_domain(policy, profile):
        return
    raise ProvisioningError("not_allowed")


def desired_role(policy: ProvisioningPolicy, profile: ExternalProfile, current: str) -> Optional[str]:
    """Zielrolle aus Gruppen oder ``None`` (nicht aendern). Ohne Gruppeninformation nie raten."""
    if policy.role_mode not in ("promote", "sync"):
        return None
    if profile.groups is None:
        return None
    if group_matches(policy.admin_groups, profile.groups, source=policy.source):
        return ROLE_ADMIN
    if policy.role_mode == "promote":
        return None
    return ROLE_USER


# ---------------------------------------------------------------------------------------------
# Benutzernamen
# ---------------------------------------------------------------------------------------------
_USERNAME_BAD_RE = re.compile(r"[^A-Za-z0-9._-]")
_DASHES_RE = re.compile(r"-{2,}")


def sanitize_username(raw: Optional[str], seed: str) -> str:
    """Panel-Benutzername aus dem Anbieter-Hinweis: Teil vor ``@``, ASCII, ``[A-Za-z0-9._-]``, max. 64.

    Kuerzer als 3 Zeichen -> ``user-<8 hex>`` aus ``seed`` (stabil je Identitaet).
    """
    s = str(raw or "").strip()
    if "@" in s:
        s = s.split("@", 1)[0]
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = _USERNAME_BAD_RE.sub("-", s)
    s = _DASHES_RE.sub("-", s)
    s = s.strip(".-_")[:USERNAME_MAX].strip(".-_")
    if len(s) < 3:
        return "user-" + hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest()[:8]
    return s


async def _username_taken(db: AsyncSession, candidate: str) -> bool:
    # DB-Kollation ist case-insensitiv (MariaDB) -> "Admin"/"admin" kollidieren wie beim Login.
    return (await db.execute(select(User.id).where(User.username == candidate))).first() is not None


async def unique_username(db: AsyncSession, base: str) -> str:
    """``base``, sonst ``base-2`` … ``base-99``, sonst ``base-<6 hex>``."""
    base = (base or "").strip() or "user"
    if not await _username_taken(db, base[:USERNAME_MAX]):
        return base[:USERNAME_MAX]
    for n in range(2, 100):
        cand = f"{base[:60]}-{n}"
        if not await _username_taken(db, cand):
            return cand
    while True:
        cand = f"{base[:56]}-{secrets.token_hex(3)}"
        if not await _username_taken(db, cand):
            return cand


# ---------------------------------------------------------------------------------------------
# DB-Helfer
# ---------------------------------------------------------------------------------------------
async def find_external_user(db: AsyncSession, source: str, issuer: str, subject: str) -> Optional[User]:
    """Konto zur externen Identitaet (exakter, case-sensitiver Vergleich ueber utf8mb4_bin)."""
    if not source or not issuer or not subject:
        return None
    result = await db.execute(
        select(User).where(
            User.auth_source == source,
            User.external_issuer == issuer,
            User.external_id == subject,
        )
    )
    return result.scalars().first()


async def _email_taken(db: AsyncSession, email: str, *, exclude_user_id: Optional[int] = None) -> bool:
    q = select(User.id).where(User.email == email)
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    return (await db.execute(q)).first() is not None


# Alias (Welle 1): aktive Admins aller Quellen = ``user_guard.count_active_admins`` (F3)
_count_active_admins = user_guard.count_active_admins


# ---------------------------------------------------------------------------------------------
# Rollen und Profil
# ---------------------------------------------------------------------------------------------
async def apply_role(
    db: AsyncSession,
    user: User,
    new_role: Optional[str],
    *,
    source: str,
    matched: Sequence[str] = (),
) -> Optional[tuple[str, str]]:
    """Rolle setzen (Audit ``USER_ROLE_SYNC``). Der letzte aktive Admin wird nie herabgestuft.

    Rueckgabe ``(alt, neu)`` bei einer Aenderung, sonst ``None``.
    """
    old = user.role or ROLE_USER
    if not new_role or new_role == old:
        return None
    if old == ROLE_ADMIN and new_role != ROLE_ADMIN and user.is_active:
        if await user_guard.count_active_admins(db, exclude_user_id=user.id) < 1:
            logger.warning("SSO-Rollenabgleich: letzter aktiver Admin %s wird nicht herabgestuft", user.username)
            await write_audit(
                db, "USER_ROLE_SYNC", "user", user.username, user_id=user.id,
                details={"target_user_id": user.id, "from": old, "to": new_role, "source": source,
                         "skipped": "last_admin"},
            )
            return None
    user.role = new_role
    await db.flush()
    await write_audit(
        db, "USER_ROLE_SYNC", "user", user.username, user_id=user.id,
        details={"target_user_id": user.id, "from": old, "to": new_role, "source": source,
                 "groups": list(matched)[:10]},
    )
    return (old, new_role)


async def sync_profile(db: AsyncSession, user: User, profile: ExternalProfile) -> list[str]:
    """E-Mail (nur bestaetigt bzw. ohne gegenteilige Aussage, und frei) und Anzeigename uebernehmen."""
    changed: list[str] = []
    email = (profile.email or "").strip()[:MAX_FIELD_LEN]
    if email and profile.email_verified is not False and email != (user.email or ""):
        if await _email_taken(db, email, exclude_user_id=user.id):
            logger.info("SSO: E-Mail-Adresse fuer %s nicht uebernommen (bereits vergeben)", user.username)
        else:
            user.email = email
            changed.append("email")
    name = (profile.display_name or "").strip()[:MAX_FIELD_LEN]
    if name and name != (user.display_name or ""):
        user.display_name = name
        changed.append("display_name")
    if changed:
        await db.flush()
    return changed


def _admin_matches(policy: ProvisioningPolicy, profile: ExternalProfile) -> list[str]:
    return group_matches(policy.admin_groups, profile.groups, source=policy.source) if profile.groups else []


# ---------------------------------------------------------------------------------------------
# Anmeldung (JIT) und Verknuepfung
# ---------------------------------------------------------------------------------------------
async def _create_external_user(db: AsyncSession, profile: ExternalProfile, policy: ProvisioningPolicy) -> Optional[User]:
    """Legt das JIT-Konto an. ``None``, wenn parallel bereits ein Konto zur Identitaet entstanden ist."""
    role = desired_role(policy, profile, ROLE_USER) or ROLE_USER
    email = (profile.email or "").strip()[:MAX_FIELD_LEN] or None
    if email and (profile.email_verified is False or await _email_taken(db, email)):
        email = None
    # Offene Aenderungen des Aufrufers vor dem SAVEPOINT flushen (begin_nested wuerde sonst selbst flushen
    # und deren Fehler als Kollision dieses Inserts deuten).
    await db.flush()
    last_exc: Optional[IntegrityError] = None
    for _ in range(_USERNAME_RETRIES):
        username = await unique_username(db, sanitize_username(profile.username_hint, profile.subject))
        user = User(
            username=username,
            email=email,
            display_name=(profile.display_name or "").strip()[:MAX_FIELD_LEN] or username,
            hashed_password=hash_password(secrets.token_urlsafe(48)),
            role=role,
            is_active=True,
            auth_source=profile.source,
            external_issuer=profile.issuer,
            external_id=profile.subject,
            preferred_language=profile.locale,
        )
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError as exc:
            last_exc = exc
            if await find_external_user(db, profile.source, profile.issuer, profile.subject) is not None:
                return None  # paralleler Erstlogin: vorhandenes Konto verwenden
            if email and await _email_taken(db, email):
                email = None
            continue  # Benutzername- bzw. E-Mail-Race: neuer Versuch
        details = {"target_user_id": user.id, "via": profile.source, "external_issuer": profile.issuer,
                   "role": user.role, "email": user.email}
        if role == ROLE_ADMIN:
            details["role_from_groups"] = _admin_matches(policy, profile)[:10]
        await write_audit(db, "USER_CREATE", "user", user.username, user_id=user.id, details=details)
        return user
    assert last_exc is not None
    raise last_exc


async def resolve_external_user(db: AsyncSession, profile: ExternalProfile, policy: ProvisioningPolicy) -> ProvisionResult:
    """Anmeldung eines externen Kontos: Freigabe, Suche ueber die Identitaet, ggf. JIT, Profil und Rolle."""
    check_allowed(policy, profile)
    user = await find_external_user(db, profile.source, profile.issuer, profile.subject)
    if user is None:
        if not policy.jit_enabled:
            raise ProvisioningError("no_account")
        if not policy.jit_permitted:
            logger.warning(
                "SSO (%s): automatische Kontoanlage ist ohne erlaubte Gruppen/Domains und ohne Freigabe fuer alle "
                "Konten gesperrt", policy.source,
            )
            raise ProvisioningError("no_account")
        created = await _create_external_user(db, profile, policy)
        if created is not None:
            return ProvisionResult(created, True, None, [], issuer=profile.issuer, source=profile.source)
        user = await find_external_user(db, profile.source, profile.issuer, profile.subject)
        if user is None:  # pragma: no cover - nur bei gleichzeitigem Loeschen
            raise ProvisioningError("no_account")
    if not user.is_active:
        raise ProvisioningError("account_disabled", user.id)
    changed = await sync_profile(db, user, profile)
    new_role = desired_role(policy, profile, user.role)
    rc = None
    if new_role and new_role != user.role:
        rc = await apply_role(db, user, new_role, source=profile.source, matched=_admin_matches(policy, profile))
    return ProvisionResult(user, False, rc, changed, issuer=profile.issuer, source=profile.source)


async def link_external_identity(db: AsyncSession, user: User, profile: ExternalProfile, policy: ProvisioningPolicy) -> dict:
    """Verknuepft ein lokales Konto mit der externen Identitaet (Selbstverknuepfung nach Re-Authentisierung).

    Danach: Zufallspasswort (alte Sessions ungueltig), Passkeys geloescht, kein erzwungener Passwortwechsel.
    """
    check_allowed(policy, profile)
    other = await find_external_user(db, profile.source, profile.issuer, profile.subject)
    if other is not None and other.id != user.id:
        raise ProvisioningError("link_conflict", user.id)
    if user.role == ROLE_ADMIN:  # static-ok: role-admin (Datenlogik, Notfallzugang)
        await assert_keeps_local_admin(db, user, removing=True)
    user.auth_source = profile.source
    user.external_issuer = profile.issuer
    user.external_id = profile.subject
    user.hashed_password = hash_password(secrets.token_urlsafe(48))
    user.must_change_password = False
    try:
        await db.flush()
    except IntegrityError:
        # paralleles Verknuepfen derselben Identitaet; der Aufrufer rollt die Session zurueck
        raise ProvisioningError("link_conflict", user.id) from None
    res = await db.execute(sql_delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    removed = int(getattr(res, "rowcount", 0) or 0)
    changed = await sync_profile(db, user, profile)
    new_role = desired_role(policy, profile, user.role)
    rc = None
    if new_role and new_role != user.role:
        rc = await apply_role(db, user, new_role, source=profile.source, matched=_admin_matches(policy, profile))
    await db.flush()
    return {
        "passkeys_removed": removed,
        "role_changed": {"from": rc[0], "to": rc[1]} if rc else None,
        "profile_updated": changed,
    }
