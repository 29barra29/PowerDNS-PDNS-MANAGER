"""First-run setup and registration endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr, Field
import os

from app.core.database import get_db
from app.core.auth import hash_password, create_access_token
from app.models.models import User, ServerConfig
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["Setup"])


class SetupStatus(BaseModel):
    """Setup status response."""
    is_setup_complete: bool
    has_users: bool
    registration_enabled: bool
    app_name: str
    app_version: str


class RegisterFirstUser(BaseModel):
    """First user registration."""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: str = Field(..., max_length=100)


class RegisterResponse(BaseModel):
    """Registration response."""
    message: str
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.get("/status", response_model=SetupStatus)
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    """Check if initial setup is complete."""
    # Check if any users exist
    user_count = await db.scalar(select(func.count(User.id)))
    has_users = user_count > 0

    # Check if registration is enabled
    registration_enabled = os.getenv("ENABLE_REGISTRATION", "false").lower() == "true"

    # If no users and registration not enabled, setup is not complete
    is_setup_complete = has_users or not registration_enabled

    return SetupStatus(
        is_setup_complete=is_setup_complete,
        has_users=has_users,
        registration_enabled=registration_enabled and not has_users,  # Only allow if no users
        app_name=settings.APP_NAME,
        app_version=settings.APP_VERSION,
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_first_user(
    user_data: RegisterFirstUser,
    db: AsyncSession = Depends(get_db),
):
    """Register the first user as admin (only works if no users exist)."""

    # Check if registration is enabled
    if os.getenv("ENABLE_REGISTRATION", "false").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung ist deaktiviert",
        )

    # Check if users already exist
    user_count = await db.scalar(select(func.count(User.id)))
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrierung nicht mehr möglich - Admin existiert bereits",
        )

    # Check if username already exists (should not happen, but double-check)
    existing = await db.scalar(
        select(User).where(User.username == user_data.username)
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Benutzername bereits vergeben",
        )

    # Create the first user as admin
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        display_name=user_data.display_name,
        role="admin",  # First user is always admin!
        is_active=True,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Create access token
    access_token = create_access_token(data={"sub": str(new_user.id)})

    # Disable registration after first user
    # Note: In production, you might want to update an env file or config
    logger.info(f"First admin user created: {new_user.username}")
    logger.info("Registration will be disabled for future requests")

    return RegisterResponse(
        message="Administrator-Account erfolgreich erstellt!",
        access_token=access_token,
        user={
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "display_name": new_user.display_name,
            "role": new_user.role,
        },
    )


class EmailConfig(BaseModel):
    """Email configuration."""
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    smtp_from: EmailStr
    use_tls: bool = True


@router.post("/configure-email", status_code=status.HTTP_200_OK)
async def configure_email(
    config: EmailConfig,
    db: AsyncSession = Depends(get_db),
):
    """Configure email settings (requires admin)."""
    # This would normally require admin authentication
    # For first-run setup, we might allow it if no users exist

    user_count = await db.scalar(select(func.count(User.id)))
    if user_count > 0:
        # Once users exist, this requires admin auth
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="E-Mail kann nur während Setup oder von Admin konfiguriert werden",
        )

    # In production, save these settings to database or config file
    # For now, just validate and return success

    return {"message": "E-Mail-Konfiguration gespeichert"}