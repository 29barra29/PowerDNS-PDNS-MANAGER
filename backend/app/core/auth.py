"""Authentication and authorization utilities."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import User

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme (für Authorization: Bearer)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_token_from_cookie_or_bearer(request: Request, token: Optional[str] = Depends(oauth2_scheme)) -> Optional[str]:
    """Token aus HttpOnly-Cookie (bevorzugt) oder aus Authorization-Header."""
    if token:
        return token
    return request.cookies.get(settings.AUTH_COOKIE_NAME)


def hash_password(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token."""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_password_reset_token(user_id: int) -> str:
    """Create a short-lived JWT for password reset (1 hour)."""
    to_encode = {"sub": str(user_id), "type": "password_reset"}
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_password_reset_token(token: str) -> Optional[int]:
    """Decode password reset token; returns user_id or None."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "password_reset":
        return None
    sub = payload.get("sub")
    return int(sub) if sub else None


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(get_token_from_cookie_or_bearer),
) -> User:
    """Get the current authenticated user from JWT token (Cookie oder Bearer)."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht angemeldet",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )
    
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Benutzer nicht gefunden oder deaktiviert",
        )
    
    return user


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is an admin."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Administratoren haben Zugriff",
        )
    return current_user


async def create_initial_admin(db: AsyncSession):
    """Create the initial admin user if no users exist and registration is disabled."""
    result = await db.execute(select(User).limit(1))
    if result.scalar_one_or_none() is None:
        # Prüfe ob Registrierung aktiviert ist
        if settings.ENABLE_REGISTRATION:
            logger.info("Registration enabled - waiting for first user to register as admin")
            return

        # Verwende konfiguriertes Passwort oder generiere eines
        if settings.INITIAL_ADMIN_PASSWORD:
            password = settings.INITIAL_ADMIN_PASSWORD
            logger.info(f"Creating admin user with configured password")
        else:
            import secrets
            password = secrets.token_urlsafe(16)
            logger.warning(f"Creating admin user with generated password: {password}")
            logger.warning("SAVE THIS PASSWORD! It will not be shown again.")

        admin = User(
            username="admin",
            email="admin@dns-manager.local",
            hashed_password=hash_password(password),
            display_name="Administrator",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        logger.info(f"Initial admin user created (username: admin)")
