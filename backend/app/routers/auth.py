"""API-Routen fuer Anmeldung, eigenes Konto und Benutzerverwaltung.

Seit 3.0 liegen die Panel-Token-Verwaltung in ``routers/panel_tokens.py`` und die Webhook-Verwaltung in
``routers/webhooks.py`` (Pfade unveraendert). Der Login-Abschluss (Cookie, Audit ``LOGIN``, Metrik,
Fehlzaehler) laeuft ueber ``services.login_session`` (Aliase ``_user_to_dict``, ``_set_session_cookie``,
``_complete_login``). Schreibende Handler nutzen ``DbWrite`` (Commit vor der Antwort, Bauplan B.7).
"""
import json
import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request, status, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator, EmailStr, BeforeValidator, AfterValidator
from typing import Optional, Annotated
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as prom
from app.core.timeutil import iso_utc
from app.core.config import settings as app_settings
from app.core.database import DbRead, DbWrite
from app.core.client_ip import get_client_ip
from app.core.login_rate_limit import is_login_rate_limited, record_failed_login
import pyotp
from starlette.concurrency import run_in_threadpool
from app.services import login_session
from app.services.audit import write_audit
from app.core.auth import (
    get_session_user, get_admin_session_user, totp_verify_once, decode_password_reset_payload, password_version,
    hash_password, verify_password, create_access_token,
    create_password_reset_token, decode_password_reset_token,
    create_two_factor_pending_token, decode_two_factor_pending_token,
    create_webauthn_challenge_token, decode_webauthn_challenge_token,
    get_current_user, get_admin_user,
    generate_random_password, MIN_PASSWORD_LENGTH,
    TOKEN_TYPE_WEBAUTHN_REG, TOKEN_TYPE_WEBAUTHN_AUTH,
)
from app.models.models import User, UserZoneAccess, WebAuthnCredential, PanelToken, Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Gleicher Text fuer IP- und Benutzer-Sperre (keine Aussage, welche greift)
RATE_LIMIT_DETAIL = "Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen."

# Aliase (Bauplan B.4): Implementierung in services/login_session.py
_user_to_dict = login_session.user_to_dict
_set_session_cookie = login_session.set_session_cookie
_complete_login = login_session.complete_login


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


class UserUpdate(BaseModel):
    email: OptionalEmail = None
    display_name: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern="^(admin|user)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=MIN_PASSWORD_LENGTH, max_length=128)


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

    @field_validator("phone")
    @classmethod
    def phone_at_least_one_digit(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return v or None
        if not any(c.isdigit() for c in v):
            raise ValueError("Telefon muss mindestens eine Ziffer enthalten")
        return v.strip()

    @field_validator("postal_code", "city", "country")
    @classmethod
    def strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return None
        return v.strip()


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
    """Abschluss der Anmeldung nach TOTP – Token aus /login bei need_two_factor."""
    two_factor_token: str = Field(..., min_length=20)
    totp_code: str = Field(..., min_length=4, max_length=12)


def _rate_limited(method: str) -> HTTPException:
    """429 mit identischem Text fuer IP- und Benutzer-Sperre (Metrik ``rate_limited``)."""
    prom.record_login(method, "rate_limited")
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_DETAIL)


@router.post("/login")
async def login(
    db: DbWrite,
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    captcha_token: Optional[str] = Form(default=None, description="Captcha-Token (nur wenn aktiviert)"),
    totp_code: Optional[str] = Form(default=None, description="6-stelliger TOTP-Code, falls 2FA aktiv"),
):
    """Anmeldung mit Benutzername und Passwort. Setzt das HttpOnly-Cookie (kein Token im Body).

    Drosselung je IP (/64) UND je Benutzername (B.3 [S2]): ein gesperrter Benutzername loest weder
    Captcha-Pruefung noch DB-Lookup noch Passwortvergleich aus.
    """
    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip, form_data.username):
        raise _rate_limited("password")

    # Captcha vor dem Login pruefen, damit Bots keinen Brute-Force gegen DB+Hash starten koennen.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, captcha_token, get_client_ip(request))

    result = await db.execute(
        select(User).where(User.username == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        record_failed_login(client_ip, form_data.username)
        prom.record_login("password", "failure")
        await write_audit(
            db, "LOGIN_FAILED", "user", form_data.username,
            user_id=user.id if user else None, status="error",
            details={"ip": client_ip, "reason": "bad_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher Benutzername oder Passwort",
        )

    if not user.is_active:
        prom.record_login("password", "denied")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto ist deaktiviert",
        )

    # --- 2FA (TOTP) ------------------------------------------------------------
    method = "password"
    if getattr(user, "totp_enabled", False):
        sec = (user.totp_secret or "").strip()
        if not sec:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="2FA fehlerhaft konfiguriert (kein Geheimnis) – bitte Admin kontaktieren",
            )
        code = (totp_code or "").strip().replace(" ", "")
        if not code:
            prom.record_login("password", "2fa_required")
            pending = create_two_factor_pending_token(user.id)
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
        method = "password+totp"

    # Erfolg: last_login, Benutzer-Zaehler des Paars loeschen, Audit LOGIN, Metrik, Cookie (B.4)
    return await _complete_login(db, user, request, method=method)


@router.post("/login/2fa")
async def login_two_factor(
    db: DbWrite,
    data: TwoFactorComplete,
    request: Request,
):
    """TOTP-Code + pending-Token aus /login, wenn 2FA aktiv. Setzt Session-Cookie."""
    client_ip = get_client_ip(request) or "unknown"
    if is_login_rate_limited(client_ip):
        raise _rate_limited("totp")
    uid = decode_two_factor_pending_token(data.two_factor_token)
    if not uid:
        record_failed_login(client_ip)
        prom.record_login("totp", "failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder abgelaufener Zweitschritt-Token",
        )
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not getattr(user, "totp_enabled", False):
        record_failed_login(client_ip)
        prom.record_login("totp", "failure")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht anmeldbar")
    # Benutzer-Zaehler: dieselbe Sperre wie beim Passwort-Login (kein TOTP-Raten ueber /login/2fa)
    if is_login_rate_limited(client_ip, user.username):
        raise _rate_limited("totp")
    sec = (user.totp_secret or "").strip()
    if not sec or not totp_verify_once(user.id, sec, data.totp_code):
        record_failed_login(client_ip, user.username)
        prom.record_login("totp", "failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher TOTP-Code",
        )
    return await _complete_login(db, user, request, method="password+totp")


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
    """Register a new user (only when registration is enabled in settings)."""
    if not await _get_auth_setting(db, "registration_enabled"):
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

    # Immer gleiche Antwort (keine Hinweise ob Konto existiert)
    if not user or not user.email:
        return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}

    token = create_password_reset_token(user.id, user.hashed_password)
    from app.models.models import SystemSetting
    result = await db.execute(select(SystemSetting.value).where(SystemSetting.key == "app_base_url"))
    base_url_val = result.scalar_one_or_none()
    base_url = (base_url_val or "").strip() if base_url_val else ""
    if not base_url:
        # Fallback NUR auf konfigurierte Werte, nie auf den Host-Header der Anfrage:
        # sonst koennte ein Angreifer dem Opfer per "Passwort vergessen" einen Reset-Link
        # auf seine eigene Domain zustellen lassen (Link-Poisoning -> Account-Uebernahme).
        first_origin = (app_settings.WEBAUTHN_ORIGIN or "").split(",")[0].strip()
        base_url = first_origin.rstrip("/") if first_origin else ""
    if not base_url:
        logger.error(
            "Passwort-Reset nicht versendet: keine App-Basis-URL konfiguriert. "
            "Bitte unter Einstellungen -> Profil -> Oeffentliche Basis-URL eintragen "
            "(oder WEBAUTHN_ORIGIN in der .env setzen)."
        )
        return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}
    reset_url = f"{base_url.rstrip('/')}/reset-password?token={token}"

    from app.services.email_service import get_smtp_settings, send_email
    from app.services.email_templates import pick_language, password_reset
    smtp = await get_smtp_settings(db)
    if not smtp.get("enabled") or not smtp.get("host"):
        logger.warning("SMTP not configured - cannot send password reset email")
        return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}

    # Sprache: erst Nutzer-Preferenz, sonst App-Default (DEFAULT_LANGUAGE), sonst en.
    lang = pick_language(user.preferred_language, app_settings.DEFAULT_LANGUAGE)
    subject, body_html, body_text = password_reset(
        lang,
        user.display_name or user.username,
        reset_url,
    )
    try:
        await run_in_threadpool(send_email, smtp, user.email, subject, body_html, body_text)
    except Exception as e:
        logger.exception("Failed to send password reset email: %s", e)
    return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}


@router.post("/reset-password")
async def reset_password(
    db: DbWrite,
    data: ResetPasswordRequest,
):
    """Set new password using the token from the email."""
    payload = decode_password_reset_payload(data.token)
    if not payload:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link. Bitte fordere einen neuen an.")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link.")
    # Einmal-Nutzung: Link passt nur zum Passwort-Hash, der beim Anfordern galt.
    if payload.get("pwv") != password_version(user.hashed_password):
        raise HTTPException(status_code=400, detail="Dieser Link wurde bereits verwendet. Bitte fordere einen neuen an.")

    user.hashed_password = hash_password(data.new_password)
    user.must_change_password = False  # F3 3.2.9: der Nutzer hat selbst ein neues Passwort gesetzt
    await db.flush()
    logger.info(f"Password reset for user id={user_id}")
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
    """Update own profile (username, email, display_name)."""
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
    
    if data.display_name is not None:
        current_user.display_name = data.display_name or None

    for attr in ("phone", "company", "street", "postal_code", "city", "country"):
        if getattr(data, attr, None) is not None:
            setattr(current_user, attr, getattr(data, attr) or None)
    if data.date_of_birth is not None:
        if data.date_of_birth:
            try:
                current_user.date_of_birth = date.fromisoformat(data.date_of_birth)
            except ValueError:
                current_user.date_of_birth = None
        else:
            current_user.date_of_birth = None
    if data.preferred_language is not None:
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
    """Change own password."""
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")

    current_user.hashed_password = hash_password(data.new_password)
    # Erzwungener Passwortwechsel ist damit erledigt (Gate in core.auth, F3 3.2.8)
    current_user.must_change_password = False
    await db.flush()
    await write_audit(db, "PASSWORD_CHANGE", "user", current_user.username, user_id=current_user.id)
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
    """List all users with their zone assignments. Admin only."""
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return {"users": [await _user_to_dict(u, db) for u in users]}


@router.post("/users", status_code=201)
async def create_user(
    db: DbWrite,
    data: UserCreate,
    admin: User = Depends(get_admin_session_user),
):
    """Create a new user. Admin only."""
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Benutzername existiert bereits")

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
        role=data.role,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    logger.info(f"User '{data.username}' created by admin '{admin.username}'")
    await write_audit(db, "USER_CREATE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "role": user.role, "email": user.email})
    return await _user_to_dict(user, db)


@router.put("/users/{user_id}")
async def update_user(
    db: DbWrite,
    user_id: int,
    data: UserUpdate,
    admin: User = Depends(get_admin_session_user),
):
    """Update a user. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    before = {"email": user.email, "display_name": user.display_name, "role": user.role, "is_active": user.is_active}
    if "email" in data.model_fields_set:
        user.email = data.email  # None = Adresse entfernen
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        # Rolle des ZIEL-Benutzers (letzter Admin), keine Rechtepruefung des Aufrufers
        if user.role == "admin" and data.role != "admin":  # static-ok: role-admin
            admin_count = await db.execute(
                select(func.count()).select_from(User).where(User.role == "admin")
            )
            if admin_count.scalar() <= 1:
                raise HTTPException(status_code=400, detail="Letzter Admin kann nicht herabgestuft werden")
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.hashed_password = hash_password(data.password)

    await db.flush()
    changed = {k: {"from": before[k], "to": getattr(user, k)} for k in before if before[k] != getattr(user, k)}
    if data.password is not None:
        changed["password"] = "set_by_admin"
    await write_audit(db, "USER_UPDATE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "changed": changed})
    return await _user_to_dict(user, db)


@router.delete("/users/{user_id}")
async def delete_user(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Delete a user and their zone assignments. Admin only."""
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst loeschen")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    # Alle benutzergebundenen Daten entfernen (keine FK-Constraints im Schema -> manuell).
    # Zustellungen (Outbox) VOR den Webhooks loeschen (F6 3.9).
    await db.execute(sql_delete(UserZoneAccess).where(UserZoneAccess.user_id == user_id))
    await db.execute(sql_delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id))
    await db.execute(sql_delete(PanelToken).where(PanelToken.user_id == user_id))
    res_deliveries = await db.execute(sql_delete(WebhookDelivery).where(WebhookDelivery.user_id == user_id))
    res_webhooks = await db.execute(sql_delete(Webhook).where(Webhook.user_id == user_id))
    await write_audit(db, "USER_DELETE", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id, "role": user.role,
                               "deleted_webhooks": int(res_webhooks.rowcount or 0),
                               "deleted_webhook_deliveries": int(res_deliveries.rowcount or 0)})
    await db.delete(user)
    await db.flush()
    return {"message": f"Benutzer '{user.username}' geloescht"}


@router.put("/users/{user_id}/reset-password")
async def reset_user_password(
    db: DbWrite,
    user_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Reset a user's password to a fresh random value. Admin only.

    Das neue Passwort wird genau einmal an den Admin zurückgegeben (für die Weitergabe an den
    Nutzer). Es wird nicht mehr auf den Benutzernamen gesetzt – das war bei öffentlich bekannten
    Benutzernamen ein Übernahmerisiko.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    new_password = generate_random_password(16)
    user.hashed_password = hash_password(new_password)
    await db.flush()
    logger.info(f"Admin '{admin.username}' reset password for user '{user.username}' (id={user.id})")
    await write_audit(db, "USER_PASSWORD_RESET", "user", user.username, user_id=admin.id,
                      details={"target_user_id": user.id})
    return {
        "message": f"Passwort für '{user.username}' wurde zurückgesetzt.",
        "username": user.username,
        "new_password": new_password,  # nur dieses eine Mal
        "hint": "Bitte unverzüglich an den Nutzer weitergeben – das Passwort wird nicht erneut angezeigt.",
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
    password: str
    code: str = Field(..., min_length=4, max_length=12)


@router.get("/me/totp/status")
async def totp_status(current_user: User = Depends(get_session_user)):
    """Ob 2FA aktiv ist und ob ein ausstehendes Setup (scan QR) laeuft."""
    pending = bool(
        (getattr(current_user, "totp_pending_secret", None) or "").strip()
        and not (getattr(current_user, "totp_enabled", False))
    )
    return {
        "totp_enabled": bool(getattr(current_user, "totp_enabled", False)),
        "totp_pending": pending,
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
    """2FA ausschalten: Passwort + gueltiger TOTP."""
    if not verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Passwort ist falsch")
    if not getattr(current_user, "totp_enabled", False):
        return {"message": "2FA war nicht aktiv", "user": await _user_to_dict(current_user, db)}
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
    """Startet die Passkey-Registrierung: liefert Creation-Options + Challenge-Token."""
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

    try:
        new_sign_count = wa.verify_authentication(request, data.credential, payload["chal"], cred)
    except wa.WebAuthnError as exc:
        _fail(user.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    cred.sign_count = new_sign_count
    cred.last_used_at = datetime.now(timezone.utc)
    return await _complete_login(db, user, request, method="passkey")
