"""Pydantic-Schemas fuer SSO (OIDC/LDAP): Einstellungen, Test, Anbieterliste, Verknuepfung (F10 3.1/3.3).

Feldvalidierung nach F10 3.3.2 (422 mit deutschem Text, Strings vorher getrimmt – Secrets nie) plus Plan-Regeln:

- LDAP-Gruppenlisten (``allowed_groups``, ``admin_groups``) nur als vollstaendige DN [S1].
- OIDC ``allowed_email_domains`` (neu, Plan A.7) – Domains klein, ohne ``@``, IDN als Punycode.
- ``jit_allow_any_account`` (beide Quellen) und ``unique_id_attr_confirmed`` (LDAP) [S5, S16]; die Pruefung auf dem
  zusammengefuehrten Stand macht ``services/sso_settings.update``.
- ``group_filter`` darf statt ``{user_dn}`` auch ``{username}`` enthalten (posixGroup/memberUid).
- Step-up-Felder (``step_up``) fuer ``PUT /settings/sso`` [S8]; die Pruefung selbst ist ``core.auth.verify_step_up``
  (WS-F10-APP-BE).

``None`` bedeutet in allen ``*In``-Abschnitten "unveraendert"; Secrets: ``None``/Maske = behalten, ``""`` = loeschen.
"""
from __future__ import annotations

import re
import ssl
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

TOKEN_AUTH_METHODS = ("client_secret_basic", "client_secret_post", "none")
OIDC_PROMPTS = ("", "login", "select_account", "consent")
ROLE_MODES = ("off", "promote", "sync")
LDAP_SECURITY_MODES = ("ldaps", "starttls", "none")
LDAP_GROUP_MODES = ("memberof", "search", "none")
# Unveraenderliche, vom Verzeichnis vergebene IDs (AD, OpenLDAP, 389-DS/Oracle, FreeIPA) [S16]
SAFE_UNIQUE_ID_ATTRS = ("objectGUID", "entryUUID", "nsUniqueId", "ipaUniqueID")

MAX_GROUP_ENTRIES = 50
MAX_GROUP_LEN = 255
MAX_SCOPES = 20

_SCOPE_RE = re.compile(r"^[A-Za-z0-9_:./-]+$")
_CLAIM_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,100}$")
_LDAP_URL_RE = re.compile(r"^ldaps?://[A-Za-z0-9.\-\[\]:]+(:\d{1,5})?/?$", re.IGNORECASE)
_LDAP_ATTR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_DOMAIN_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _insecure_allowed() -> bool:
    return bool(getattr(settings, "SSO_ALLOW_INSECURE", False))


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def _short(v: str, n: int = 80) -> str:
    return v if len(v) <= n else v[: n - 1] + "…"


# ---------------------------------------------------------------------------------------------
# Einzelpruefungen (auch von Tests und dem Test-Endpunkt nutzbar)
# ---------------------------------------------------------------------------------------------
def validate_issuer(v: str) -> str:
    """Leer erlaubt; sonst ``https://host[:port][/pfad]`` (``http://`` nur mit SSO_ALLOW_INSECURE)."""
    v = v.strip()
    if not v:
        return ""
    if any(c.isspace() for c in v):
        raise ValueError("Ungültige Issuer-URL")
    try:
        parts = urlsplit(v)
        port = parts.port
    except ValueError:
        raise ValueError("Ungültige Issuer-URL") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.query or parts.fragment \
            or "@" in parts.netloc or v.endswith("?") or "#" in v or (port is not None and not 0 < port < 65536):
        raise ValueError("Ungültige Issuer-URL")
    if parts.scheme.lower() != "https" and not _insecure_allowed():
        raise ValueError("Die Issuer-URL muss mit https:// beginnen")
    return v


def normalize_scopes(v: str) -> str:
    tokens = [t for t in v.split() if t]
    for t in tokens:
        if not _SCOPE_RE.fullmatch(t):
            raise ValueError(f"Ungültiger Scope: {_short(t)}")
    if "openid" not in tokens:
        tokens.insert(0, "openid")
    deduped = list(dict.fromkeys(tokens))
    if len(deduped) > MAX_SCOPES:
        raise ValueError(f"Höchstens {MAX_SCOPES} Scopes erlaubt")
    return " ".join(deduped)


def validate_claim(v: str, *, required: bool = False) -> str:
    v = v.strip()
    if not v:
        if required:
            raise ValueError("Ungültiger Claim-Name: (leer)")
        return ""
    if not _CLAIM_RE.fullmatch(v):
        raise ValueError(f"Ungültiger Claim-Name: {_short(v)}")
    return v


def normalize_group_list(values: list[Any]) -> list[str]:
    """strip, leere entfernen, ≤ 255 Zeichen, case-insensitiv deduplizieren (erstes Vorkommen bleibt)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if raw is None:
            continue
        g = str(raw).strip()
        if not g:
            continue
        if len(g) > MAX_GROUP_LEN:
            raise ValueError(f"Gruppenname zu lang (max. {MAX_GROUP_LEN} Zeichen): {_short(g)}")
        key = g.lower()
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


def validate_ldap_group_dns(values: list[str]) -> list[str]:
    """LDAP: jeder Eintrag muss eine vollstaendige DN sein; Duplikate nach normalisierter RDN-Folge entfernt [S1]."""
    from app.services.sso_provisioning import dn_key  # lazy: Service-Import nur fuer die Pruefung

    out: list[str] = []
    seen: set = set()
    for g in normalize_group_list(values):
        key = dn_key(g)
        if key is None or len(key) < 2:
            raise ValueError(
                f"Ungültiger Gruppen-DN: {_short(g)} – bitte den vollständigen DN angeben, "
                f"z. B. CN=pdns-admins,OU=Groups,DC=example,DC=com")
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out


def normalize_email_domains(values: list[Any]) -> list[str]:
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        d = str(raw).strip().lower().lstrip("@").rstrip(".")
        if not d:
            continue
        try:
            d = d.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(f"Ungültige E-Mail-Domain: {_short(str(raw).strip())}") from None
        labels = d.split(".")
        if len(d) > 253 or len(labels) < 2 or not all(_DOMAIN_LABEL_RE.fullmatch(lab) for lab in labels) \
                or labels[-1].isdigit():
            raise ValueError(f"Ungültige E-Mail-Domain: {_short(str(raw).strip())}")
        if d not in out:
            out.append(d)
    return out


def validate_ldap_url(v: str) -> str:
    u = v.strip()
    m = _LDAP_URL_RE.fullmatch(u)
    if not m:
        raise ValueError(f"Ungültige LDAP-Server-URL: {_short(u)}")
    try:
        port = urlsplit(u).port
    except ValueError:
        raise ValueError(f"Ungültige LDAP-Server-URL: {_short(u)}") from None
    if port is not None and not 0 < port < 65536:
        raise ValueError(f"Ungültige LDAP-Server-URL: {_short(u)}")
    scheme, rest = u.split("://", 1)
    return f"{scheme.lower()}://{rest.rstrip('/')}"


def validate_ldap_attr(v: str, *, required: bool = False) -> str:
    v = v.strip()
    if not v:
        if required:
            raise ValueError("Ungültiger Attributname: (leer)")
        return ""
    if not _LDAP_ATTR_RE.fullmatch(v):
        raise ValueError(f"Ungültiger Attributname: {_short(v)}")
    return v


def _balanced(v: str) -> bool:
    depth = 0
    for c in v:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def validate_user_filter(v: str) -> str:
    v = v.strip()
    if not v.startswith("(") or "{username}" not in v or not _balanced(v):
        raise ValueError(
            "Der Benutzerfilter muss mit ( beginnen, {username} enthalten und ausgeglichene Klammern haben")
    return v


def validate_group_filter(v: str) -> str:
    v = v.strip()
    if not v.startswith("(") or ("{user_dn}" not in v and "{username}" not in v) or not _balanced(v):
        raise ValueError(
            "Der Gruppenfilter muss mit ( beginnen, {user_dn} (oder {username}) enthalten und ausgeglichene "
            "Klammern haben")
    return v


def validate_ca_cert(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    if "-----BEGIN CERTIFICATE-----" not in v:
        raise ValueError("Das CA-Zertifikat muss im PEM-Format vorliegen")
    try:
        ssl.create_default_context(cadata=v)
    except (ssl.SSLError, ValueError):
        raise ValueError("Das CA-Zertifikat konnte nicht gelesen werden (PEM-Format prüfen)") from None
    return v


def _display_name(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("Die Beschriftung darf nicht leer sein")
    return v


# ---------------------------------------------------------------------------------------------
# Eingaben
# ---------------------------------------------------------------------------------------------
class SsoStepUpIn(BaseModel):
    """Step-up-Felder (Bauplan B.3 ``StepUpBody``) fuer sensible Aenderungen [S8]."""

    current_password: Optional[str] = Field(None, max_length=128)
    totp_code: Optional[str] = Field(None, max_length=12)


class SsoGeneralIn(BaseModel):
    local_login_enabled: Optional[bool] = None
    require_totp: Optional[bool] = None


class OidcSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    display_name: Optional[str] = Field(None, max_length=60)
    issuer: Optional[str] = Field(None, max_length=500)
    client_id: Optional[str] = Field(None, max_length=255)
    client_secret: Optional[str] = Field(None, max_length=1000)  # None/Maske = behalten, "" = loeschen
    token_auth_method: Optional[Literal["client_secret_basic", "client_secret_post", "none"]] = None
    scopes: Optional[str] = Field(None, max_length=500)
    prompt: Optional[Literal["", "login", "select_account", "consent"]] = None
    use_userinfo: Optional[bool] = None
    username_claim: Optional[str] = Field(None, max_length=100)
    email_claim: Optional[str] = Field(None, max_length=100)
    name_claim: Optional[str] = Field(None, max_length=100)
    groups_claim: Optional[str] = Field(None, max_length=100)
    allowed_groups: Optional[list[str]] = Field(None, max_length=MAX_GROUP_ENTRIES)
    admin_groups: Optional[list[str]] = Field(None, max_length=MAX_GROUP_ENTRIES)
    allowed_email_domains: Optional[list[str]] = Field(None, max_length=MAX_GROUP_ENTRIES)
    role_mode: Optional[Literal["off", "promote", "sync"]] = None
    jit_enabled: Optional[bool] = None
    jit_allow_any_account: Optional[bool] = None
    allow_linking: Optional[bool] = None

    @field_validator("display_name")
    @classmethod
    def _v_display_name(cls, v):
        return _display_name(v)

    @field_validator("issuer")
    @classmethod
    def _v_issuer(cls, v):
        return None if v is None else validate_issuer(v)

    @field_validator("client_id")
    @classmethod
    def _v_client_id(cls, v):
        if v is None:
            return None
        v = v.strip()
        if any(ord(c) < 32 for c in v):
            raise ValueError("Ungültige Client-ID")
        return v

    @field_validator("scopes")
    @classmethod
    def _v_scopes(cls, v):
        return None if v is None else normalize_scopes(v)

    @field_validator("username_claim")
    @classmethod
    def _v_username_claim(cls, v):
        return None if v is None else validate_claim(v, required=True)

    @field_validator("email_claim", "name_claim", "groups_claim")
    @classmethod
    def _v_claims(cls, v):
        return None if v is None else validate_claim(v)

    @field_validator("allowed_groups", "admin_groups")
    @classmethod
    def _v_groups(cls, v):
        return None if v is None else normalize_group_list(v)

    @field_validator("allowed_email_domains")
    @classmethod
    def _v_domains(cls, v):
        return None if v is None else normalize_email_domains(v)


class LdapSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    display_name: Optional[str] = Field(None, max_length=60)
    server_urls: Optional[list[str]] = Field(None, max_length=5)
    security: Optional[Literal["ldaps", "starttls", "none"]] = None
    tls_verify: Optional[bool] = None
    ca_cert: Optional[str] = Field(None, max_length=20000)
    timeout: Optional[int] = Field(None, ge=2, le=30)
    bind_dn: Optional[str] = Field(None, max_length=500)
    bind_password: Optional[str] = Field(None, max_length=1000)  # wie client_secret
    user_base_dn: Optional[str] = Field(None, max_length=500)
    user_filter: Optional[str] = Field(None, max_length=500)
    username_attr: Optional[str] = Field(None, max_length=64)
    email_attr: Optional[str] = Field(None, max_length=64)
    name_attr: Optional[str] = Field(None, max_length=64)
    unique_id_attr: Optional[str] = Field(None, max_length=64)
    unique_id_attr_confirmed: Optional[bool] = None
    group_mode: Optional[Literal["memberof", "search", "none"]] = None
    group_base_dn: Optional[str] = Field(None, max_length=500)
    group_filter: Optional[str] = Field(None, max_length=500)
    allowed_groups: Optional[list[str]] = Field(None, max_length=MAX_GROUP_ENTRIES)
    admin_groups: Optional[list[str]] = Field(None, max_length=MAX_GROUP_ENTRIES)
    role_mode: Optional[Literal["off", "promote", "sync"]] = None
    jit_enabled: Optional[bool] = None
    jit_allow_any_account: Optional[bool] = None
    allow_linking: Optional[bool] = None

    @field_validator("display_name")
    @classmethod
    def _v_display_name(cls, v):
        return _display_name(v)

    @field_validator("server_urls")
    @classmethod
    def _v_urls(cls, v):
        if v is None:
            return None
        out: list[str] = []
        for raw in v:
            if raw is None or not str(raw).strip():
                continue
            u = validate_ldap_url(str(raw))
            if u.lower() not in (x.lower() for x in out):
                out.append(u)
        return out

    @field_validator("ca_cert")
    @classmethod
    def _v_ca(cls, v):
        return None if v is None else validate_ca_cert(v)

    @field_validator("bind_dn")
    @classmethod
    def _v_bind_dn(cls, v):
        # AD akzeptiert auch UPN (svc@corp.example) oder DOMAIN\\konto als Bind-Benutzer -> keine DN-Pflicht
        if v is None:
            return None
        v = v.strip()
        if any(ord(c) < 32 for c in v):
            raise ValueError("Ungültiger Bind-Benutzer")
        return v

    @field_validator("user_base_dn", "group_base_dn")
    @classmethod
    def _v_dn(cls, v):
        if v is None:
            return None
        v = v.strip()
        if v:
            from app.services.sso_provisioning import is_valid_dn  # lazy

            if not is_valid_dn(v):
                raise ValueError(f"Ungültiger DN: {_short(v)}")
        return v

    @field_validator("user_filter")
    @classmethod
    def _v_user_filter(cls, v):
        return None if v is None else validate_user_filter(v)

    @field_validator("group_filter")
    @classmethod
    def _v_group_filter(cls, v):
        return None if v is None else validate_group_filter(v)

    @field_validator("username_attr")
    @classmethod
    def _v_username_attr(cls, v):
        return None if v is None else validate_ldap_attr(v, required=True)

    @field_validator("email_attr", "name_attr", "unique_id_attr")
    @classmethod
    def _v_attrs(cls, v):
        return None if v is None else validate_ldap_attr(v)

    @field_validator("allowed_groups", "admin_groups")
    @classmethod
    def _v_groups(cls, v):
        return None if v is None else validate_ldap_group_dns(v)


class SsoSettingsUpdate(BaseModel):
    general: Optional[SsoGeneralIn] = None
    oidc: Optional[OidcSettingsIn] = None
    ldap: Optional[LdapSettingsIn] = None
    step_up: Optional[SsoStepUpIn] = None


class SsoTestRequest(BaseModel):
    target: Literal["oidc", "ldap"]
    oidc: Optional[OidcSettingsIn] = None
    ldap: Optional[LdapSettingsIn] = None
    test_username: Optional[str] = Field(None, max_length=256)
    test_password: Optional[str] = Field(None, max_length=1024)


class SsoLinkStart(BaseModel):
    """``POST /auth/me/sso/oidc/link`` (F10 3.1.4)."""

    current_password: str = Field(..., min_length=1, max_length=128)
    totp_code: Optional[str] = Field(None, max_length=12)


class LdapLinkRequest(BaseModel):
    """``POST /auth/me/sso/ldap/link`` (F10 3.1.5)."""

    current_password: str = Field(..., min_length=1, max_length=128)
    totp_code: Optional[str] = Field(None, max_length=12)
    ldap_username: str = Field(..., min_length=1, max_length=256)
    ldap_password: str = Field(..., min_length=1, max_length=1024)


# ---------------------------------------------------------------------------------------------
# Ausgaben
# ---------------------------------------------------------------------------------------------
class SsoGeneralOut(BaseModel):
    local_login_enabled: bool
    require_totp: bool
    base_url: Optional[str] = None
    redirect_uri: Optional[str] = None
    emergency_login_url: Optional[str] = None
    insecure_allowed: bool = False
    session_max_age: int = 0   # Sekunden (AUTH_COOKIE_MAX_AGE); Warnung im SSO-Tab bei > 86400 (Plan E-F10-2)


class OidcSettingsOut(BaseModel):
    enabled: bool
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    client_secret_set: bool
    client_secret_unreadable: bool = False
    token_auth_method: str
    scopes: str
    prompt: str
    use_userinfo: bool
    username_claim: str
    email_claim: str
    name_claim: str
    groups_claim: str
    allowed_groups: list[str]
    admin_groups: list[str]
    allowed_email_domains: list[str]
    role_mode: str
    jit_enabled: bool
    jit_allow_any_account: bool
    allow_linking: bool
    linked_accounts: int = 0


class LdapSettingsOut(BaseModel):
    enabled: bool
    display_name: str
    server_urls: list[str]
    security: str
    tls_verify: bool
    ca_cert: str
    timeout: int
    bind_dn: str
    bind_password: str
    bind_password_set: bool
    bind_password_unreadable: bool = False
    user_base_dn: str
    user_filter: str
    username_attr: str
    email_attr: str
    name_attr: str
    unique_id_attr: str
    unique_id_attr_confirmed: bool
    unique_id_attr_safe: bool
    group_mode: str
    group_base_dn: str
    group_filter: str
    allowed_groups: list[str]
    admin_groups: list[str]
    role_mode: str
    jit_enabled: bool
    jit_allow_any_account: bool
    allow_linking: bool
    linked_accounts: int = 0


class SsoSettingsOut(BaseModel):
    general: SsoGeneralOut
    oidc: OidcSettingsOut
    ldap: LdapSettingsOut


class SsoSettingsSaveOut(BaseModel):
    message: str
    settings: SsoSettingsOut
    warnings: list[str] = []


class SsoTestResult(BaseModel):
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    warnings: list[str] = []
    details: dict[str, Any] = {}


class SsoProviderOut(BaseModel):
    id: str
    type: str
    label: str
    start_url: str


class SsoLdapProviderOut(BaseModel):
    enabled: bool
    label: Optional[str] = None


class SsoLinkingOut(BaseModel):
    oidc: bool
    ldap: bool


class SsoProvidersOut(BaseModel):
    local_login_enabled: bool
    providers: list[SsoProviderOut]
    ldap: SsoLdapProviderOut
    linking: SsoLinkingOut
