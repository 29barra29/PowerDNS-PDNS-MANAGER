"""SSO-Einstellungen: Defaults, Laden, Validieren, Speichern, oeffentliche Sicht (F10 5.3, Bauplan WS-F10-SVC).

Alle Keys tragen das Praefix ``sso_``, ``oidc_`` oder ``ldap_`` (Tabelle F10 8.2 mit den Plan-Aenderungen A.7).
Lesen und Schreiben ausschliesslich ueber ``services/system_settings.py`` (Secrets ``oidc_client_secret`` und
``ldap_bind_password`` werden dort ver-/entschluesselt; unlesbare Werte kommen als ``UNREADABLE`` zurueck und
gelten als "nicht gesetzt").

Abweichungen von der Spec (Plan hat Vorrang):

- JIT ist per Default aus (``*_jit_enabled=false``). Wer JIT einschaltet, braucht erlaubte Gruppen, bei OIDC
  alternativ erlaubte E-Mail-Domains (greifen nur bei ``email_verified=true``), oder die ausdrueckliche Freigabe
  ``jit_allow_any_account=true`` – sonst 422 [S5].
- ``ldap_unique_id_attr``: ``objectGUID``, ``entryUUID``, ``nsUniqueId``, ``ipaUniqueID`` sind sicher; jedes andere
  Attribut (oder leer = DN) nur mit ``unique_id_attr_confirmed=true`` – sonst 422 [S16].
- LDAP-Gruppenlisten nur als vollstaendige DN (Pruefung im Schema, 422) [S1].
- ``update`` wendet ``guard_secret_retarget`` an (Ziele: OIDC issuer/client_id/token_auth_method, LDAP
  server_urls/bind_dn/security/ca_cert) [S3] und meldet ``changed_sensitive`` fuer den Step-up [S8].
- Der LDAP-Benutzerfilter-Default enthaelt ``(objectCategory=person)``.

Fehler der Konsistenzpruefung: ``SsoSettingsError(status_code, detail)`` – der Router (WS-F10-APP-BE) wandelt sie in
``HTTPException``. ``SecretReentryRequired`` (ValueError) behandelt die App global als 400.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, replace
from typing import Any, Literal, Optional, Union

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_mask import (
    LDAP_TARGET_FIELDS,
    OIDC_TARGET_FIELDS,
    SECRET_MASK,
    guard_secret_retarget,
    pick_targets,
    secret_input_action,
)
from app.core.secrets import is_unreadable
from app.models.models import User
from app.schemas.sso import (
    LDAP_GROUP_MODES,
    LDAP_SECURITY_MODES,
    OIDC_PROMPTS,
    ROLE_MODES,
    SAFE_UNIQUE_ID_ATTRS,
    TOKEN_AUTH_METHODS,
    LdapSettingsIn,
    OidcSettingsIn,
    SsoGeneralIn,
    SsoSettingsUpdate,
    SsoTestRequest,
)
from app.services.sso_provisioning import ProvisioningPolicy
from app.services.system_settings import get_settings, set_settings

logger = logging.getLogger(__name__)

SECRET_KEYS = frozenset({"oidc_client_secret", "ldap_bind_password"})   # Teil von SECRET_SETTING_KEYS (F5)
BASE_URL_KEY = "app_base_url"
CALLBACK_PATH = "/api/v1/auth/oidc/callback"
OIDC_START_PATH = "/api/v1/auth/oidc/start"
EMERGENCY_LOGIN_PATH = "/login?local=1"
DEFAULT_LDAP_USER_FILTER = "(&(objectCategory=person)(objectClass=user)(sAMAccountName={username}))"

# Feldtypen je Abschnitt (Reihenfolge = Ausgabe-Reihenfolge)
_GENERAL_FIELDS: dict[str, str] = {"local_login_enabled": "bool", "require_totp": "bool"}
_OIDC_FIELDS: dict[str, str] = {
    "enabled": "bool", "display_name": "str", "issuer": "str", "client_id": "str", "client_secret": "secret",
    "token_auth_method": "str", "scopes": "str", "prompt": "str", "use_userinfo": "bool",
    "username_claim": "str", "email_claim": "str", "name_claim": "str", "groups_claim": "str",
    "allowed_groups": "list", "admin_groups": "list", "allowed_email_domains": "list", "role_mode": "str",
    "jit_enabled": "bool", "jit_allow_any_account": "bool", "allow_linking": "bool",
}
_LDAP_FIELDS: dict[str, str] = {
    "enabled": "bool", "display_name": "str", "server_urls": "list", "security": "str", "tls_verify": "bool",
    "ca_cert": "str", "timeout": "int", "bind_dn": "str", "bind_password": "secret", "user_base_dn": "str",
    "user_filter": "str", "username_attr": "str", "email_attr": "str", "name_attr": "str",
    "unique_id_attr": "str", "unique_id_attr_confirmed": "bool", "group_mode": "str", "group_base_dn": "str",
    "group_filter": "str", "allowed_groups": "list", "admin_groups": "list", "role_mode": "str",
    "jit_enabled": "bool", "jit_allow_any_account": "bool", "allow_linking": "bool",
}
_SECTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "general": ("sso", _GENERAL_FIELDS),
    "oidc": ("oidc", _OIDC_FIELDS),
    "ldap": ("ldap", _LDAP_FIELDS),
}

# Defaults (Key fehlt = Default; F10 8.2 mit Plan A.7)
DEFAULTS: dict[str, str] = {
    "sso_local_login_enabled": "true",
    "sso_require_totp": "true",
    "oidc_enabled": "false",
    "oidc_display_name": "SSO",
    "oidc_issuer": "",
    "oidc_client_id": "",
    "oidc_client_secret": "",
    "oidc_token_auth_method": "client_secret_basic",
    "oidc_scopes": "openid profile email",
    "oidc_prompt": "",
    "oidc_use_userinfo": "true",
    "oidc_username_claim": "preferred_username",
    "oidc_email_claim": "email",
    "oidc_name_claim": "name",
    "oidc_groups_claim": "groups",
    "oidc_allowed_groups": "[]",
    "oidc_admin_groups": "[]",
    "oidc_allowed_email_domains": "[]",
    "oidc_role_mode": "off",
    "oidc_jit_enabled": "false",
    "oidc_jit_allow_any_account": "false",
    "oidc_allow_linking": "true",
    "ldap_enabled": "false",
    "ldap_display_name": "LDAP",
    "ldap_server_urls": "[]",
    "ldap_security": "ldaps",
    "ldap_tls_verify": "true",
    "ldap_ca_cert": "",
    "ldap_timeout": "8",
    "ldap_bind_dn": "",
    "ldap_bind_password": "",
    "ldap_user_base_dn": "",
    "ldap_user_filter": DEFAULT_LDAP_USER_FILTER,
    "ldap_username_attr": "sAMAccountName",
    "ldap_email_attr": "mail",
    "ldap_name_attr": "displayName",
    "ldap_unique_id_attr": "objectGUID",
    "ldap_unique_id_attr_confirmed": "false",
    "ldap_group_mode": "memberof",
    "ldap_group_base_dn": "",
    "ldap_group_filter": "(&(objectClass=group)(member={user_dn}))",
    "ldap_allowed_groups": "[]",
    "ldap_admin_groups": "[]",
    "ldap_role_mode": "off",
    "ldap_jit_enabled": "false",
    "ldap_jit_allow_any_account": "false",
    "ldap_allow_linking": "true",
}
ALL_KEYS: tuple[str, ...] = tuple(DEFAULTS)

# Erlaubte Werte der Auswahlfelder (manuell veraenderte DB-Werte fallen auf den Default zurueck)
_ENUMS: dict[str, frozenset[str]] = {
    "oidc_token_auth_method": frozenset(TOKEN_AUTH_METHODS),
    "oidc_prompt": frozenset(OIDC_PROMPTS),
    "oidc_role_mode": frozenset(ROLE_MODES),
    "ldap_role_mode": frozenset(ROLE_MODES),
    "ldap_security": frozenset(LDAP_SECURITY_MODES),
    "ldap_group_mode": frozenset(LDAP_GROUP_MODES),
}

# Felder, deren Aenderung einen Step-up verlangt (WS-F10-APP-BE, S8). Plan-Liste plus alles, was Gruppen- bzw.
# Rollenzuordnung, Vertrauensanker oder Freigaben beeinflusst (strenger als die Mindestliste).
SENSITIVE_FIELDS: dict[str, frozenset[str]] = {
    "general": frozenset({"local_login_enabled"}),
    "oidc": frozenset({
        "enabled", "issuer", "client_id", "token_auth_method", "jit_enabled", "jit_allow_any_account",
        "role_mode", "admin_groups", "allowed_groups", "allowed_email_domains", "groups_claim",
    }),
    "ldap": frozenset({
        "enabled", "server_urls", "bind_dn", "security", "tls_verify", "ca_cert", "jit_enabled",
        "jit_allow_any_account", "role_mode", "admin_groups", "allowed_groups", "unique_id_attr",
        "unique_id_attr_confirmed", "group_mode", "group_base_dn", "group_filter",
    }),
}


# ---------------------------------------------------------------------------------------------
# Konfigurationsobjekte
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GeneralCfg:
    local_login_enabled: bool = True
    require_totp: bool = True


@dataclass(frozen=True)
class OidcCfg:
    enabled: bool = False
    display_name: str = "SSO"
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_auth_method: str = "client_secret_basic"
    scopes: str = "openid profile email"
    prompt: str = ""
    use_userinfo: bool = True
    username_claim: str = "preferred_username"
    email_claim: str = "email"
    name_claim: str = "name"
    groups_claim: str = "groups"
    allowed_groups: tuple[str, ...] = ()
    admin_groups: tuple[str, ...] = ()
    allowed_email_domains: tuple[str, ...] = ()
    role_mode: str = "off"
    jit_enabled: bool = False
    jit_allow_any_account: bool = False
    allow_linking: bool = True
    client_secret_unreadable: bool = False


@dataclass(frozen=True)
class LdapCfg:
    enabled: bool = False
    display_name: str = "LDAP"
    server_urls: tuple[str, ...] = ()
    security: str = "ldaps"
    tls_verify: bool = True
    ca_cert: str = ""
    timeout: int = 8
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = DEFAULT_LDAP_USER_FILTER
    username_attr: str = "sAMAccountName"
    email_attr: str = "mail"
    name_attr: str = "displayName"
    unique_id_attr: str = "objectGUID"
    unique_id_attr_confirmed: bool = False
    group_mode: str = "memberof"
    group_base_dn: str = ""
    group_filter: str = "(&(objectClass=group)(member={user_dn}))"
    allowed_groups: tuple[str, ...] = ()
    admin_groups: tuple[str, ...] = ()
    role_mode: str = "off"
    jit_enabled: bool = False
    jit_allow_any_account: bool = False
    allow_linking: bool = True
    bind_password_unreadable: bool = False


@dataclass(frozen=True)
class SsoConfig:
    general: GeneralCfg = field(default_factory=GeneralCfg)
    oidc: OidcCfg = field(default_factory=OidcCfg)
    ldap: LdapCfg = field(default_factory=LdapCfg)
    base_url: str = ""          # oeffentliche Basis-URL ohne "/" am Ende, "" wenn keine


class SsoSettingsError(Exception):
    """Abgelehnte SSO-Einstellung (400 Konsistenz, 422 Sicherheitsbestaetigung fehlt)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class SsoUpdateResult:
    """Ergebnis von ``update`` (fuer ``PUT /settings/sso`` in WS-F10-APP-BE)."""

    config: SsoConfig
    changed: dict[str, Any]
    warnings: list[str]
    sections: list[str]
    changed_sensitive: bool
    jit_allow_any_account: dict[str, bool] = field(default_factory=dict)
    unique_id_attr_confirmed: Optional[bool] = None

    def audit_details(self) -> dict:
        """``details`` fuer ``SSO_SETTINGS_UPDATE`` – nie Secret-Werte.

        ``jit_allow_any_account: true`` (plus ``jit_allow_any_account_sources``), sobald eine Quelle die Freigabe
        fuer alle Konten in diesem Update erhalten hat [S5]; ``unique_id_attr_confirmed`` bei einer Aenderung des
        ID-Attributs bzw. seiner Bestaetigung [S16].
        """
        details: dict[str, Any] = {"sections": list(self.sections), "changed": dict(self.changed)}
        enabled_any = sorted(src for src, on in self.jit_allow_any_account.items() if on)
        if enabled_any:
            details["jit_allow_any_account"] = True
            details["jit_allow_any_account_sources"] = enabled_any
        if self.unique_id_attr_confirmed is not None:
            details["unique_id_attr_confirmed"] = self.unique_id_attr_confirmed
        return details


# ---------------------------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------------------------
def insecure_allowed() -> bool:
    return bool(getattr(settings, "SSO_ALLOW_INSECURE", False))


def _parse_bool(raw: Optional[str], default: str) -> bool:
    val = (raw if raw is not None and str(raw).strip() != "" else default)
    return str(val).strip().lower() == "true"


def _parse_list(key: str, raw: Optional[str], default: str) -> tuple[str, ...]:
    val = raw if raw is not None and str(raw).strip() != "" else default
    try:
        data = json.loads(val)
    except (TypeError, ValueError):
        logger.warning("SSO-Einstellung %s ist kein gueltiges JSON – wird als leere Liste behandelt", key)
        return ()
    if not isinstance(data, list):
        logger.warning("SSO-Einstellung %s ist keine Liste – wird als leere Liste behandelt", key)
        return ()
    return tuple(str(x) for x in data if isinstance(x, (str, int, float)) and str(x).strip())


def _parse_int(raw: Optional[str], default: str, lo: int = 2, hi: int = 30) -> int:
    try:
        val = int(str(raw if raw is not None and str(raw).strip() != "" else default).strip())
    except (TypeError, ValueError):
        val = int(default)
    return max(lo, min(hi, val))


def _resolve_base_url(raw: Optional[str]) -> str:
    """``app_base_url``, sonst erster ``WEBAUTHN_ORIGIN``, sonst "" – nie der Host-Header.

    Gleiche Logik wie ``password_reset_mail.resolve_public_base_url`` (F3, parallel in Welle 1).
    """
    base = (raw or "").strip()
    if not base:
        first = (getattr(settings, "WEBAUTHN_ORIGIN", "") or "").split(",")[0].strip()
        base = first
    return base.rstrip("/")


def _section_from_values(cls, prefix: str, spec: dict[str, str], values: dict[str, Optional[str]]):
    kwargs: dict[str, Any] = {}
    for name, kind in spec.items():
        key = f"{prefix}_{name}"
        raw = values.get(key)
        default = DEFAULTS[key]
        if kind == "bool":
            kwargs[name] = _parse_bool(raw, default)
        elif kind == "int":
            kwargs[name] = _parse_int(raw, default)
        elif kind == "list":
            kwargs[name] = _parse_list(key, raw, default)
        elif kind == "secret":
            if is_unreadable(raw):
                kwargs[name] = ""
                kwargs[f"{name}_unreadable"] = True
            else:
                kwargs[name] = raw or ""
        else:
            val = raw if raw is not None else default
            allowed = _ENUMS.get(key)
            if allowed is not None and val not in allowed:
                logger.warning("SSO-Einstellung %s hat einen unbekannten Wert – Default wird verwendet", key)
                val = default
            kwargs[name] = val
    return cls(**kwargs)


def config_from_values(values: dict[str, Optional[str]]) -> SsoConfig:
    """Baut die Konfiguration aus Rohwerten (``get_settings``-Ergebnis); fehlende Keys = Defaults."""
    return SsoConfig(
        general=_section_from_values(GeneralCfg, "sso", _GENERAL_FIELDS, values),
        oidc=_section_from_values(OidcCfg, "oidc", _OIDC_FIELDS, values),
        ldap=_section_from_values(LdapCfg, "ldap", _LDAP_FIELDS, values),
        base_url=_resolve_base_url(values.get(BASE_URL_KEY)),
    )


async def load_sso_config(db: AsyncSession) -> SsoConfig:
    """EIN Select ueber alle SSO-Keys plus ``app_base_url``."""
    values = await get_settings(db, (*ALL_KEYS, BASE_URL_KEY))
    return config_from_values(values)


# ---------------------------------------------------------------------------------------------
# Abgeleitete Sichten
# ---------------------------------------------------------------------------------------------
def oidc_ready(cfg: SsoConfig) -> bool:
    o = cfg.oidc
    return bool(
        o.enabled and o.issuer and o.client_id and cfg.base_url
        and (o.client_secret or o.token_auth_method == "none")
    )


def ldap_ready(cfg: SsoConfig) -> bool:
    return bool(cfg.ldap.enabled and cfg.ldap.server_urls and cfg.ldap.user_base_dn)


def redirect_uri(cfg: SsoConfig) -> Optional[str]:
    return f"{cfg.base_url}{CALLBACK_PATH}" if cfg.base_url else None


def emergency_login_url(cfg: SsoConfig) -> Optional[str]:
    return f"{cfg.base_url}{EMERGENCY_LOGIN_PATH}" if cfg.base_url else None


def public_providers(cfg: SsoConfig) -> dict:
    """Struktur F10 3.1.1 – ohne Secrets, ohne Issuer."""
    providers = []
    if oidc_ready(cfg):
        providers.append({"id": "oidc", "type": "oidc", "label": cfg.oidc.display_name, "start_url": OIDC_START_PATH})
    ldap_on = ldap_ready(cfg)
    return {
        "local_login_enabled": cfg.general.local_login_enabled,
        "providers": providers,
        "ldap": {"enabled": ldap_on, "label": cfg.ldap.display_name if ldap_on else None},
        "linking": {
            "oidc": oidc_ready(cfg) and cfg.oidc.allow_linking,
            "ldap": ldap_on and cfg.ldap.allow_linking,
        },
    }


def unique_id_attr_is_safe(attr: str) -> bool:
    return (attr or "").strip().lower() in {a.lower() for a in SAFE_UNIQUE_ID_ATTRS}


def settings_out(cfg: SsoConfig, *, linked: Optional[dict[str, int]] = None) -> dict:
    """Struktur F10 3.3.1 (Secrets maskiert) plus die Plan-Felder (E-Mail-Domains, JIT-Freigabe, ID-Bestaetigung)."""
    linked = linked or {}
    o, ld = cfg.oidc, cfg.ldap
    return {
        "general": {
            "local_login_enabled": cfg.general.local_login_enabled,
            "require_totp": cfg.general.require_totp,
            "base_url": cfg.base_url or None,
            "redirect_uri": redirect_uri(cfg),
            "emergency_login_url": emergency_login_url(cfg),
            "insecure_allowed": insecure_allowed(),
        },
        "oidc": {
            "enabled": o.enabled, "display_name": o.display_name, "issuer": o.issuer, "client_id": o.client_id,
            "client_secret": SECRET_MASK if o.client_secret else "", "client_secret_set": bool(o.client_secret),
            "client_secret_unreadable": o.client_secret_unreadable,
            "token_auth_method": o.token_auth_method, "scopes": o.scopes, "prompt": o.prompt,
            "use_userinfo": o.use_userinfo, "username_claim": o.username_claim, "email_claim": o.email_claim,
            "name_claim": o.name_claim, "groups_claim": o.groups_claim,
            "allowed_groups": list(o.allowed_groups), "admin_groups": list(o.admin_groups),
            "allowed_email_domains": list(o.allowed_email_domains), "role_mode": o.role_mode,
            "jit_enabled": o.jit_enabled, "jit_allow_any_account": o.jit_allow_any_account,
            "allow_linking": o.allow_linking, "linked_accounts": int(linked.get("oidc", 0)),
        },
        "ldap": {
            "enabled": ld.enabled, "display_name": ld.display_name, "server_urls": list(ld.server_urls),
            "security": ld.security, "tls_verify": ld.tls_verify, "ca_cert": ld.ca_cert, "timeout": ld.timeout,
            "bind_dn": ld.bind_dn, "bind_password": SECRET_MASK if ld.bind_password else "",
            "bind_password_set": bool(ld.bind_password), "bind_password_unreadable": ld.bind_password_unreadable,
            "user_base_dn": ld.user_base_dn, "user_filter": ld.user_filter, "username_attr": ld.username_attr,
            "email_attr": ld.email_attr, "name_attr": ld.name_attr, "unique_id_attr": ld.unique_id_attr,
            "unique_id_attr_confirmed": ld.unique_id_attr_confirmed,
            "unique_id_attr_safe": unique_id_attr_is_safe(ld.unique_id_attr),
            "group_mode": ld.group_mode, "group_base_dn": ld.group_base_dn, "group_filter": ld.group_filter,
            "allowed_groups": list(ld.allowed_groups), "admin_groups": list(ld.admin_groups),
            "role_mode": ld.role_mode, "jit_enabled": ld.jit_enabled,
            "jit_allow_any_account": ld.jit_allow_any_account, "allow_linking": ld.allow_linking,
            "linked_accounts": int(linked.get("ldap", 0)),
        },
    }


def policy_from(src_cfg: Union[OidcCfg, LdapCfg], source: Optional[str] = None) -> ProvisioningPolicy:
    """Provisioning-Regeln einer Quelle (``source`` wird aus dem Typ abgeleitet, wenn nicht angegeben)."""
    src = source or ("oidc" if isinstance(src_cfg, OidcCfg) else "ldap")
    return ProvisioningPolicy(
        source=src,
        jit_enabled=bool(src_cfg.jit_enabled),
        allowed_groups=tuple(src_cfg.allowed_groups),
        admin_groups=tuple(src_cfg.admin_groups),
        role_mode=src_cfg.role_mode if src_cfg.role_mode in ROLE_MODES else "off",
        allowed_email_domains=tuple(getattr(src_cfg, "allowed_email_domains", ()) if src == "oidc" else ()),
        jit_allow_any_account=bool(src_cfg.jit_allow_any_account),
    )


async def count_linked_accounts(db: AsyncSession) -> dict[str, int]:
    """Anzahl verknuepfter Konten je Quelle (``{"oidc": n, "ldap": n}``)."""
    rows = (await db.execute(
        select(User.auth_source, func.count()).where(User.auth_source.in_(("oidc", "ldap"))).group_by(User.auth_source)
    )).all()
    out = {"oidc": 0, "ldap": 0}
    for src, n in rows:
        if src in out:
            out[src] = int(n or 0)
    return out


# ---------------------------------------------------------------------------------------------
# Zusammenfuehren und Pruefen
# ---------------------------------------------------------------------------------------------
_SectionIn = Union[SsoGeneralIn, OidcSettingsIn, LdapSettingsIn]
_SectionCfg = Union[GeneralCfg, OidcCfg, LdapCfg]


def _merge_section(
    section: str,
    current: _SectionCfg,
    data: Optional[_SectionIn],
) -> tuple[_SectionCfg, dict[str, Any], dict[str, str]]:
    """Request ueber gespeicherte Werte legen. Rueckgabe: (neu, geaenderte Felder {name: (alt, neu)}, Secret-Aktionen)."""
    if data is None:
        return current, {}, {}
    _, spec = _SECTIONS[section]
    updates: dict[str, Any] = {}
    changed: dict[str, Any] = {}
    secret_actions: dict[str, str] = {}
    provided = data.model_dump(exclude_unset=True)
    for name, kind in spec.items():
        if name not in provided:
            continue
        val = getattr(data, name)
        old = getattr(current, name)
        if kind == "secret":
            action = secret_input_action(val)
            if action == "keep":
                continue
            new_val = "" if action == "clear" else val
            secret_actions[name] = action
            updates[name] = new_val
            updates[f"{name}_unreadable"] = False
            if new_val != old or getattr(current, f"{name}_unreadable", False):
                changed[name] = (old, new_val)
            continue
        if val is None:
            continue  # None = unveraendert
        if kind == "list":
            val = tuple(val)
        updates[name] = val
        if val != old:
            changed[name] = (old, val)
    return (replace(current, **updates) if updates else current), changed, secret_actions


def _check_consistency(cfg: SsoConfig) -> None:
    """Konsistenzregeln F10 3.3.2 auf dem zusammengefuehrten Stand (400, exakte Texte)."""
    o, ld = cfg.oidc, cfg.ldap
    if o.enabled and (not o.issuer or not o.client_id):
        raise SsoSettingsError(400, "Für OIDC müssen Issuer-URL und Client-ID gesetzt sein")
    if o.enabled and not cfg.base_url:
        raise SsoSettingsError(
            400, "Für OIDC muss zuerst die öffentliche Basis-URL gesetzt werden (Einstellungen → Profil)")
    if o.enabled and o.token_auth_method != "none" and not o.client_secret:
        raise SsoSettingsError(400, "Für die gewählte Client-Authentifizierung fehlt das Client-Secret")
    if ld.enabled and (not ld.server_urls or not ld.user_base_dn):
        raise SsoSettingsError(400, "Für LDAP müssen Server und Suchbasis gesetzt sein")
    for url in ld.server_urls:
        is_ldaps = url.lower().startswith("ldaps://")
        if (ld.security == "ldaps" and not is_ldaps) or (ld.security in ("starttls", "none") and is_ldaps):
            raise SsoSettingsError(
                400, "Verschlüsselung und Server-URL passen nicht zusammen (LDAPS braucht ldaps://, StartTLS ldap://)")
    if (ld.security == "none" or ld.tls_verify is False) and not insecure_allowed():
        raise SsoSettingsError(
            400, "Unverschlüsselte LDAP-Verbindungen bzw. das Abschalten der Zertifikatsprüfung erfordern "
                 "SSO_ALLOW_INSECURE=true")
    if ld.group_mode == "search" and not ld.group_base_dn:
        raise SsoSettingsError(400, "Für die Gruppensuche wird eine Suchbasis benötigt")
    for src_cfg, no_groups in ((o, not o.groups_claim), (ld, ld.group_mode == "none")):
        if src_cfg.role_mode != "off" and not src_cfg.admin_groups:
            raise SsoSettingsError(
                400, "Für die Rollen-Zuordnung muss mindestens eine Admin-Gruppe eingetragen sein")
        if src_cfg.role_mode != "off" and no_groups:
            raise SsoSettingsError(
                400, "Für die Rollen-Zuordnung müssen Gruppen ermittelt werden (Gruppen-Claim bzw. Gruppenmodus)")
        if src_cfg.allowed_groups and no_groups:
            raise SsoSettingsError(
                400, "Für erlaubte Gruppen müssen Gruppen ermittelt werden (Gruppen-Claim bzw. Gruppenmodus)")
    if cfg.general.local_login_enabled is False and not (o.enabled or ld.enabled):
        raise SsoSettingsError(
            400, "Die lokale Anmeldung kann nur abgeschaltet werden, wenn OIDC oder LDAP aktiv ist")


JIT_OPEN_OIDC_DETAIL = (
    "Automatische Kontoanlage (JIT) ohne Einschränkung: bitte erlaubte Gruppen oder E-Mail-Domains eintragen "
    "oder ausdrücklich bestätigen, dass sich jedes Konto des Anbieters anmelden darf (jit_allow_any_account)"
)
JIT_OPEN_LDAP_DETAIL = (
    "Automatische Kontoanlage (JIT) ohne Einschränkung: bitte erlaubte Gruppen eintragen oder ausdrücklich "
    "bestätigen, dass sich jedes Konto des Verzeichnisses anmelden darf (jit_allow_any_account)"
)


def _unique_id_detail(attr: str) -> str:
    safe = ", ".join(SAFE_UNIQUE_ID_ATTRS)
    if not attr:
        return (f"Ohne ID-Attribut wird der DN als Kennung verwendet – Umbenennen oder Verschieben trennt die "
                f"Verknüpfung, ein neues Konto mit gleichem DN übernimmt sie. Empfohlen: {safe}. Bitte ausdrücklich "
                f"bestätigen (unique_id_attr_confirmed)")
    return (f"Das ID-Attribut {attr} ist nicht als unveränderlich bekannt (empfohlen: {safe}). Ein änderbares "
            f"Attribut kann bei Umbenennung oder Neuvergabe fremde Konten übernehmen – bitte ausdrücklich bestätigen "
            f"(unique_id_attr_confirmed)")


def _check_security_confirmations(cfg: SsoConfig) -> None:
    """Plan-Regeln S5 (JIT-Freigabe) und S16 (ID-Attribut) auf dem zusammengefuehrten Stand (422)."""
    o, ld = cfg.oidc, cfg.ldap
    if o.jit_enabled and not (o.allowed_groups or o.allowed_email_domains or o.jit_allow_any_account):
        raise SsoSettingsError(422, JIT_OPEN_OIDC_DETAIL)
    if ld.jit_enabled and not (ld.allowed_groups or ld.jit_allow_any_account):
        raise SsoSettingsError(422, JIT_OPEN_LDAP_DETAIL)
    if not unique_id_attr_is_safe(ld.unique_id_attr) and not ld.unique_id_attr_confirmed:
        raise SsoSettingsError(422, _unique_id_detail(ld.unique_id_attr))


def _audit_value(name: str, kind: str, old: Any, new: Any) -> Any:
    if kind == "secret":
        return "removed" if not new else "changed"
    if name == "ca_cert":
        return "changed"
    if kind == "list":
        return {"from": list(old or ()), "to": list(new or ())}
    return {"from": old, "to": new}


def _storage_value(kind: str, value: Any) -> Any:
    if kind == "list":
        return json.dumps(list(value), ensure_ascii=False)
    if kind == "int":
        return str(int(value))
    return value  # bool -> "true"/"false" macht set_settings, str bleibt


# ---------------------------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------------------------
async def update(db: AsyncSession, data: SsoSettingsUpdate) -> SsoUpdateResult:
    """Zusammenfuehren, pruefen und speichern (nur ``flush``; Commit macht ``DbWrite``).

    Reihenfolge: Retarget-Schutz [S3] -> Konsistenz (400) -> Sicherheitsbestaetigungen S5/S16 (422) -> Speichern.
    Wirft ``SecretReentryRequired`` bzw. ``SsoSettingsError``. Leert die OIDC-Caches, wenn sich Issuer,
    Client-ID oder ``enabled`` geaendert haben.
    """
    current = await load_sso_config(db)

    ld_in = data.ldap
    if ld_in is not None and ld_in.unique_id_attr is not None and ld_in.unique_id_attr_confirmed is None:
        # Ein anderes ID-Attribut uebernimmt die alte Bestaetigung nicht [S16].
        if ld_in.unique_id_attr.strip().lower() != current.ldap.unique_id_attr.strip().lower():
            ld_in = ld_in.model_copy(update={"unique_id_attr_confirmed": False})  # setzt auch fields_set

    new_general, ch_general, _ = _merge_section("general", current.general, data.general)
    new_oidc, ch_oidc, _ = _merge_section("oidc", current.oidc, data.oidc)
    new_ldap, ch_ldap, _ = _merge_section("ldap", current.ldap, ld_in)

    # Retarget-Schutz: gespeichertes Secret nie an ein geaendertes Ziel [S3]
    if data.oidc is not None:
        guard_secret_retarget(
            targets_before=pick_targets(_as_dict(current.oidc), OIDC_TARGET_FIELDS),
            targets_after=pick_targets(_as_dict(new_oidc), OIDC_TARGET_FIELDS),
            secret_in=data.oidc.client_secret,
            secret_stored=bool(current.oidc.client_secret),
        )
    if ld_in is not None:
        guard_secret_retarget(
            targets_before=pick_targets(_as_dict(current.ldap), LDAP_TARGET_FIELDS),
            targets_after=pick_targets(_as_dict(new_ldap), LDAP_TARGET_FIELDS),
            secret_in=ld_in.bind_password,
            secret_stored=bool(current.ldap.bind_password),
        )

    new_cfg = replace(current, general=new_general, oidc=new_oidc, ldap=new_ldap)
    _check_consistency(new_cfg)
    _check_security_confirmations(new_cfg)

    # Speichern (nur geaenderte bzw. gesendete Felder)
    to_store: dict[str, Any] = {}
    audit_changed: dict[str, Any] = {}
    for section, ch in (("general", ch_general), ("oidc", ch_oidc), ("ldap", ch_ldap)):
        prefix, spec = _SECTIONS[section]
        for name, (old, new) in ch.items():
            kind = spec[name]
            key = f"{prefix}_{name}"
            to_store[key] = _storage_value(kind, new)
            audit_changed[key] = _audit_value(name, kind, old, new)
    if to_store:
        await set_settings(db, to_store)

    sections = [s for s, d in (("general", data.general), ("oidc", data.oidc), ("ldap", data.ldap)) if d is not None]
    changed_sensitive = any(
        name in SENSITIVE_FIELDS[section]
        for section, ch in (("general", ch_general), ("oidc", ch_oidc), ("ldap", ch_ldap))
        for name in ch
    )

    warnings = await _warnings(db, current, new_cfg, ch_oidc, ch_ldap)

    if {"issuer", "client_id", "enabled"} & set(ch_oidc):
        from app.services import sso_oidc  # lazy: sso_oidc importiert dieses Modul

        sso_oidc.clear_caches()

    jit_any = {}
    if "jit_allow_any_account" in ch_oidc:
        jit_any["oidc"] = new_oidc.jit_allow_any_account
    if "jit_allow_any_account" in ch_ldap:
        jit_any["ldap"] = new_ldap.jit_allow_any_account
    confirmed = None
    if {"unique_id_attr", "unique_id_attr_confirmed"} & set(ch_ldap):
        confirmed = bool(new_ldap.unique_id_attr_confirmed)

    return SsoUpdateResult(
        config=new_cfg,
        changed=audit_changed,
        warnings=warnings,
        sections=sections,
        changed_sensitive=changed_sensitive,
        jit_allow_any_account=jit_any,
        unique_id_attr_confirmed=confirmed,
    )


# Spec-Name (F10 5.3)
save_sso_update = update


def _as_dict(section_cfg: _SectionCfg) -> dict[str, Any]:
    return {f.name: getattr(section_cfg, f.name) for f in fields(section_cfg)}


async def _warnings(db: AsyncSession, before: SsoConfig, after: SsoConfig,
                    ch_oidc: dict[str, Any], ch_ldap: dict[str, Any]) -> list[str]:
    out: list[str] = []
    linked: Optional[dict[str, int]] = None
    if "issuer" in ch_oidc and before.oidc.issuer:
        linked = linked or await count_linked_accounts(db)
        if linked["oidc"] > 0:
            out.append(
                f"{linked['oidc']} bestehende OIDC-Konten sind an den bisherigen Issuer gebunden und werden nicht "
                f"übernommen – diese Personen erhalten bei der nächsten Anmeldung neue Konten.")
    if "unique_id_attr" in ch_ldap:
        linked = linked or await count_linked_accounts(db)
        if linked["ldap"] > 0:
            out.append(
                f"{linked['ldap']} bestehende LDAP-Konten sind über das bisherige ID-Attribut verknüpft und werden "
                f"nicht übernommen.")
    o, ld = after.oidc, after.ldap
    if o.jit_enabled and not o.allowed_groups and not o.allowed_email_domains:
        out.append("OIDC: Ohne erlaubte Gruppen kann sich jedes Konto des Anbieters anmelden (ohne Zonenrechte).")
    if ld.jit_enabled and not ld.allowed_groups:
        out.append("LDAP: Ohne erlaubte Gruppen kann sich jedes Konto des Verzeichnisses anmelden (ohne Zonenrechte).")
    if o.allowed_email_domains:
        out.append("OIDC: Erlaubte E-Mail-Domains greifen nur, wenn der Anbieter die Adresse als bestätigt meldet "
                   "(email_verified).")
    if not unique_id_attr_is_safe(ld.unique_id_attr):
        attr = ld.unique_id_attr or "DN"
        out.append(f"LDAP: Das ID-Attribut {attr} ist änderbar – Umbenennungen oder Neuvergaben können Konten falsch "
                   f"zuordnen.")
    return out


# ---------------------------------------------------------------------------------------------
# Test-Endpunkt
# ---------------------------------------------------------------------------------------------
async def build_test_config(db: AsyncSession, req: SsoTestRequest) -> SsoConfig:
    """Formularwerte ueber die gespeicherten legen (nichts wird gespeichert) – fuer ``POST /settings/sso/test``.

    Das gespeicherte Secret wird nur verwendet, wenn alle Zielfelder unveraendert sind (``guard_secret_retarget``,
    sonst ``SecretReentryRequired`` -> 400) [S3]. ``enabled`` spielt fuer den Test keine Rolle.
    """
    current = await load_sso_config(db)
    target: Literal["oidc", "ldap"] = req.target
    if target == "oidc":
        merged, _, _ = _merge_section("oidc", current.oidc, req.oidc)
        if req.oidc is not None:
            guard_secret_retarget(
                targets_before=pick_targets(_as_dict(current.oidc), OIDC_TARGET_FIELDS),
                targets_after=pick_targets(_as_dict(merged), OIDC_TARGET_FIELDS),
                secret_in=req.oidc.client_secret,
                secret_stored=bool(current.oidc.client_secret),
            )
        return replace(current, oidc=merged)
    merged_l, _, _ = _merge_section("ldap", current.ldap, req.ldap)
    if req.ldap is not None:
        guard_secret_retarget(
            targets_before=pick_targets(_as_dict(current.ldap), LDAP_TARGET_FIELDS),
            targets_after=pick_targets(_as_dict(merged_l), LDAP_TARGET_FIELDS),
            secret_in=req.ldap.bind_password,
            secret_stored=bool(current.ldap.bind_password),
        )
    return replace(current, ldap=merged_l)
