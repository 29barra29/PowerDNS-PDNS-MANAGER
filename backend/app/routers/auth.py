"""API-Routen fuer Anmeldung, eigenes Konto und Benutzerverwaltung.

Seit 3.0 liegen die Panel-Token-Verwaltung in ``routers/panel_tokens.py`` und die Webhook-Verwaltung in
``routers/webhooks.py`` (Pfade unveraendert). Der Login-Abschluss (Cookie, Audit ``LOGIN``, Metrik,
Fehlzaehler) laeuft ueber ``services.login_session`` (Aliase ``_user_to_dict``, ``_set_session_cookie``,
``_complete_login``). Schreibende Handler nutzen ``DbWrite`` (Commit vor der Antwort, Bauplan B.7).

Benutzerverwaltung (F2/F3): Zufallspasswort mit Einmalanzeige, erzwungener Passwortwechsel
(``users.must_change_password``), Reset-Link per Mail (``services/password_reset_mail.py``), 2FA-/Passkey-Reset,
Zugangs-Widerruf (``services/access_revocation.py``) und Schutzregeln (``services/user_guard.py``: E-Mail-Duplikat
409, letzter aktiver Admin, externe Konten). Alle Admin-Mutationen verlangen eine Browser-Session
(``get_admin_session_user``); ein Admin kann diese Aktionen nicht auf sein eigenes Konto anwenden (F3 E11).

SSO (F10, Welle 2): ``POST /auth/login`` prueft erst das lokale Konto, dann – bei aktivem LDAP – das Verzeichnis
(Login-Zaehler je IP und Benutzername vor jedem Passwortvergleich und jedem LDAP-Bind, [S2]); der 2FA-Schritt nimmt
das Pending-Token auch aus dem Cookie des OIDC-Callbacks. Externe Konten (``auth_source`` oidc/ldap) haben weder
Passwort-Login noch Passwort-Reset/-Wechsel noch Passkeys; Benutzername und E-Mail verwaltet der Anmeldedienst.
Bei aktivem SSO bleibt immer ein aktiver lokaler Admin (Notfallzugang, ``user_guard.assert_keeps_local_admin``).
``convert-to-local`` verlangt einen Step-up des Admins (``core.auth.verify_step_up``, [S8]). Die SSO-Flows selbst
liegen in ``routers/sso.py``, die SSO-Einstellungen in ``routers/settings_sso.py``.
"""
import json
import logging
import secrets
import time
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import APIRouter, Body, HTTPException, Depends, Request, status, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator, EmailStr, BeforeValidator, AfterValidator
from typing import Optional, Annotated
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as prom
from app.core.timeutil import iso_utc
from app.core.config import settings as app_settings
from app.core.database import DbRead, DbWrite
from app.core.client_ip import get_client_ip
from app.core.login_rate_limit import clear_login_fails, is_login_rate_limited, record_failed_login
import pyotp
from starlette.concurrency import run_in_threadpool
from app.core.secrets import is_unreadable
from app.services import access_revocation, login_session, user_guard
from app.services import panel_token as ptk
from app.services.audit import write_audit
from app.services.password_reset_mail import (
    ADMIN_VALID_MINUTES, SELF_SERVICE_VALID_MINUTES, ResetMailError, reset_mail_available, send_password_reset_mail,
)
from app.core.auth import (
    get_session_user, get_admin_session_user, totp_verify_once, decode_password_reset_payload, password_version,
    hash_password, verify_password, create_access_token,
    create_two_factor_pending_token, decode_two_factor_pending_token, decode_two_factor_pending_payload,
    create_webauthn_challenge_token, decode_webauthn_challenge_token,
    get_current_user, get_admin_user,
    generate_random_password, MIN_PASSWORD_LENGTH,
    TOKEN_TYPE_WEBAUTHN_REG, TOKEN_TYPE_WEBAUTHN_AUTH,
    StepUpBody, totp_secret_state, verify_step_up,
)
from app.models.models import (
    User, UserZoneAccess, WebAuthnCredential, PanelToken, Webhook, WebhookDelivery,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Gleicher Text fuer IP- und Benutzer-Sperre (keine Aussage, welche greift)
RATE_LIMIT_DETAIL = "Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen."

# Aliase (Bauplan B.4): Implementierung in services/login_session.py
_user_to_dict = login_session.user_to_dict
_set_session_cookie = login_session.set_session_cookie
_complete_login = login_session.complete_login

# Hook fuer F10 (F3 5.3): Passwort-Aktionen nur fuer lokale Konten (auth_source == "local")
_ensure_local_account = user_guard.ensure_local_account

# --- F5 5.13: 2FA-Geheimnis nicht entschluesselbar -------------------------------------------------------
TOTP_UNREADABLE_LOGIN = (
    "2FA-Geheimnis kann nicht entschlüsselt werden – bitte mit Passkey anmelden oder einen Admin bitten, "
    "2FA zurückzusetzen."
)
TOTP_UNREADABLE_DISABLE = (
    "2FA-Geheimnis kann nicht entschlüsselt werden – Deaktivierung nur über einen Admin (2FA zurücksetzen)."
)


def _totp_secret_state(user) -> Literal["ok", "missing", "unreadable"]:
    """Zustand des aktiven TOTP-Geheimnisses: ``unreadable`` (Chiffretext nicht entschluesselbar), ``missing``, ``ok``.

    Implementierung in ``core.auth.totp_secret_state`` (gemeinsam mit ``routers/sso.py`` und ``verify_step_up``).
    """
    return totp_secret_state(user)  # type: ignore[return-value]


# --- Texte der Benutzerverwaltung (F3) ------------------------------------------------------------------
USER_NOT_FOUND = "Benutzer nicht gefunden"
OWN_PASSWORD_DETAIL = "Das eigene Passwort bitte unter Einstellungen ändern"
OWN_FORCE_CHANGE_DETAIL = "Für das eigene Konto kann kein Passwortwechsel erzwungen werden"
OWN_DEACTIVATE_DETAIL = "Du kannst dich nicht selbst deaktivieren"
OWN_DEMOTE_DETAIL = "Du kannst dir die Admin-Rolle nicht selbst entziehen"
OWN_2FA_DETAIL = "Die eigene 2FA bitte unter Einstellungen → API & Sicherheit verwalten"
OWN_PASSKEYS_DETAIL = "Die eigenen Passkeys bitte unter Einstellungen → API & Sicherheit verwalten"
OWN_ACCESS_DETAIL = "Die eigenen Zugänge bitte unter Einstellungen → API & Sicherheit verwalten"
CURRENT_PASSWORD_WRONG = "Aktuelles Passwort ist falsch"
NEW_PASSWORD_SAME = "Das neue Passwort muss sich vom aktuellen unterscheiden"
FORGOT_PASSWORD_REPLY = {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}
RESET_LINK_ERRORS = {
    "smtp_disabled": (400, "E-Mail-Versand ist nicht eingerichtet (Einstellungen → SMTP)"),
    "no_base_url": (400, "Keine öffentliche Basis-URL konfiguriert (Einstellungen → Profil → Öffentliche Basis-URL)"),
    "no_email": (400, "Für diesen Benutzer ist keine E-Mail-Adresse hinterlegt"),
    "send_failed": (502, "E-Mail konnte nicht gesendet werden – Details im Server-Log"),
}

# Reset-Link-Sperre je Ziel-Benutzer (In-Memory, Single-Worker – Rahmenentscheidung; F3 5.3)
_RESET_LINK_LAST: dict[int, float] = {}
_RESET_LINK_MIN_INTERVAL = 60.0


def _reset_link_rate_limited(user_id: int) -> bool:
    """True, wenn fuer diesen Benutzer innerhalb der letzten 60 s schon ein Link ging; sonst Zeitstempel setzen."""
    now = time.monotonic()
    for k in [k for k, t in _RESET_LINK_LAST.items() if now - t > _RESET_LINK_MIN_INTERVAL]:
        _RESET_LINK_LAST.pop(k, None)
    if user_id in _RESET_LINK_LAST:
        return True
    _RESET_LINK_LAST[user_id] = now
    return False


def _coded_error(status_code: int, detail: str, code: str) -> JSONResponse:
    """Fehlerantwort ``{"detail": <Text>, "code": <Maschinencode>}`` (``detail`` bleibt ein String)."""
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


def _blank_to_none(v):
    """Leere Strings aus Formularen als "nicht gesetzt" behandeln."""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _validate_single_email(v):
    """Genau EINE syntaktisch gueltige Adresse. Special-Use-Domains wie .local/.lan sind
    erlaubt (Default-Admin ist admin@dns-manager.local, Homelab-Setups nutzen .lan)."""
    if v is None:
        return None
    import re as _re
    from email_validator import EmailNotValidError, validate_email
    try:
        return validate_email(v, check_deliverability=False, globally_deliverable=False).normalized
    except EmailNotValidError as exc:
        # email-validator lehnt Special-Use-Domains (.local, .home.arpa ...) grundsaetzlich ab.
        # Fuer Homelabs ist das legitim -> minimaler Syntax-Check: genau ein @, keine
        # Trennzeichen/Whitespace (das eigentliche Ziel: keine Mehrfachempfaenger).
        if "special-use" in str(exc) and _re.fullmatch(r"[^\s@,;<>\"'()\[\]]+@[^\s@,;<>\"'()\[\]]+\.[A-Za-z0-9-]+", v.strip()):
            local, _, domain = v.strip().rpartition("@")
            return f"{local}@{domain.lower()}"
        raise ValueError(f"Ungueltige E-Mail-Adresse: {exc}") from exc


# E-Mail-Adressen werden validiert (genau eine Adresse). Vorher waren die Felder freie
# Strings: "a@x.tld, b@y.tld" machte den Panel-SMTP zum Mail-Relay. Ein leerer String
# wird zu None (= Adresse entfernen, wenn das Feld mitgeschickt wurde).
OptionalEmail = Annotated[Optional[str], BeforeValidator(_blank_to_none), AfterValidator(_validate_single_email)]


# ========================
# Schemas
# ========================
# Anmerkung: Bewusst KEIN LoginResponse-Schema mit access_token mehr exportiert –
# der Token lebt ausschließlich im HttpOnly-Cookie, das Frontend greift nie auf das JWT zu.


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    email: OptionalEmail = None
    display_name: Optional[str] = Field(None, max_length=255)
    role: str = Field(default="user", pattern="^(admin|user)$")
    # F3: Passwortwechsel beim ersten Login erzwingen (das UI setzt es standardmaessig)
    must_change_password: bool = False


class UserUpdate(BaseModel):
    email: OptionalEmail = None
    display_name: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern="^(admin|user)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=MIN_PASSWORD_LENGTH, max_length=128)
    # F3: None = unveraendert (auch beim Setzen eines Passworts, API-Rueckwaertskompatibilitaet)
    must_change_password: Optional[bool] = None
    # [S9]: Panel-Tokens, DynDNS-Tokens und Webhooks des Benutzers widerrufen (access_revocation.revoke_all)
    revoke_all_access: bool = False


class AdminPasswordResetBody(BaseModel):
    """Optionaler Body von ``PUT /auth/users/{id}/reset-password`` (fehlt er, gelten die Defaults, F3 E6)."""
    must_change_password: bool = True
    revoke_all_access: bool = False


class ConvertToLocalBody(BaseModel):
    """``POST /auth/users/{id}/convert-to-local`` (F10 3.2.10): Passwortwechsel erzwingen (Default) + Step-up [S8]."""
    must_change_password: bool = True
    step_up: Optional[StepUpBody] = None


class RevokeAccessBody(BaseModel):
    """``POST /auth/users/{id}/revoke-access``: was widerrufen bzw. zurueckgesetzt wird ([S9])."""
    panel_tokens: bool = True
    dyndns_tokens: bool = True
    webhooks: bool = True
    reset_2fa: bool = False
    remove_passkeys: bool = False


# Sprachen des Frontends (F8 3.3); leer = Default der Instanz
PROFILE_LANGUAGES = frozenset({"de", "en", "sr", "hr", "bs", "hu"})


class ProfileUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=100)
    email: OptionalEmail = None
    display_name: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=25)
    company: Optional[str] = Field(None, max_length=255)
    street: Optional[str] = Field(None, max_length=255)
    postal_code: Optional[str] = Field(None, max_length=20)
    city: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    date_of_birth: Optional[str] = None  # ISO date string
    preferred_language: Optional[str] = Field(None, max_length=10)  # de, en

    # F8 5.2: Leere Eingaben bleiben "" (nicht None), damit "mitgeschickt und leer" (= Feld loeschen) von
    # "nicht mitgeschickt" (= unveraendert) unterscheidbar bleibt; update_profile wertet model_fields_set aus.
    @field_validator("phone")
    @classmethod
    def phone_at_least_one_digit(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        if not s:
            return ""
        if not any(c.isdigit() for c in s):
            raise ValueError("Telefon muss mindestens eine Ziffer enthalten")
        return s

    @field_validator("postal_code", "city", "country")
    @classmethod
    def strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip()

    @field_validator("preferred_language")
    @classmethod
    def supported_language(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().lower()
        if s and s not in PROFILE_LANGUAGES:
            raise ValueError("Nicht unterstützte Sprache")
        return s


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)


class ZoneAccessUpdate(BaseModel):
    zones: list[str] = Field(..., description="Liste der Zonen")
    zone_permissions: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional: Zonenname -> read oder manage (Fehlende = manage)",
    )

    @field_validator("zone_permissions")
    @classmethod
    def _validate_zone_perms(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if not v:
            return v
        out = {}
        for k, val in v.items():
            p = (val or "manage").strip().lower()
            if p not in ("read", "manage"):
                raise ValueError("permission muss read oder manage sein")
            out[k.strip()] = p
        return out


class RegisterPublic(BaseModel):
    """Public registration (only when registration_enabled)."""
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    email: OptionalEmail = None
    display_name: Optional[str] = Field(None, max_length=255)
    captcha_token: Optional[str] = Field(None, max_length=4096, description="Captcha-Token vom Browser (nur wenn aktiviert)")


class ForgotPasswordRequest(BaseModel):
    email: Optional[str] = Field(None, description="E-Mail des Kontos")
    username: Optional[str] = Field(None, description="Benutzername (Alternative zu E-Mail)")
    captcha_token: Optional[str] = Field(None, max_length=4096, description="Captcha-Token vom Browser (nur wenn aktiviert)")


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="Token aus der E-Mail", max_length=4096)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)


async def _get_auth_setting(db: AsyncSession, key: str) -> bool:
    from app.models.models import SystemSetting
    result = await db.execute(select(SystemSetting.value).where(SystemSetting.key == key))
    value = result.scalar_one_or_none()  # Einzelne Spalte => Skalar (str), nicht Row
    if value is None:
        return False
    return str(value).strip().lower() == "true"


# ========================
# Auth Endpoints
# ========================
class TwoFactorComplete(BaseModel):
    """Abschluss der Anmeldung nach TOTP.

    ``two_factor_token`` aus ``/login`` (``need_two_factor``); fehlt er, gilt das HttpOnly-Cookie ``pdnsmgr_2fa``
    aus dem OIDC-Callback (F10 3.2.2).
    """
    two_factor_token: Annotated[Optional[str], BeforeValidator(_blank_to_none)] = Field(
        None, min_length=20, max_length=4096)
    totp_code: str = Field(..., min_length=4, max_length=12)


def _rate_limited(method: str) -> HTTPException:
    """429 mit identischem Text fuer IP- und Benutzer-Sperre (Metrik ``rate_limited``)."""
    prom.record_login(method, "rate_limited")
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_DETAIL)


# --- F10: Anmeldung mit LDAP und externen Konten (5.8) --------------------------------------------------------
BAD_CREDENTIALS_DETAIL = "Falscher Benutzername oder Passwort"
ACCOUNT_DISABLED_DETAIL = "Konto ist deaktiviert"
LOCAL_LOGIN_DISABLED_DETAIL = "Die Anmeldung mit lokalem Konto ist deaktiviert – bitte über SSO anmelden."
LDAP_UNAVAILABLE_DETAIL = "Der Anmeldedienst (LDAP) ist nicht erreichbar. Bitte später erneut versuchen."
LDAP_CONFIG_DETAIL = "Die LDAP-Anmeldung ist fehlerhaft konfiguriert – bitte den Administrator informieren."
_PROVISIONING_LOGIN_TEXT = {
    "not_allowed": "Dein Konto ist nicht für dieses Panel freigegeben.",
    "no_account": "Für dieses Konto ist im Panel kein Zugang eingerichtet. Bitte wende dich an den Administrator.",
    "account_disabled": ACCOUNT_DISABLED_DETAIL,
}
EXTERNAL_PASSKEY_LOGIN_DETAIL = "Die Passkey-Anmeldung ist für Konten mit SSO- oder LDAP-Anmeldung nicht möglich"
EXTERNAL_PASSKEY_REGISTER_DETAIL = "Passkeys sind für Konten mit SSO- oder LDAP-Anmeldung nicht verfügbar"
EXTERNAL_USERNAME_DETAIL = "Der Benutzername wird beim Anmeldedienst verwaltet und kann nicht geändert werden"
EXTERNAL_EMAIL_SELF_DETAIL = "Die E-Mail-Adresse wird beim Anmeldedienst verwaltet und kann nicht geändert werden"
EXTERNAL_EMAIL_ADMIN_DETAIL = "Die E-Mail-Adresse externer Konten wird beim Anmeldedienst verwaltet"
MAX_LOGIN_USERNAME = 100

_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    """Bcrypt-Hash eines Zufallswerts: gleicht die Laufzeit fuer unbekannte Benutzer an (Fund f39)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


def _is_external(user) -> bool:
    """Externes Konto (OIDC/LDAP): Passwort, Benutzername und E-Mail verwaltet der Anmeldedienst."""
    return not user_guard.is_local_account(user)


async def _load_sso_config(db: AsyncSession):
    from app.services.sso_settings import load_sso_config  # lazy: SSO-Dienste nur bei Bedarf laden

    return await load_sso_config(db)


async def _local_login_enabled(db: AsyncSession) -> bool:
    return (await _load_sso_config(db)).general.local_login_enabled


@router.post("/login")
async def login(
    db: DbWrite,
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    captcha_token: Optional[str] = Form(default=None, description="Captcha-Token (nur wenn aktiviert)"),
    totp_code: Optional[str] = Form(default=None, description="6-stelliger TOTP-Code, falls 2FA aktiv"),
):
    """Anmeldung mit Benutzername und Passwort (lokal, sonst LDAP). Setzt das HttpOnly-Cookie (kein Token im Body).

    Drosselung je IP (/64) UND je Benutzername (B.3 [S2]): ein gesperrter Benutzername loest weder Captcha-Pruefung
    noch DB-Lookup, Passwortvergleich oder LDAP-Bind aus. Reihenfolge (F10 5.8): lokales Konto mit passendem
    Passwort, sonst – bei aktivem LDAP – Suche + Bind im Verzeichnis (auch nach falschem lokalem Passwort; die
    Zuordnung laeuft nur ueber die externe ID). Externe Konten haben kein nutzbares lokales Passwort.
    """
    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip, form_data.username):
        raise _rate_limited("password")

    # Captcha vor dem Login pruefen, damit Bots keinen Brute-Force gegen DB+Hash starten koennen.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, captcha_token, get_client_ip(request))

    from app.services import sso_ldap
    from app.services.sso_provisioning import ProvisioningError, resolve_external_user
    from app.services.sso_settings import ldap_ready, policy_from

    cfg = await _load_sso_config(db)
    username = (form_data.username or "").strip()
    password = form_data.password or ""
    user = None
    if 0 < len(username) <= MAX_LOGIN_USERNAME:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    ldap_on = ldap_ready(cfg)
    method, authed, prov = "password", False, None
    if user is not None and user_guard.is_local_account(user):
        authed = verify_password(password, user.hashed_password)
    elif not ldap_on:
        verify_password(password, _dummy_hash())   # unbekannt bzw. extern ohne LDAP: gleiche Laufzeit (f39)

    if not authed and ldap_on and username and password:
        # [S2] erneut vor dem Bind: ein gesperrter Name erreicht das Verzeichnis nie (kein AD-Lockout)
        if is_login_rate_limited(client_ip, username):
            raise _rate_limited("ldap")
        try:
            ident = await sso_ldap.authenticate(cfg.ldap, username, password)
        except sso_ldap.LdapUnavailable:
            prom.record_login("ldap", "failure")
            await write_audit(db, "LOGIN_FAILED", "user", username[:255], status="error",
                              details={"ip": client_ip, "method": "ldap", "reason": "ldap_unavailable"})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=LDAP_UNAVAILABLE_DETAIL)
        except sso_ldap.LdapConfigError as exc:
            logger.error("LDAP-Konfigurationsfehler: %s", exc)
            prom.record_login("ldap", "failure")
            await write_audit(db, "LOGIN_FAILED", "user", username[:255], status="error",
                              details={"ip": client_ip, "method": "ldap", "reason": "ldap_config"})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=LDAP_CONFIG_DETAIL)
        if ident is not None:
            try:
                prov = await resolve_external_user(db, ident.profile, policy_from(cfg.ldap, "ldap"))
            except ProvisioningError as pe:
                prom.record_login("ldap", "denied")
                await write_audit(db, "LOGIN_FAILED", "user", username[:255], user_id=pe.user_id, status="error",
                                  details={"ip": client_ip, "method": "ldap", "reason": pe.code})
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail=_PROVISIONING_LOGIN_TEXT.get(pe.code, _PROVISIONING_LOGIN_TEXT["not_allowed"]))
            user, authed, method = prov.user, True, "ldap"

    if not authed:
        record_failed_login(client_ip, username or form_data.username)
        prom.record_login("ldap" if ldap_on else "password", "failure")
        await write_audit(
            db, "LOGIN_FAILED", "user", (username or form_data.username or "")[:255],
            user_id=user.id if user else None, status="error",
            details={"ip": client_ip, "reason": "bad_credentials", "method": "ldap" if ldap_on else "password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_CREDENTIALS_DETAIL)

    if not user.is_active:
        prom.record_login(method, "denied")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ACCOUNT_DISABLED_DETAIL)

    # Lokale Anmeldung abgeschaltet: Notfallzugang nur fuer Admins (Rolle des Ziel-Kontos, kein Token-Kontext)
    if method == "password" and not cfg.general.local_login_enabled and user.role != "admin":  # static-ok: role-admin
        prom.record_login("password", "denied")
        await write_audit(db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
                          details={"ip": client_ip, "method": "password", "reason": "local_login_disabled"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCAL_LOGIN_DISABLED_DETAIL)

    # Beim LDAP-Login kann der Panel-Name vom eingegebenen Namen abweichen (jdoe -> jdoe-2): beide Zaehler loeschen
    if method == "ldap" and username and username.lower() != (user.username or "").lower():
        clear_login_fails(client_ip, username)
    audit_extra = prov.audit_extra if prov else None

    # --- 2FA (TOTP) ------------------------------------------------------------
    if getattr(user, "totp_enabled", False):
        if _totp_secret_state(user) == "unreadable":
            # Passwort war korrekt -> kein Fehlzaehler; Recovery: Passkey oder Admin setzt 2FA zurueck (F5 5.13)
            prom.record_login(method, "denied")
            await write_audit(
                db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
                details={"ip": client_ip, "method": method, "reason": "totp_unreadable"},
            )
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=TOTP_UNREADABLE_LOGIN)
        sec = (user.totp_secret or "").strip()
        if not sec:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="2FA fehlerhaft konfiguriert (kein Geheimnis) – bitte Admin kontaktieren",
            )
        code = (totp_code or "").strip().replace(" ", "")
        if not code:
            prom.record_login(method, "2fa_required")
            pending = create_two_factor_pending_token(user.id, method=method)
            return JSONResponse(
                status_code=200,
                content={
                    "need_two_factor": True,
                    "two_factor_token": pending,
                },
            )
        if not totp_verify_once(user.id, sec, code):
            record_failed_login(client_ip, user.username)
            prom.record_login("totp", "failure")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Falscher TOTP-Code",
            )
        method = f"{method}+totp"

    # Erfolg: last_login, Benutzer-Zaehler des Paars loeschen, Audit LOGIN, Metrik, Cookie (B.4)
    return await _complete_login(db, user, request, method=method, audit_extra=audit_extra)


@router.post("/login/2fa")
async def login_two_factor(
    db: DbWrite,
    data: TwoFactorComplete,
    request: Request,
):
    """TOTP-Code + pending-Token (Body oder Cookie ``pdnsmgr_2fa``), wenn 2FA aktiv. Setzt das Session-Cookie.

    Die Methode der ersten Stufe steht im Token (``password``/``ldap``/``oidc``); Audit ``<methode>+totp``. Bei
    ``password`` gilt die Abschaltung der lokalen Anmeldung (ausser Admins). OIDC-Anmeldungen loeschen keine
    Login-Zaehler (kein Passwort-Raten).
    """
    from app.services.login_session import TWO_FACTOR_COOKIE, TWO_FACTOR_COOKIE_PATH, delete_transient_cookie

    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip):
        raise _rate_limited("totp")
    from_cookie = not data.two_factor_token
    token = data.two_factor_token or request.cookies.get(TWO_FACTOR_COOKIE)
    payload = decode_two_factor_pending_payload(token)
    if not payload:
        record_failed_login(client_ip)
        prom.record_login("totp", "failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder abgelaufener Zweitschritt-Token",
        )
    first = payload["m"]
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not getattr(user, "totp_enabled", False):
        record_failed_login(client_ip)
        prom.record_login("totp", "failure")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht anmeldbar")
    # Benutzer-Zaehler: dieselbe Sperre wie beim Passwort-Login (kein TOTP-Raten ueber /login/2fa)
    if is_login_rate_limited(client_ip, user.username):
        raise _rate_limited("totp")
    if first == "password" and _is_external(user):
        # Token einer lokalen Anmeldung, Konto inzwischen extern verknuepft
        record_failed_login(client_ip)
        prom.record_login("totp", "failure")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht anmeldbar")
    if first == "password" and user.role != "admin" and not await _local_login_enabled(db):  # static-ok: role-admin
        prom.record_login("totp", "denied")
        await write_audit(db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
                          details={"ip": client_ip, "method": "password", "reason": "local_login_disabled"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCAL_LOGIN_DISABLED_DETAIL)
    if _totp_secret_state(user) == "unreadable":
        prom.record_login("totp", "denied")
        await write_audit(
            db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
            details={"ip": client_ip, "method": f"{first}+totp", "reason": "totp_unreadable"},
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=TOTP_UNREADABLE_LOGIN)
    sec = (user.totp_secret or "").strip()
    if not sec or not totp_verify_once(user.id, sec, data.totp_code):
        record_failed_login(client_ip, user.username)
        prom.record_login("totp", "failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher TOTP-Code",
        )
    resp = await _complete_login(db, user, request, method=f"{first}+totp", clear_fails=(first != "oidc"))
    if from_cookie:
        delete_transient_cookie(resp, TWO_FACTOR_COOKIE, path=TWO_FACTOR_COOKIE_PATH)
    return resp


@router.post("/logout")
async def logout():
    """Abmelden: Auth-Cookie löschen (Frontend speichert keinen Token mehr)."""
    response = JSONResponse(content={"message": "Abgemeldet"})
    response.delete_cookie(key=app_settings.AUTH_COOKIE_NAME, path="/")
    return response


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_public(
    db: DbWrite,
    data: RegisterPublic,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Register a new user (only when registration is enabled in settings).

    F10 3.2.12: Ist die Anmeldung mit lokalen Konten abgeschaltet, ist auch die Registrierung aus (ein neues lokales
    Konto koennte sich ohnehin nicht anmelden).
    """
    if not await _get_auth_setting(db, "registration_enabled") or not await _local_login_enabled(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung ist deaktiviert",
        )

    # Captcha gegen Spam-/Bot-Registrierungen.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, data.captcha_token, get_client_ip(request))

    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Benutzername existiert bereits")

    if data.email:
        result = await db.execute(select(User).where(User.email == data.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="E-Mail wird bereits verwendet")

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    logger.info(f"New user registered: {data.username}")

    # Welcome-Mail im Hintergrund versenden, damit der Register-Request nicht
    # auf SMTP wartet (und auch nicht fehlschlaegt, wenn der Mailserver kurz down ist).
    if data.email:
        background_tasks.add_task(
            _send_welcome_email_safe,
            user_id=user.id,
            base_url=str(request.base_url).rstrip("/"),
        )

    return {"message": "Registrierung erfolgreich. Du kannst dich jetzt anmelden."}


async def _send_welcome_email_safe(user_id: int, base_url: str) -> None:
    """Background-Task: laedt Settings, rendert Template, schickt Mail.
    Eigene DB-Session, weil die Request-Session bereits geschlossen ist.
    Schluckt alle Exceptions - die Registrierung darf NICHT zurueckgerollt werden,
    nur weil SMTP gerade hakt.
    """
    from app.core.database import async_session
    from app.services.email_service import (
        get_smtp_settings,
        get_welcome_email_settings,
        send_email,
    )
    from app.services.email_templates import render_welcome_email, pick_language
    from app.models.models import SystemSetting

    try:
        async with async_session() as session:
            welcome = await get_welcome_email_settings(session)
            if not welcome["enabled"]:
                return
            smtp = await get_smtp_settings(session)
            if not smtp.get("enabled") or not smtp.get("host"):
                logger.info("Welcome email skipped: SMTP not configured")
                return

            user_row = await session.execute(select(User).where(User.id == user_id))
            user = user_row.scalar_one_or_none()
            if not user or not user.email:
                return

            name_row = await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "app_name")
            )
            app_name = (name_row.scalar_one_or_none() or app_settings.APP_NAME or "PDNS Manager").strip()
            base_row = await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "app_base_url")
            )
            real_base = (base_row.scalar_one_or_none() or "").strip() or base_url

            lang = pick_language(user.preferred_language, app_settings.DEFAULT_LANGUAGE)
            subject, body_html, body_text = render_welcome_email(
                lang=lang,
                subject_template=welcome["subject"],
                body_template=welcome["body"],
                username=user.username,
                display_name=user.display_name or user.username,
                email=user.email,
                app_name=app_name,
                login_url=f"{real_base.rstrip('/')}/login",
            )
            await run_in_threadpool(send_email, smtp, user.email, subject, body_html, body_text)
            logger.info("Welcome email sent to %s", user.email)
    except Exception as exc:  # noqa: BLE001 - Hintergrund-Task soll nie crashen
        logger.warning("Failed to send welcome email (user_id=%s): %s", user_id, exc)


@router.post("/forgot-password")
async def forgot_password(
    db: DbWrite,
    data: ForgotPasswordRequest,
    request: Request,
):
    """Request a password reset email (only when forgot_password is enabled)."""
    if not await _get_auth_setting(db, "forgot_password_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Passwort vergessen ist deaktiviert",
        )

    # Captcha schuetzt davor, dass jemand massenhaft Reset-Mails an fremde Adressen triggert.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, data.captcha_token, get_client_ip(request))

    if not data.email and not data.username:
        raise HTTPException(status_code=400, detail="E-Mail oder Benutzername angeben")

    user = None
    if data.email:
        result = await db.execute(select(User).where(User.email == data.email))
        user = result.scalar_one_or_none()
    if not user and data.username:
        result = await db.execute(select(User).where(User.username == data.username))
        user = result.scalar_one_or_none()

    # Immer gleiche Antwort (keine Hinweise ob Konto existiert). Externe Konten (SSO/LDAP) und deaktivierte
    # Konten bekommen keinen Link: ihr Passwort verwaltet der Identitaetsanbieter bzw. der Link waere wertlos.
    # Ist die lokale Anmeldung abgeschaltet, bekommen nur Admins (Notfallzugang) einen Link (F10 3.2.12).
    if not user or not user.email or not user.is_active or not user_guard.is_local_account(user):
        return dict(FORGOT_PASSWORD_REPLY)
    if user.role != "admin" and not await _local_login_enabled(db):  # static-ok: role-admin (Ziel-Konto)
        return dict(FORGOT_PASSWORD_REPLY)
    try:
        await send_password_reset_mail(db, user, valid_minutes=SELF_SERVICE_VALID_MINUTES, admin_initiated=False)
    except ResetMailError as exc:
        # Details stehen bereits im Log des Service; der Client erfaehrt nichts (keine Konto-Enumeration)
        logger.warning("Passwort-Reset-Mail nicht versendet (user_id=%s): %s", user.id, exc.code)
    return dict(FORGOT_PASSWORD_REPLY)


@router.post("/reset-password")
async def reset_password(
    db: DbWrite,
    data: ResetPasswordRequest,
    request: Request,
):
    """Set new password using the token from the email (Self-Service- und Admin-Reset-Link)."""
    payload = decode_password_reset_payload(data.token)
    if not payload:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link. Bitte fordere einen neuen an.")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not user_guard.is_local_account(user):
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link.")
    # Einmal-Nutzung: Link passt nur zum Passwort-Hash, der beim Anfordern galt.
    if payload.get("pwv") != password_version(user.hashed_password):
        raise HTTPException(status_code=400, detail="Dieser Link wurde bereits verwendet. Bitte fordere einen neuen an.")

    user.hashed_password = hash_password(data.new_password)
    user.must_change_password = False  # F3 3.2.9: der Nutzer hat selbst ein neues Passwort gesetzt
    await db.flush()
    logger.info(f"Password reset for user id={user_id}")
    await write_audit(db, "PASSWORD_RESET", "user", user.username, user_id=user.id,
                      details={"ip": get_client_ip(request) or "unknown", "via": "link"})
    return {"message": "Passwort wurde geändert. Du kannst dich jetzt anmelden."}


def _auth_block(request: Request) -> dict:
    """``auth``-Block fuer ``GET /auth/me`` (F14 3.1): Art des Zugangs und – bei Panel-Tokens – dessen Scope.

    Nie Hash oder Klartext; nur Name, Praefix und Einschraenkungen.
    """
    via = getattr(request.state, "auth_via", None)
    pt = getattr(request.state, "panel_token", None)
    scope = getattr(request.state, "token_scope", None)
    token = None
    if via == "panel_token" and pt is not None and scope is not None:
        token = {
            "id": pt.id,
            "name": pt.name,
            "token_prefix": pt.token_prefix,
            "scope_zones": sorted(scope.zones) if scope.zones is not None else None,
            "permission": scope.permission,
            "allow_admin": bool(scope.allow_admin),
            "expires_at": iso_utc(pt.expires_at),
        }
    return {"via": via, "token": token}


@router.get("/me")
async def get_me(
    request: Request,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Eigenes Konto inkl. ``auth``-Block (Session oder Panel-Token mit Scope)."""
    out = await _user_to_dict(current_user, db)
    out["auth"] = _auth_block(request)
    return out


@router.put("/me")
async def update_profile(
    db: DbWrite,
    data: ProfileUpdate,
    current_user: User = Depends(get_session_user),
):
    """Update own profile (username, email, display_name).

    Externe Konten (F10 3.2.6): Benutzername und E-Mail verwaltet der Anmeldedienst (400 bei einer Aenderung);
    Anzeigename und uebrige Felder bleiben editierbar.
    """
    if _is_external(current_user):
        if data.username is not None and data.username != current_user.username:
            raise HTTPException(status_code=400, detail=EXTERNAL_USERNAME_DETAIL)
        if "email" in data.model_fields_set and (data.email or None) != (current_user.email or None):
            raise HTTPException(status_code=400, detail=EXTERNAL_EMAIL_SELF_DETAIL)
    if data.username is not None and data.username != current_user.username:
        # Check if new username is already taken
        result = await db.execute(select(User).where(User.username == data.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Benutzername ist bereits vergeben")
        current_user.username = data.username
    
    if "email" in data.model_fields_set:  # mitgeschickt: None/"" = Adresse entfernen
        if data.email and data.email != current_user.email:
            # Check if email is already taken
            result = await db.execute(select(User).where(User.email == data.email))
            existing = result.scalar_one_or_none()
            if existing and existing.id != current_user.id:
                raise HTTPException(status_code=400, detail="E-Mail ist bereits vergeben")
        current_user.email = data.email or None
    
    # F8 5.2: mitgeschickt (model_fields_set) -> setzen, leer/Whitespace -> NULL; nicht mitgeschickt -> unveraendert
    fs = data.model_fields_set
    if "display_name" in fs:
        current_user.display_name = (data.display_name or "").strip() or None

    for attr in ("phone", "company", "street", "postal_code", "city", "country"):
        if attr in fs:
            setattr(current_user, attr, (getattr(data, attr) or "").strip() or None)
    if "date_of_birth" in fs:
        if data.date_of_birth:
            try:
                current_user.date_of_birth = date.fromisoformat(data.date_of_birth)
            except ValueError:
                current_user.date_of_birth = None
        else:
            current_user.date_of_birth = None
    if "preferred_language" in fs:
        current_user.preferred_language = (data.preferred_language or "").strip() or None
    
    await db.flush()
    logger.info(f"User '{current_user.username}' updated their profile")
    return {
        "message": "Profil aktualisiert",
        "user": await _user_to_dict(current_user, db),
    }


@router.put("/me/password")
async def change_password(
    db: DbWrite,
    data: PasswordChange,
    current_user: User = Depends(get_session_user),
):
    """Eigenes Passwort aendern (auch der Ausweg aus dem erzwungenen Passwortwechsel, F3 3.2.8).

    Fehler tragen zusaetzlich ``code`` (``current_password_wrong`` / ``password_unchanged``), damit das Frontend
    sie ohne Textvergleich erkennt.
    """
    _ensure_local_account(current_user)
    if not verify_password(data.current_password, current_user.hashed_password):
        return _coded_error(400, CURRENT_PASSWORD_WRONG, "current_password_wrong")
    if verify_password(data.new_password, current_user.hashed_password):
        return _coded_error(400, NEW_PASSWORD_SAME, "password_unchanged")

    forced = bool(getattr(current_user, "must_change_password", False))
    current_user.hashed_password = hash_password(data.new_password)
    # Erzwungener Passwortwechsel ist damit erledigt (Gate in core.auth, F3 3.2.8)
    current_user.must_change_password = False
    await db.flush()
    await write_audit(db, "PASSWORD_CHANGE", "user", current_user.username, user_id=current_user.id,
                      details={"forced": forced})
    # Alle anderen Sessions sind durch die pwv-Bindung jetzt ungueltig; die eigene bekommt
    # ein frisches Cookie, damit der Nutzer nicht mitten im Panel ausgeloggt wird.
    token = create_access_token(data={"sub": str(current_user.id), "role": current_user.role}, user=current_user)
    return _set_session_cookie(JSONResponse(content={"message": "Passwort geaendert"}), token)


# ========================
# User Management (Admin only)
# ========================
@router.get("/users")
async def list_users(
    db: DbRead,
    admin: User = Depends(get_admin_user),
):
    """Alle Benutzer mit Zonenrechten, Passkey- und Token-Zaehlern. Admin only.

    ``password_reset_mail_available``: SMTP und oeffentliche Basis-URL sind eingerichtet (Reset-Link-Button).
    F10 3.2.11: je Benutzer ``external_issuer``/``external_id`` (nur hier, nicht in ``/auth/me``), dazu der Block
    ``sso`` (Rollen-Modus und JIT je Quelle) fuer Badges und Hinweise der Benutzerverwaltung.
    """
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    passkeys = await db.execute(
        select(WebAuthnCredential.user_id, func.count(WebAuthnCredential.id)).group_by(WebAuthnCredential.user_id)
    )
    passkey_counts = {int(uid): int(n) for uid, n in passkeys.all()}
    token_counts = await ptk.count_active_by_user(db)
    out = []
    for u in users:
        d = await _user_to_dict(u, db)
        d["passkey_count"] = passkey_counts.get(u.id, 0)
        d["panel_token_count"] = token_counts.get(u.id, 0)
        d["external_issuer"] = getattr(u, "external_issuer", None)
        d["external_id"] = getattr(u, "external_id", None)
        out.append(d)
    cfg = await _load_sso_config(db)
    sso = {"oidc_role_mode": cfg.oidc.role_mode, "ldap_role_mode": cfg.ldap.role_mode,
           "oidc_jit": cfg.oidc.jit_enabled, "ldap_jit": cfg.ldap.jit_enabled}
    return {"users": out, "password_reset_mail_available": await reset_mail_available(db), "sso": sso}


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    return user


async def _flush_or_409(db: AsyncSession, detail: str) -> None:
    """Flush; ein Unique-Verstoss (paralleles Anlegen/Aendern) wird zu 409 statt 500."""
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=detail)


@router.post("/users", status_code=201)
async def create_user(
    db: DbWrite,
    data: UserCreate,
    admin: User = Depends(get_admin_session_user),
):
    """Create a new user. Admin only. E-Mail-Duplikat -> 409, Benutzername-Duplikat -> 400 (F3 3.2.2)."""
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Benutzername existiert bereits")
    if await user_guard.email_taken(db, data.email):
        raise HTTPException(status_code=409, detail=user_guard.EMAIL_TAKEN_DETAIL)

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
        role=data.role,
        is_active=True,
        must_change_password=bool(data.must_change_password),
    )
    db.add(user)
    await _flush_or_409(db, user_guard.USERNAME_OR_EMAIL_TAKEN_DETAIL)

    logger.info(f"User '{data.username}' created by admin '{admin.username}'")
    await write_audit(db, "USER_CREATE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "role": user.role, "email": user.email,
                               "must_change_password": bool(user.must_change_password)})
    return await _user_to_dict(user, db)


@router.put("/users/{user_id}")
async def update_user(
    db: DbWrite,
    user_id: int,
    data: UserUpdate,
    admin: User = Depends(get_admin_session_user),
):
    """Update a user. Admin only.

    Alle Pruefungen laufen vor jeder Aenderung (F3 3.2.3): Selbstschutz (eigenes Passwort, erzwungener Wechsel,
    Deaktivierung, Herabstufung, Zugangs-Widerruf), letzter aktiver Admin, E-Mail-Duplikat (409), lokales Konto.
    """
    user = await _get_user_or_404(db, user_id)
    self_edit = user.id == admin.id
    if self_edit and data.password is not None:
        raise HTTPException(status_code=400, detail=OWN_PASSWORD_DETAIL)
    if self_edit and data.must_change_password:
        raise HTTPException(status_code=400, detail=OWN_FORCE_CHANGE_DETAIL)
    if self_edit and data.is_active is False:
        raise HTTPException(status_code=400, detail=OWN_DEACTIVATE_DETAIL)
    if self_edit and data.role == "user" and user.role == "admin":  # static-ok: role-admin (Ziel-Konto)
        raise HTTPException(status_code=400, detail=OWN_DEMOTE_DETAIL)
    if self_edit and data.revoke_all_access:
        raise HTTPException(status_code=400, detail=OWN_ACCESS_DETAIL)
    await user_guard.assert_keeps_active_admin(
        db, user,
        new_role=data.role or user.role,
        new_active=user.is_active if data.is_active is None else data.is_active,
    )
    # F10 3.2.8: Notfallzugang (letzter aktiver lokaler Admin bei aktivem SSO)
    await user_guard.assert_keeps_local_admin(
        db, user,
        new_role=data.role or user.role,
        new_active=user.is_active if data.is_active is None else data.is_active,
    )
    if _is_external(user) and "email" in data.model_fields_set and (data.email or None) != (user.email or None):
        raise HTTPException(status_code=400, detail=EXTERNAL_EMAIL_ADMIN_DETAIL)
    email_change = "email" in data.model_fields_set and data.email and data.email != user.email
    if email_change and await user_guard.email_taken(db, data.email, exclude_user_id=user.id):
        raise HTTPException(status_code=409, detail=user_guard.EMAIL_TAKEN_DETAIL)
    if data.password is not None or data.must_change_password is not None:
        _ensure_local_account(user)

    before = {"email": user.email, "display_name": user.display_name, "role": user.role,
              "is_active": user.is_active, "must_change_password": bool(user.must_change_password)}
    if "email" in data.model_fields_set:
        user.email = data.email  # None = Adresse entfernen
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.hashed_password = hash_password(data.password)
    if data.must_change_password is not None:
        user.must_change_password = bool(data.must_change_password)
    await _flush_or_409(db, user_guard.EMAIL_TAKEN_DETAIL)
    revoked = None
    if data.revoke_all_access:
        revoked = await access_revocation.revoke_all(db, user.id, reason="admin_user_update")

    changed = {k: {"from": before[k], "to": getattr(user, k)} for k in before if before[k] != getattr(user, k)}
    if data.password is not None:
        changed["password"] = "set_by_admin"
    details = {"target_user_id": user.id, "changed": changed}
    if revoked is not None:
        details["revoked"] = revoked
    await write_audit(db, "USER_UPDATE", "user", user.username, user_id=admin.id, details=details)
    out = await _user_to_dict(user, db)
    if revoked is not None:
        out["revoked"] = revoked
    return out


@router.delete("/users/{user_id}")
async def delete_user(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Delete a user and their zone assignments. Admin only.

    Der letzte aktive lokale Admin bleibt bei aktivem SSO erhalten (Notfallzugang, F10 3.2.9). DynDNS-Tokens loescht
    ``services/dyndns.delete_tokens_of_user`` (WS-F9F11-BE).
    """
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst loeschen")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    await user_guard.assert_keeps_local_admin(db, user, removing=True)

    # Laufzeit-Import: services/dyndns.py liefert WS-F9F11-BE parallel in Welle 2 (Cross-Item F9, Plan C)
    from app.services import dyndns as dyndns_service

    # Alle benutzergebundenen Daten entfernen (keine FK-Constraints im Schema -> manuell).
    # Zustellungen (Outbox) VOR den Webhooks loeschen (F6 3.9).
    await db.execute(sql_delete(UserZoneAccess).where(UserZoneAccess.user_id == user_id))
    await db.execute(sql_delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id))
    await db.execute(sql_delete(PanelToken).where(PanelToken.user_id == user_id))
    deleted_dyndns = await dyndns_service.delete_tokens_of_user(db, user_id)
    res_deliveries = await db.execute(sql_delete(WebhookDelivery).where(WebhookDelivery.user_id == user_id))
    res_webhooks = await db.execute(sql_delete(Webhook).where(Webhook.user_id == user_id))
    await write_audit(db, "USER_DELETE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "role": user.role,
                               "auth_source": getattr(user, "auth_source", None) or "local",
                               "deleted_webhooks": int(res_webhooks.rowcount or 0),
                               "deleted_webhook_deliveries": int(res_deliveries.rowcount or 0),
                               "deleted_dyndns_tokens": int(deleted_dyndns or 0)})
    await db.delete(user)
    await db.flush()
    return {"message": f"Benutzer '{user.username}' geloescht"}


@router.put("/users/{user_id}/reset-password")
async def reset_user_password(
    db: DbWrite,
    user_id: int,
    data: Optional[AdminPasswordResetBody] = Body(default=None),
    admin: User = Depends(get_admin_session_user),
):
    """Setzt ein neues Zufallspasswort (16 Zeichen) und gibt es genau einmal zurueck. Admin only.

    Ohne Body wird der Passwortwechsel beim naechsten Login erzwungen (F3 E6); ``revoke_all_access`` widerruft
    zusaetzlich alle Zugaenge ([S9]). Bestehende Sitzungen des Nutzers enden ueber die pwv-Bindung.
    """
    opts = data or AdminPasswordResetBody()
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail=OWN_PASSWORD_DETAIL)
    _ensure_local_account(user)

    new_password = generate_random_password(16)
    user.hashed_password = hash_password(new_password)
    user.must_change_password = bool(opts.must_change_password)
    await db.flush()
    revoked = None
    if opts.revoke_all_access:
        revoked = await access_revocation.revoke_all(db, user.id, reason="admin_password_reset")
    logger.info(f"Admin '{admin.username}' reset password for user '{user.username}' (id={user.id})")
    details = {"target_user_id": user.id, "must_change_password": bool(user.must_change_password)}
    if revoked is not None:
        details["revoked"] = revoked
    await write_audit(db, "USER_PASSWORD_RESET", "user", user.username, user_id=admin.id, details=details)
    return {
        "message": f"Passwort für '{user.username}' wurde zurückgesetzt.",
        "username": user.username,
        "new_password": new_password,  # nur dieses eine Mal
        "hint": "Bitte unverzüglich an den Nutzer weitergeben – das Passwort wird nicht erneut angezeigt.",
        "must_change_password": bool(user.must_change_password),
        "revoked": revoked,
    }


@router.post("/users/{user_id}/send-reset-link")
async def send_user_reset_link(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Schickt dem Nutzer einen Reset-Link (24 h gueltig, einmal verwendbar) an seine gespeicherte Adresse.

    Das Passwort bleibt bis dahin unveraendert, Sitzungen bleiben gueltig. Unabhaengig von
    ``forgot_password_enabled``. 60-s-Sperre je Ziel-Benutzer (F3 3.2.5).
    """
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail=OWN_PASSWORD_DETAIL)
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Benutzer ist deaktiviert")
    _ensure_local_account(user)
    if not user.email:
        raise HTTPException(status_code=400, detail=RESET_LINK_ERRORS["no_email"][1])
    if _reset_link_rate_limited(user.id):
        raise HTTPException(
            status_code=429,
            detail="Für diesen Benutzer wurde gerade erst ein Link gesendet – bitte eine Minute warten",
        )
    valid_hours = ADMIN_VALID_MINUTES // 60
    try:
        await send_password_reset_mail(db, user, valid_minutes=ADMIN_VALID_MINUTES, admin_initiated=True)
    except ResetMailError as exc:
        status_code, detail = RESET_LINK_ERRORS.get(exc.code, RESET_LINK_ERRORS["send_failed"])
        if exc.code == "send_failed":
            await write_audit(db, "USER_PASSWORD_RESET_LINK", "user", user.username, user_id=admin.id,
                              status="error", error_message="send_failed",
                              details={"target_user_id": user.id, "email": user.email, "valid_hours": valid_hours})
        else:
            # Konfigurationsfehler sollen nicht zum Warten zwingen
            _RESET_LINK_LAST.pop(user.id, None)
        raise HTTPException(status_code=status_code, detail=detail)
    await write_audit(db, "USER_PASSWORD_RESET_LINK", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "email": user.email, "valid_hours": valid_hours})
    return {"message": f"Reset-Link an {user.email} gesendet", "email": user.email, "valid_hours": valid_hours}


async def _reset_totp(db: AsyncSession, admin: User, user: User) -> bool:
    """2FA (TOTP) des Nutzers abschalten; True, wenn sich etwas geaendert hat (dann Audit ``USER_2FA_RESET``)."""
    had_totp = bool(user.totp_enabled)
    had_pending = bool(is_unreadable(user.totp_pending_secret) or (user.totp_pending_secret or "").strip())
    if not had_totp and not had_pending:
        return False
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_pending_secret = None
    await db.flush()
    await write_audit(db, "USER_2FA_RESET", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "had_totp": had_totp, "had_pending": had_pending})
    return True


async def _remove_passkeys(db: AsyncSession, admin: User, user: User) -> int:
    """Alle Passkeys des Nutzers loeschen (``webauthn_user_handle`` bleibt, F3 E10); Audit ``USER_PASSKEYS_RESET``."""
    creds = await _list_user_credentials(db, user.id)
    if not creds:
        return 0
    names = [(c.name or "")[:100] for c in creds][:20]
    await db.execute(sql_delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    await db.flush()
    await write_audit(db, "USER_PASSKEYS_RESET", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "deleted": len(creds), "names": names})
    return len(creds)


@router.post("/users/{user_id}/reset-2fa")
async def reset_user_2fa(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """2FA eines Nutzers zuruecksetzen (Lockout-Recovery, auch bei unlesbarem TOTP-Geheimnis). Admin only."""
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail=OWN_2FA_DETAIL)
    if not await _reset_totp(db, admin, user):
        return {"message": "2FA war nicht aktiv", "changed": False, "user": await _user_to_dict(user, db)}
    return {"message": f"2FA für '{user.username}' zurückgesetzt", "changed": True,
            "user": await _user_to_dict(user, db)}


@router.delete("/users/{user_id}/webauthn-credentials")
async def delete_user_passkeys(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Alle Passkeys eines Nutzers entfernen (Lockout-Recovery). Admin only."""
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail=OWN_PASSKEYS_DETAIL)
    n = await _remove_passkeys(db, admin, user)
    return {"message": f"{n} Passkey(s) von '{user.username}' entfernt", "deleted": n}


@router.get("/users/{user_id}/access-summary")
async def get_user_access_summary(
    db: DbRead,
    user_id: int,
    admin: User = Depends(get_admin_user),
):
    """Zaehler der Zugaenge eines Nutzers (Panel-Tokens, DynDNS-Tokens, Webhooks, ausstehende Zustellungen) [S9]."""
    user = await _get_user_or_404(db, user_id)
    summary = await access_revocation.access_summary(db, user.id)
    summary["passkeys"] = len(await _list_user_credentials(db, user.id))
    summary["totp_enabled"] = bool(user.totp_enabled)
    return {"user_id": user.id, **summary}


@router.post("/users/{user_id}/revoke-access")
async def revoke_user_access(
    db: DbWrite,
    user_id: int,
    data: Optional[RevokeAccessBody] = Body(default=None),
    admin: User = Depends(get_admin_session_user),
):
    """"Alle Zugaenge widerrufen" (Kontouebernahme, [S9]): Panel-Tokens (endgueltig), DynDNS-Tokens, Webhooks
    (+ ausstehende Zustellungen verworfen); optional 2FA und Passkeys zuruecksetzen. Das Passwort bleibt
    unveraendert (dafuer Zufallspasswort/Reset-Link). Audit ``USER_ACCESS_REVOKE``.
    """
    opts = data or RevokeAccessBody()
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail=OWN_ACCESS_DETAIL)
    if not any((opts.panel_tokens, opts.dyndns_tokens, opts.webhooks, opts.reset_2fa, opts.remove_passkeys)):
        raise HTTPException(status_code=400, detail="Nichts zum Widerrufen ausgewählt")
    revoked = await access_revocation.revoke_all(
        db, user.id, reason="admin_revoke_access",
        panel_tokens=opts.panel_tokens, dyndns_tokens=opts.dyndns_tokens, webhooks=opts.webhooks,
    )
    totp_reset = await _reset_totp(db, admin, user) if opts.reset_2fa else False
    passkeys_removed = await _remove_passkeys(db, admin, user) if opts.remove_passkeys else 0
    await write_audit(db, "USER_ACCESS_REVOKE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "revoked": revoked,
                               "totp_reset": totp_reset, "passkeys_removed": passkeys_removed})
    summary = await access_revocation.access_summary(db, user.id)
    return {
        "message": f"Zugänge von '{user.username}' widerrufen",
        "revoked": revoked,
        "totp_reset": totp_reset,
        "passkeys_removed": passkeys_removed,
        "summary": summary,
        "user": await _user_to_dict(user, db),
    }


@router.post("/users/{user_id}/convert-to-local")
async def convert_user_to_local(
    db: DbWrite,
    user_id: int,
    request: Request,
    data: Optional[ConvertToLocalBody] = Body(default=None),
    admin: User = Depends(get_admin_session_user),
):
    """Externes Konto (OIDC/LDAP) in ein lokales umwandeln: Zufallspasswort (Einmalanzeige), externe ID entfernt.

    Immer mit Step-up des Admins (``step_up``: Passwort + ggf. TOTP bzw. frische Anmeldung bei externen Admins,
    Plan [S8]). Audit ``USER_CONVERT_LOCAL`` (ohne Passwort).
    """
    opts = data or ConvertToLocalBody()
    user = await _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Das eigene Konto kann nicht umgewandelt werden")
    if user_guard.is_local_account(user):
        raise HTTPException(status_code=400, detail="Das Konto ist bereits ein lokales Konto")
    step_up = await verify_step_up(db, admin, request, opts.step_up)

    previous_source, previous_issuer = user.auth_source, user.external_issuer
    new_password = generate_random_password(16)
    user.hashed_password = hash_password(new_password)
    user.auth_source = "local"
    user.external_issuer = None
    user.external_id = None
    user.must_change_password = bool(opts.must_change_password)
    await db.flush()
    logger.info("Admin '%s' hat das Konto '%s' (id=%s) in ein lokales umgewandelt", admin.username, user.username,
                user.id)
    await write_audit(db, "USER_CONVERT_LOCAL", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "previous_source": previous_source,
                               "previous_issuer": previous_issuer,
                               "must_change_password": bool(user.must_change_password), "step_up": step_up})
    return {
        "message": f"Konto '{user.username}' ist jetzt ein lokales Konto",
        "username": user.username,
        "new_password": new_password,  # nur dieses eine Mal
        "hint": "Bitte unverzüglich an den Nutzer weitergeben – das Passwort wird nicht erneut angezeigt.",
        "must_change_password": bool(user.must_change_password),
    }


# ========================
# Zone Access Management (Admin only)
# ========================
@router.put("/users/{user_id}/zones")
async def update_user_zones(
    db: DbWrite,
    user_id: int,
    data: ZoneAccessUpdate,
    admin: User = Depends(get_admin_session_user),
):
    """Set which zones a user can manage. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    # Alle bisherigen Zuordnungen loeschen
    await db.execute(sql_delete(UserZoneAccess).where(UserZoneAccess.user_id == user_id))

    def _perm_for(zone_name_norm: str) -> str:
        if not data.zone_permissions:
            return "manage"
        for k, v in data.zone_permissions.items():
            kn = k.strip().lower()
            if not kn.endswith('.'):
                kn += '.'
            if kn == zone_name_norm:
                p = (v or "manage").strip().lower()
                return p if p in ("read", "manage") else "manage"
        return "manage"

    for zone in data.zones:
        zone_name = zone.strip().lower()
        if not zone_name.endswith('.'):
            zone_name += '.'
        perm = _perm_for(zone_name)
        db.add(UserZoneAccess(user_id=user_id, zone_name=zone_name, permission=perm))

    await db.flush()
    logger.info(f"Zone access for '{user.username}' updated: {data.zones}")
    await write_audit(db, "ZONE_ACCESS_UPDATE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "zones": data.zones,
                               "zone_permissions": data.zone_permissions or {}})
    return {"message": f"Zonen fuer '{user.username}' aktualisiert", "zones": data.zones}


@router.get("/users/{user_id}/zones")
async def get_user_zones(
    db: DbRead,
    user_id: int,
    admin: User = Depends(get_admin_user),
):
    """Get zones assigned to a user. Admin only."""
    result = await db.execute(
        select(UserZoneAccess.zone_name, UserZoneAccess.permission).where(
            UserZoneAccess.user_id == user_id
        )
    )
    rows = result.all()
    zones = [row[0] for row in rows]
    zone_permissions = {row[0]: (row[1] or "manage") for row in rows}
    return {"user_id": user_id, "zones": zones, "zone_permissions": zone_permissions}


# ========================
# 2FA (TOTP) – Einstellungen
# ========================
class TotpEnableBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=12, description="Aktueller TOTP-Code aus der App")


class TotpDisableBody(BaseModel):
    # Pflicht nur fuer lokale Konten; externe Konten haben kein lokales Passwort (F10 3.2.5)
    password: Optional[str] = Field(None, max_length=128)
    code: str = Field(..., min_length=4, max_length=12)


@router.get("/me/totp/status")
async def totp_status(current_user: User = Depends(get_session_user)):
    """Ob 2FA aktiv ist und ob ein ausstehendes Setup (scan QR) laeuft."""
    pending = bool(
        (getattr(current_user, "totp_pending_secret", None) or "").strip()
        and not (getattr(current_user, "totp_enabled", False))
    )
    enabled = bool(getattr(current_user, "totp_enabled", False))
    return {
        "totp_enabled": enabled,
        "totp_pending": pending,
        "totp_unreadable": enabled and _totp_secret_state(current_user) == "unreadable",
    }


@router.post("/me/totp/begin")
async def totp_begin(
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Startet 2FA-Einrichtung: neues Geheimnis, Secret + otpauth-URI für Authenticator-App."""
    if getattr(current_user, "totp_enabled", False):
        raise HTTPException(status_code=400, detail="2FA ist bereits aktiv – zuerst deaktivieren")
    sec = pyotp.random_base32()
    current_user.totp_pending_secret = sec
    await db.flush()
    t = pyotp.totp.TOTP(sec)
    # Klarer Label-Text für Authenticator-Apps; Sonderzeichen/Zeilenumbruch vermeiden
    iss = (app_settings.APP_NAME or "PDNS Manager").strip()[:64]
    uname = (current_user.username or "user").strip()[:200]
    uri = t.provisioning_uri(name=uname, issuer_name=iss)
    return {
        "secret": sec,
        "provisioning_uri": uri,
    }


@router.post("/me/totp/enable")
async def totp_enable(
    db: DbWrite,
    data: TotpEnableBody,
    current_user: User = Depends(get_session_user),
):
    """Bestaetigt das Setup: pending-Secret wird aktiv, 2FA an."""
    ps = (getattr(current_user, "totp_pending_secret", None) or "").strip()
    if not ps:
        raise HTTPException(
            status_code=400,
            detail="Zuerst /me/totp/begin aufrufen",
        )
    if not totp_verify_once(current_user.id, ps, data.code):
        raise HTTPException(status_code=400, detail="Falscher TOTP-Code")
    current_user.totp_secret = ps
    current_user.totp_pending_secret = None
    current_user.totp_enabled = True
    await db.flush()
    await write_audit(db, "TOTP_ENABLE", "user", current_user.username, user_id=current_user.id)
    return {"message": "2FA aktiviert", "user": await _user_to_dict(current_user, db)}


@router.post("/me/totp/disable")
async def totp_disable(
    db: DbWrite,
    data: TotpDisableBody,
    current_user: User = Depends(get_session_user),
):
    """2FA ausschalten: Passwort (nur lokale Konten) + gueltiger TOTP."""
    if not _is_external(current_user) and (
            not data.password or not verify_password(data.password, current_user.hashed_password)):
        raise HTTPException(status_code=400, detail="Passwort ist falsch")
    if not getattr(current_user, "totp_enabled", False):
        return {"message": "2FA war nicht aktiv", "user": await _user_to_dict(current_user, db)}
    if _totp_secret_state(current_user) == "unreadable":
        raise HTTPException(status_code=409, detail=TOTP_UNREADABLE_DISABLE)
    sec = (getattr(current_user, "totp_secret", None) or "").strip()
    if not sec or not totp_verify_once(current_user.id, sec, data.code):
        raise HTTPException(status_code=400, detail="Falscher TOTP-Code")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_pending_secret = None
    await db.flush()
    await write_audit(db, "TOTP_DISABLE", "user", current_user.username, user_id=current_user.id)
    return {"message": "2FA deaktiviert", "user": await _user_to_dict(current_user, db)}


# ========================
# WebAuthn / Passkeys (passwortlose Anmeldung)
# ========================
class WebAuthnRegisterComplete(BaseModel):
    name: str = Field(default="Passkey", min_length=1, max_length=100)
    challenge_token: str = Field(..., min_length=20)
    credential: dict


class WebAuthnLoginComplete(BaseModel):
    challenge_token: str = Field(..., min_length=20)
    credential: dict


async def _list_user_credentials(db: AsyncSession, user_id: int) -> list[WebAuthnCredential]:
    result = await db.execute(
        select(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == user_id)
        .order_by(WebAuthnCredential.created_at)
    )
    return list(result.scalars().all())


def _credential_to_dict(c: WebAuthnCredential) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "created_at": iso_utc(c.created_at),
        "last_used_at": iso_utc(c.last_used_at),
    }


@router.get("/me/webauthn/credentials")
async def list_passkeys(
    db: DbRead,
    current_user: User = Depends(get_session_user),
):
    """Eigene registrierte Passkeys auflisten."""
    rows = await _list_user_credentials(db, current_user.id)
    return {"credentials": [_credential_to_dict(c) for c in rows]}


@router.post("/me/webauthn/register/begin")
async def webauthn_register_begin(
    db: DbWrite,
    request: Request,
    current_user: User = Depends(get_session_user),
):
    """Startet die Passkey-Registrierung: liefert Creation-Options + Challenge-Token (nur lokale Konten)."""
    if _is_external(current_user):
        raise HTTPException(status_code=400, detail=EXTERNAL_PASSKEY_REGISTER_DETAIL)
    from app.services import webauthn_service as wa

    existing = await _list_user_credentials(db, current_user.id)
    options_json, challenge_b64 = wa.build_registration_options(request, current_user, existing)
    # ensure_user_handle hat den User ggf. mutiert -> persistieren
    await db.flush()
    token = create_webauthn_challenge_token(
        challenge_b64, purpose=TOKEN_TYPE_WEBAUTHN_REG, user_id=current_user.id
    )
    return {"options": json.loads(options_json), "challenge_token": token}


@router.post("/me/webauthn/register/complete", status_code=201)
async def webauthn_register_complete(
    db: DbWrite,
    data: WebAuthnRegisterComplete,
    request: Request,
    current_user: User = Depends(get_session_user),
):
    """Schließt die Passkey-Registrierung ab: verifiziert Attestation und speichert den Public-Key."""
    if _is_external(current_user):
        raise HTTPException(status_code=400, detail=EXTERNAL_PASSKEY_REGISTER_DETAIL)
    from app.services import webauthn_service as wa

    payload = decode_webauthn_challenge_token(data.challenge_token, purpose=TOKEN_TYPE_WEBAUTHN_REG)
    if not payload or str(payload.get("sub")) != str(current_user.id):
        raise HTTPException(status_code=400, detail="Ungültiges oder abgelaufenes Challenge-Token")

    try:
        result = wa.verify_registration(request, data.credential, payload["chal"])
    except wa.WebAuthnError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Schon registriert? (z. B. doppelter Submit)
    dup = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == result["credential_id"])
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Dieser Passkey ist bereits registriert")

    cred = WebAuthnCredential(
        user_id=current_user.id,
        name=(data.name or "Passkey").strip()[:100],
        credential_id=result["credential_id"],
        public_key=result["public_key"],
        sign_count=result["sign_count"],
        transports=result["transports"],
        aaguid=result["aaguid"],
    )
    db.add(cred)
    await db.flush()
    logger.info("Passkey '%s' registered for user '%s'", cred.name, current_user.username)
    await write_audit(db, "PASSKEY_ADD", "user", current_user.username, user_id=current_user.id,
                      details={"credential_id": cred.id, "name": cred.name})
    return {"message": "Passkey hinzugefügt", "credential": _credential_to_dict(cred)}


@router.delete("/me/webauthn/credentials/{cred_id}")
async def delete_passkey(
    db: DbWrite,
    cred_id: int,
    current_user: User = Depends(get_session_user),
):
    """Einen eigenen Passkey entfernen."""
    result = await db.execute(
        select(WebAuthnCredential).where(
            WebAuthnCredential.id == cred_id,
            WebAuthnCredential.user_id == current_user.id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Passkey nicht gefunden")
    await write_audit(db, "PASSKEY_DELETE", "user", current_user.username, user_id=current_user.id,
                      details={"credential_id": cred.id, "name": cred.name})
    await db.delete(cred)
    await db.flush()
    return {"message": "Passkey entfernt"}


@router.post("/webauthn/login/begin")
async def webauthn_login_begin(
    db: DbWrite,
    request: Request,
):
    """Öffentlich: startet die Passkey-Anmeldung (usernameless / discoverable).

    Liefert Request-Options + Challenge-Token. Aus Datenschutzgründen wird keine
    allow_credentials-Liste ausgegeben (keine Nutzer-/Geräte-Enumeration)."""
    from app.services import webauthn_service as wa

    options_json, challenge_b64 = wa.build_authentication_options(request, allow_creds=None)
    token = create_webauthn_challenge_token(challenge_b64, purpose=TOKEN_TYPE_WEBAUTHN_AUTH)
    return {"options": json.loads(options_json), "challenge_token": token}


@router.post("/webauthn/login/complete")
async def webauthn_login_complete(
    db: DbWrite,
    data: WebAuthnLoginComplete,
    request: Request,
):
    """Öffentlich: schließt die Passkey-Anmeldung ab und setzt das Session-Cookie."""
    from app.services import webauthn_service as wa

    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip):
        raise _rate_limited("passkey")

    def _fail(username: Optional[str] = None) -> None:
        record_failed_login(client_ip, username)
        prom.record_login("passkey", "failure")

    payload = decode_webauthn_challenge_token(data.challenge_token, purpose=TOKEN_TYPE_WEBAUTHN_AUTH)
    if not payload:
        _fail()
        raise HTTPException(status_code=400, detail="Ungültiges oder abgelaufenes Challenge-Token")

    cred_id = wa.extract_credential_id(data.credential)
    if not cred_id:
        _fail()
        raise HTTPException(status_code=400, detail="Ungültige Passkey-Antwort")

    result = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        _fail()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Passkey nicht erkannt")

    result = await db.execute(select(User).where(User.id == cred.user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        _fail(user.username if user else None)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Konto ist deaktiviert")
    if is_login_rate_limited(client_ip, user.username):
        raise _rate_limited("passkey")
    # F10 3.2.3: Passkeys nur fuer lokale Konten (sie wuerden eine Sperre beim Anmeldedienst umgehen); bei
    # abgeschalteter lokaler Anmeldung nur fuer Admins (Notfallzugang)
    if _is_external(user):
        _fail()
        await write_audit(db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
                          details={"ip": client_ip, "method": "passkey", "reason": "external_account"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EXTERNAL_PASSKEY_LOGIN_DETAIL)
    if user.role != "admin" and not await _local_login_enabled(db):  # static-ok: role-admin (Ziel-Konto)
        prom.record_login("passkey", "denied")
        await write_audit(db, "LOGIN_FAILED", "user", user.username, user_id=user.id, status="error",
                          details={"ip": client_ip, "method": "passkey", "reason": "local_login_disabled"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCAL_LOGIN_DISABLED_DETAIL)

    try:
        new_sign_count = wa.verify_authentication(request, data.credential, payload["chal"], cred)
    except wa.WebAuthnError as exc:
        _fail(user.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    cred.sign_count = new_sign_count
    cred.last_used_at = datetime.now(timezone.utc)
    return await _complete_login(db, user, request, method="passkey")
