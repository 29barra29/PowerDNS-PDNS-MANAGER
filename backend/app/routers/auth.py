"""API routes for authentication and user management."""
import logging
from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.auth import (
    hash_password, verify_password, create_access_token,
    create_password_reset_token, decode_password_reset_token,
    get_current_user, get_admin_user,
)
from app.models.models import User, UserZoneAccess

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ========================
# Schemas
# ========================
class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=4, max_length=100)
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=4)


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
    new_password: str = Field(..., min_length=4)


class ZoneAccessUpdate(BaseModel):
    zones: list[str] = Field(..., description="Liste der Zonen die der Benutzer verwalten darf")


class RegisterPublic(BaseModel):
    """Public registration (only when registration_enabled)."""
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=4, max_length=100)
    email: Optional[str] = None
    display_name: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    email: Optional[str] = Field(None, description="E-Mail des Kontos")
    username: Optional[str] = Field(None, description="Benutzername (Alternative zu E-Mail)")


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="Token aus der E-Mail")
    new_password: str = Field(..., min_length=4)


async def _get_auth_setting(db: AsyncSession, key: str) -> bool:
    from app.models.models import SystemSetting
    result = await db.execute(select(SystemSetting.value).where(SystemSetting.key == key))
    value = result.scalar_one_or_none()  # Einzelne Spalte => Skalar (str), nicht Row
    if value is None:
        return False
    return str(value).strip().lower() == "true"


async def _user_to_dict(user: User, db: AsyncSession) -> dict:
    # Zugewiesene Zonen laden
    result = await db.execute(
        select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == user.id)
    )
    zones = [row[0] for row in result.all()]

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "zones": zones,
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
    }


# ========================
# Auth Endpoints
# ========================
@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Login with username and password. Setzt HttpOnly-Cookie (kein Token im localStorage)."""
    result = await db.execute(
        select(User).where(User.username == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falscher Benutzername oder Passwort",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto ist deaktiviert",
        )

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    user_dict = await _user_to_dict(user, db)

    response = JSONResponse(content={
        "access_token": token,
        "token_type": "bearer",
        "user": user_dict,
    })
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
    db: AsyncSession = Depends(get_db),
):
    """Register a new user (only when registration is enabled in settings)."""
    if not await _get_auth_setting(db, "registration_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung ist deaktiviert",
        )

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
    return {"message": "Registrierung erfolgreich. Du kannst dich jetzt anmelden."}


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
    smtp = await get_smtp_settings(db)
    if not smtp.get("enabled") or not smtp.get("host"):
        logger.warning("SMTP not configured - cannot send password reset email")
        return {"message": "Falls ein Konto mit dieser Angabe existiert, wurde eine E-Mail versendet."}

    subject = "Passwort zurücksetzen – DNS Manager"
    body_html = f"""
    <p>Hallo {user.display_name or user.username},</p>
    <p>du hast eine Zurücksetzung deines Passworts angefordert.</p>
    <p>Klicke auf den folgenden Link, um ein neues Passwort zu setzen (der Link ist 1 Stunde gültig):</p>
    <p><a href="{reset_url}">{reset_url}</a></p>
    <p>Falls du das nicht warst, ignoriere diese E-Mail.</p>
    """
    body_text = f"Passwort zurücksetzen: {reset_url}"
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
    """Reset a user's password. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    new_password = user.username
    user.hashed_password = hash_password(new_password)
    await db.flush()
    return {"message": f"Passwort fuer '{user.username}' zurueckgesetzt (neues Passwort: {new_password})"}


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

    # Neue Zuordnungen erstellen
    for zone in data.zones:
        zone_name = zone.strip().lower()
        if not zone_name.endswith('.'):
            zone_name += '.'
        db.add(UserZoneAccess(user_id=user_id, zone_name=zone_name))

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
        select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == user_id)
    )
    zones = [row[0] for row in result.all()]
    return {"user_id": user_id, "zones": zones}
