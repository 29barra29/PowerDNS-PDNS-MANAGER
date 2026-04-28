"""API routes for authentication and user management."""
import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request, status, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.database import get_db
import pyotp
from app.core.auth import (
    hash_password, verify_password, create_access_token,
    create_password_reset_token, decode_password_reset_token,
    create_two_factor_pending_token, decode_two_factor_pending_token,
    get_current_user, get_admin_user,
    generate_random_password, MIN_PASSWORD_LENGTH,
)
from app.models.models import User, UserZoneAccess

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ========================
# Schemas
# ========================
# Anmerkung: Bewusst KEIN LoginResponse-Schema mit access_token mehr exportiert –
# der Token lebt ausschließlich im HttpOnly-Cookie, das Frontend greift nie auf das JWT zu.


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, max_length=255)
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern="^(admin|user)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=MIN_PASSWORD_LENGTH, max_length=128)


class ProfileUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[str] = None
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
    email: Optional[str] = None
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


async def _user_to_dict(user: User, db: AsyncSession) -> dict:
    result = await db.execute(
        select(UserZoneAccess.zone_name, UserZoneAccess.permission).where(
            UserZoneAccess.user_id == user.id
        )
    )
    rows = result.all()
    zones = [row[0] for row in rows]
    zone_permissions = {row[0]: (row[1] or "manage") for row in rows}

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "zones": zones,
        "zone_permissions": zone_permissions,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "phone": getattr(user, "phone", None),
        "company": getattr(user, "company", None),
        "street": getattr(user, "street", None),
        "postal_code": getattr(user, "postal_code", None),
        "city": getattr(user, "city", None),
        "country": getattr(user, "country", None),
        "date_of_birth": user.date_of_birth.isoformat()[:10] if getattr(user, "date_of_birth", None) else None,
        "preferred_language": getattr(user, "preferred_language", None) or None,
        "totp_enabled": bool(getattr(user, "totp_enabled", False)),
    }


# ========================
# Auth Endpoints
# ========================
class TwoFactorComplete(BaseModel):
    """Abschluss der Anmeldung nach TOTP – Token aus /login bei need_two_factor."""
    two_factor_token: str = Field(..., min_length=20)
    totp_code: str = Field(..., min_length=4, max_length=12)


@router.post("/login")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    captcha_token: Optional[str] = Form(default=None, description="Captcha-Token (nur wenn aktiviert)"),
    totp_code: Optional[str] = Form(default=None, description="6-stelliger TOTP-Code, falls 2FA aktiv"),
    db: AsyncSession = Depends(get_db),
):
    """Login with username and password. Setzt HttpOnly-Cookie (kein Token im localStorage)."""
    from app.core.login_rate_limit import (
        is_login_rate_limited,
        record_failed_login,
        clear_login_fails,
    )

    client_ip = request.client.host if request.client else "unknown"
    if is_login_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen.",
        )

    # Captcha vor dem Login pruefen, damit Bots keinen Brute-Force gegen DB+Hash starten koennen.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, captcha_token, request.client.host if request.client else None)

    result = await db.execute(
        select(User).where(User.username == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher Benutzername oder Passwort",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto ist deaktiviert",
        )

    # --- 2FA (TOTP) ------------------------------------------------------------
    if getattr(user, "totp_enabled", False):
        sec = (user.totp_secret or "").strip()
        if not sec:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="2FA fehlerhaft konfiguriert (kein Geheimnis) – bitte Admin kontaktieren",
            )
        code = (totp_code or "").strip().replace(" ", "")
        if not code:
            pending = create_two_factor_pending_token(user.id)
            return JSONResponse(
                status_code=200,
                content={
                    "need_two_factor": True,
                    "two_factor_token": pending,
                },
            )
        if not pyotp.TOTP(sec).verify(code, valid_window=1):
            record_failed_login(client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Falscher TOTP-Code",
            )

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    clear_login_fails(client_ip)

    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    user_dict = await _user_to_dict(user, db)

    # Token wird NUR per HttpOnly-Cookie gesetzt – nicht mehr im JSON-Body,
    # damit er nicht in Browser-DevTools/Logs sichtbar wird oder versehentlich
    # vom Frontend in localStorage etc. gespeichert werden kann.
    response = JSONResponse(content={"user": user_dict})
    response.set_cookie(
        key=app_settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=app_settings.AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=app_settings.AUTH_COOKIE_SECURE,
        samesite=app_settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return response


@router.post("/login/2fa")
async def login_two_factor(
    data: TwoFactorComplete,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """TOTP-Code + pending-Token aus /login, wenn 2FA aktiv. Setzt Session-Cookie."""
    from app.core.login_rate_limit import (
        is_login_rate_limited,
        record_failed_login,
        clear_login_fails,
    )

    client_ip = request.client.host if request.client else "unknown"
    if is_login_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen.",
        )
    uid = decode_two_factor_pending_token(data.two_factor_token)
    if not uid:
        record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder abgelaufener Zweitschritt-Token",
        )
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not getattr(user, "totp_enabled", False):
        record_failed_login(client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht anmeldbar")
    sec = (user.totp_secret or "").strip()
    if not sec or not pyotp.TOTP(sec).verify((data.totp_code or "").strip().replace(" ", ""), valid_window=1):
        record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher TOTP-Code",
        )
    user.last_login = datetime.now(timezone.utc)
    await db.flush()
    clear_login_fails(client_ip)
    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    user_dict = await _user_to_dict(user, db)
    response = JSONResponse(content={"user": user_dict})
    response.set_cookie(
        key=app_settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=app_settings.AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=app_settings.AUTH_COOKIE_SECURE,
        samesite=app_settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return response


@router.post("/logout")
async def logout():
    """Abmelden: Auth-Cookie löschen (Frontend speichert keinen Token mehr)."""
    response = JSONResponse(content={"message": "Abgemeldet"})
    response.delete_cookie(key=app_settings.AUTH_COOKIE_NAME, path="/")
    return response


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_public(
    data: RegisterPublic,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user (only when registration is enabled in settings)."""
    if not await _get_auth_setting(db, "registration_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung ist deaktiviert",
        )

    # Captcha gegen Spam-/Bot-Registrierungen.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, data.captcha_token, request.client.host if request.client else None)

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
            send_email(smtp, user.email, subject, body_html, body_text)
            logger.info("Welcome email sent to %s", user.email)
    except Exception as exc:  # noqa: BLE001 - Hintergrund-Task soll nie crashen
        logger.warning("Failed to send welcome email (user_id=%s): %s", user_id, exc)


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email (only when forgot_password is enabled)."""
    if not await _get_auth_setting(db, "forgot_password_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Passwort vergessen ist deaktiviert",
        )

    # Captcha schuetzt davor, dass jemand massenhaft Reset-Mails an fremde Adressen triggert.
    from app.services.captcha import verify_or_raise as _verify_captcha
    await _verify_captcha(db, data.captcha_token, request.client.host if request.client else None)

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

    token = create_password_reset_token(user.id)
    from app.models.models import SystemSetting
    result = await db.execute(select(SystemSetting.value).where(SystemSetting.key == "app_base_url"))
    base_url_val = result.scalar_one_or_none()
    base_url = (base_url_val or "").strip() if base_url_val else ""
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
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
        send_email(smtp, user.email, subject, body_html, body_text)
    except Exception as e:
        logger.exception("Failed to send password reset email: %s", e)
    return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set new password using the token from the email."""
    user_id = decode_password_reset_token(data.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link. Bitte fordere einen neuen an.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Link.")

    user.hashed_password = hash_password(data.new_password)
    await db.flush()
    logger.info(f"Password reset for user id={user_id}")
    return {"message": "Passwort wurde geändert. Du kannst dich jetzt anmelden."}


@router.get("/me")
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user info."""
    return await _user_to_dict(current_user, db)


@router.put("/me")
async def update_profile(
    data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update own profile (username, email, display_name)."""
    if data.username is not None and data.username != current_user.username:
        # Check if new username is already taken
        result = await db.execute(select(User).where(User.username == data.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Benutzername ist bereits vergeben")
        current_user.username = data.username
    
    if data.email is not None:
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
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change own password."""
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")

    current_user.hashed_password = hash_password(data.new_password)
    await db.flush()
    return {"message": "Passwort geaendert"}


# ========================
# User Management (Admin only)
# ========================
@router.get("/users")
async def list_users(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users with their zone assignments. Admin only."""
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return {"users": [await _user_to_dict(u, db) for u in users]}


@router.post("/users", status_code=201)
async def create_user(
    data: UserCreate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
    return await _user_to_dict(user, db)


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    data: UserUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    if data.email is not None:
        user.email = data.email
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        if user.role == "admin" and data.role != "admin":
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
    return await _user_to_dict(user, db)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user and their zone assignments. Admin only."""
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst loeschen")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    # Remove zone access entries
    await db.execute(sql_delete(UserZoneAccess).where(UserZoneAccess.user_id == user_id))
    await db.delete(user)
    await db.flush()
    return {"message": f"Benutzer '{user.username}' geloescht"}


@router.put("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
    user_id: int,
    data: ZoneAccessUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
    return {"message": f"Zonen fuer '{user.username}' aktualisiert", "zones": data.zones}


@router.get("/users/{user_id}/zones")
async def get_user_zones(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
async def totp_status(current_user: User = Depends(get_current_user)):
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
    data: TotpEnableBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bestaetigt das Setup: pending-Secret wird aktiv, 2FA an."""
    ps = (getattr(current_user, "totp_pending_secret", None) or "").strip()
    if not ps:
        raise HTTPException(
            status_code=400,
            detail="Zuerst /me/totp/begin aufrufen",
        )
    if not pyotp.TOTP(ps).verify((data.code or "").strip().replace(" ", ""), valid_window=1):
        raise HTTPException(status_code=400, detail="Falscher TOTP-Code")
    current_user.totp_secret = ps
    current_user.totp_pending_secret = None
    current_user.totp_enabled = True
    await db.flush()
    return {"message": "2FA aktiviert", "user": await _user_to_dict(current_user, db)}


@router.post("/me/totp/disable")
async def totp_disable(
    data: TotpDisableBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """2FA ausschalten: Passwort + gueltiger TOTP."""
    if not verify_password(data.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Passwort ist falsch")
    if not getattr(current_user, "totp_enabled", False):
        return {"message": "2FA war nicht aktiv", "user": await _user_to_dict(current_user, db)}
    sec = (getattr(current_user, "totp_secret", None) or "").strip()
    if not sec or not pyotp.TOTP(sec).verify((data.code or "").strip().replace(" ", ""), valid_window=1):
        raise HTTPException(status_code=400, detail="Falscher TOTP-Code")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_pending_secret = None
    await db.flush()
    return {"message": "2FA deaktiviert", "user": await _user_to_dict(current_user, db)}


# ========================
# Panel-API-Token (Bearer wie Session, für Skripte)
# ========================
class PanelTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


@router.get("/me/panel-tokens")
async def list_panel_tokens(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import panel_token as ptk

    rows = await ptk.list_tokens(db, current_user.id)
    return {
        "tokens": [
            {
                "id": t.id,
                "name": t.name,
                "token_prefix": t.token_prefix,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                "is_active": t.is_active,
            }
            for t in rows
        ]
    }


@router.post("/me/panel-tokens", status_code=201)
async def create_panel_token(
    data: PanelTokenCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import panel_token as ptk

    row, plain = await ptk.create_token(db, current_user.id, data.name)
    return {
        "token": {
            "id": row.id,
            "name": row.name,
            "token_prefix": row.token_prefix,
        },
        "plaintext_token": plain,
        "warning": "Dieser Token wird nur einmal angezeigt – bitte sicher speichern.",
    }


@router.delete("/me/panel-tokens/{token_id}")
async def delete_panel_token(
    token_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import panel_token as ptk

    ok = await ptk.delete_token(db, current_user.id, token_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    return {"message": "Token widerrufen"}


# ========================
# Webhooks
# ========================
class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=5, max_length=1024)
    events: list[str] = Field(default_factory=lambda: ["*"])


class WebhookUpdateBody(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    events: Optional[list[str]] = None
    is_active: Optional[bool] = None
    rotate_secret: bool = False


@router.get("/me/webhooks")
async def list_my_webhooks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import webhook_service as wh

    rows = await wh.list_webhooks(db, current_user.id)
    return {
        "webhooks": [
            {
                "id": w.id,
                "name": w.name,
                "url": w.url,
                "events": w.events or ["*"],
                "is_active": w.is_active,
                "has_secret": True,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in rows
        ]
    }


@router.post("/me/webhooks", status_code=201)
async def create_my_webhook(
    data: WebhookCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import webhook_service as wh

    try:
        w = await wh.create_webhook(db, current_user.id, data.name, data.url, data.events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "webhook": {
            "id": w.id,
            "name": w.name,
            "url": w.url,
            "events": w.events,
            "is_active": w.is_active,
        },
        "secret": w.secret,
        "warning": "Das Shared Secret für HMAC (Header X-DNS-Manager-Signature) – nur in dieser Antwort; bei Verlust: rotate_secret im PUT nutzen",
    }


@router.put("/me/webhooks/{webhook_id}")
async def update_my_webhook(
    webhook_id: int,
    data: WebhookUpdateBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import webhook_service as wh

    try:
        w = await wh.update_webhook(
            db,
            current_user.id,
            webhook_id,
            name=data.name,
            url=data.url,
            events=data.events,
            is_active=data.is_active,
            new_secret=data.rotate_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not w:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    out: dict = {
        "id": w.id,
        "name": w.name,
        "url": w.url,
        "events": w.events,
        "is_active": w.is_active,
    }
    if data.rotate_secret:
        out["new_secret"] = w.secret
    return out


@router.delete("/me/webhooks/{webhook_id}")
async def delete_my_webhook(
    webhook_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import webhook_service as wh

    if not await wh.delete_webhook(db, current_user.id, webhook_id):
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    return {"message": "Webhook gelöscht"}
