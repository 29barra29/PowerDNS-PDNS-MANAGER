"""First-run setup endpoints (bewusst klein gehalten)."""
import logging
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr, Field

from app.core.database import get_db
from app.core.auth import hash_password, create_access_token, MIN_PASSWORD_LENGTH
from app.models.models import User
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["Setup"])


class SetupStatus(BaseModel):
    is_setup_complete: bool
    has_users: bool
    registration_enabled: bool
    app_name: str
    app_version: str


class RegisterFirstUser(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    email: EmailStr
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=100)


@router.get("/status", response_model=SetupStatus)
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    """Check if initial setup is complete."""
    user_count = await db.scalar(select(func.count(User.id)))
    has_users = user_count > 0
    registration_enabled = settings.ENABLE_REGISTRATION
    is_setup_complete = has_users or not registration_enabled
    return SetupStatus(
        is_setup_complete=is_setup_complete,
        has_users=has_users,
        registration_enabled=registration_enabled and not has_users,
        app_name=settings.APP_NAME,
        app_version=settings.APP_VERSION,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_first_user(
    user_data: RegisterFirstUser,
    db: AsyncSession = Depends(get_db),
):
    """Register the first user as admin (only works if no users exist).

    Race-sicher: Wir versuchen das Insert; wenn parallel bereits ein User entstanden ist
    (Unique-Constraint auf username/Anzahl), bricht IntegrityError, und wir liefern 403.
    """
    if not settings.ENABLE_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung ist deaktiviert",
        )

    user_count = await db.scalar(select(func.count(User.id)))
    if user_count and user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung nicht mehr möglich – Admin existiert bereits",
        )

    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        display_name=user_data.display_name,
        role="admin",
        is_active=True,
    )
    db.add(new_user)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Benutzername oder E-Mail bereits vergeben",
        )

    # Doppelt-Prüfung gegen TOCTOU-Lücke (zwischen count() und INSERT könnten zwei Requests durchgerutscht sein):
    # Wenn nun mehr als 1 Admin existiert UND wir nicht der älteste sind, abbrechen.
    final_count = await db.scalar(select(func.count(User.id)))
    if final_count and final_count > 1:
        # Es gab parallel einen anderen Admin – wir behalten den ersten und löschen den eigenen wieder
        await db.delete(new_user)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung nicht mehr möglich – Admin existiert bereits",
        )

    await db.commit()
    await db.refresh(new_user)

    access_token = create_access_token(data={"sub": str(new_user.id), "role": new_user.role})
    user_dict = {
        "id": new_user.id,
        "username": new_user.username,
        "email": new_user.email,
        "display_name": new_user.display_name,
        "role": new_user.role,
        "zones": [],
        "created_at": new_user.created_at.isoformat() if new_user.created_at else None,
        "last_login": None,
    }

    logger.info(f"First admin user created: {new_user.username}")

    response = JSONResponse(
        status_code=201,
        content={
            "message": "Administrator-Account erfolgreich erstellt!",
            "user": user_dict,
        },
    )
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=access_token,
        max_age=settings.AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return response
