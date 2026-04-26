"""Authentication and authorization utilities."""
import hashlib
import logging
import secrets as _secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import User, UserZoneAccess, PanelToken

logger = logging.getLogger(__name__)

# Password hashing - pwdlib mit bcrypt (passlib ist seit 2020 unmaintained,
# bcrypt-Hash-Format ist identisch -> bestehende Hashes weiter gueltig).
password_hash = PasswordHash((BcryptHasher(),))

# OAuth2 scheme (für Authorization: Bearer)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# Token-Typen (im JWT-Claim "typ"). Trennen Session-Tokens strikt von Reset-Tokens,
# damit ein Reset-Link nie als Session-Token missbraucht werden kann.
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_PASSWORD_RESET = "password_reset"
TOKEN_TYPE_2FA_PENDING = "2fa_pending"

# Panel-API-Bearer, unterscheidbar von zufälligen Zeichen und ACME-Token
PANEL_TOKEN_PREFIX = "dnsmgr_usr_"

# Mindestlänge für Passwörter (gilt für Setup, Register, Reset und Admin-Updates).
MIN_PASSWORD_LENGTH = 8


def get_token_from_cookie_or_bearer(request: Request, token: Optional[str] = Depends(oauth2_scheme)) -> Optional[str]:
    """Token aus HttpOnly-Cookie (bevorzugt) oder aus Authorization-Header."""
    if token:
        return token
    return request.cookies.get(settings.AUTH_COOKIE_NAME)


def hash_password(password: str) -> str:
    """Hash a password."""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    try:
        return password_hash.verify(plain_password, hashed_password)
    except Exception:
        # Defekte/inkompatible Hashes -> als ungueltig behandeln, nicht 500
        return False


def generate_random_password(length: int = 16) -> str:
    """Cryptographically secure random password (URL-safe)."""
    return _secrets.token_urlsafe(length)[:length]


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access (session) token. Always tagged with typ=access."""
    to_encode = data.copy()
    to_encode["typ"] = TOKEN_TYPE_ACCESS
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token without enforcing a token type."""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_password_reset_token(user_id: int) -> str:
    """Create a short-lived JWT for password reset (1 hour). Tagged typ=password_reset."""
    to_encode = {"sub": str(user_id), "typ": TOKEN_TYPE_PASSWORD_RESET}
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_two_factor_pending_token(user_id: int) -> str:
    """Kurzlebiger JWT (5 min) – beweist erfolgreiche Passwort-Prüfung vor TOTP-Abschluss."""
    to_encode = {"sub": str(user_id), "typ": TOKEN_TYPE_2FA_PENDING}
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_two_factor_pending_token(token: str) -> Optional[int]:
    """Liefert user_id aus einem pending-2FA-Token oder None."""
    payload = decode_token(token)
    if not payload:
        return None
    typ = payload.get("typ") or payload.get("type")
    if typ != TOKEN_TYPE_2FA_PENDING:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


def decode_password_reset_token(token: str) -> Optional[int]:
    """Decode password reset token; returns user_id or None.

    Akzeptiert NUR Tokens mit typ=password_reset (oder altem 'type'-Claim für Bestand).
    """
    payload = decode_token(token)
    if not payload:
        return None
    typ = payload.get("typ") or payload.get("type")
    if typ != TOKEN_TYPE_PASSWORD_RESET:
        return None
    sub = payload.get("sub")
    return int(sub) if sub else None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(get_token_from_cookie_or_bearer),
) -> User:
    """Aktuellen Benutzer aus JWT (Cookie oder Bearer) oder Panel-API-Token holen.

    Lehnt ausdrücklich Tokens ab, die keine Session-Tokens sind (z.B. Password-Reset).
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht angemeldet",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- Panel-API-Token (Bearer, Prefix dnsmgr_usr_ – kein JWT) -------------------
    if token.startswith(PANEL_TOKEN_PREFIX):
        t_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        result = await db.execute(
            select(PanelToken).where(
                PanelToken.token_hash == t_hash,
                PanelToken.is_active.is_(True),
            )
        )
        pt = result.scalar_one_or_none()
        if pt:
            r_ip = request.client.host if request.client else None
            now = datetime.now(timezone.utc)
            pt.last_used_at = now
            if r_ip:
                pt.last_used_ip = r_ip
            await db.flush()
            result = await db.execute(select(User).where(User.id == pt.user_id))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger API-Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Strenge Trennung: nur typ=access wird als Session akzeptiert.
    typ = payload.get("typ") or payload.get("type")
    if typ != TOKEN_TYPE_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token-Typ",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )

    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger Token")

    result = await db.execute(select(User).where(User.id == uid_int))
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


def _normalize_zone_name(zone_id: str) -> str:
    """Normalisiert Zonen-Namen (lower + trailing dot) – muss zur Speicherung in UserZoneAccess passen."""
    z = (zone_id or "").strip().lower()
    if not z:
        return z
    if not z.endswith("."):
        z += "."
    return z


async def assert_zone_access(
    db: AsyncSession,
    user: User,
    zone_id: str,
    *,
    write: bool = False,
) -> None:
    """403 wenn Nicht-Admin die Zone nicht sieht; bei write=True zusätzlich bei Rolle „read“."""
    if user.role == "admin":
        return
    zone_name = _normalize_zone_name(zone_id)
    if not zone_name:
        raise HTTPException(status_code=400, detail="Zone-Name fehlt")
    result = await db.execute(
        select(UserZoneAccess.permission).where(
            UserZoneAccess.user_id == user.id,
            UserZoneAccess.zone_name == zone_name,
        ).limit(1)
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine Berechtigung für diese Zone",
        )
    if not write:
        return
    p = (perm or "manage").strip().lower() or "manage"
    if p == "read":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Lese-Zugriff auf diese Zone",
        )


async def create_initial_admin(db: AsyncSession):
    """Create the initial admin user if no users exist and registration is disabled."""
    result = await db.execute(select(User).limit(1))
    if result.scalar_one_or_none() is None:
        # Prüfe ob Registrierung aktiviert ist
        if settings.ENABLE_REGISTRATION:
            logger.info("Registration enabled - waiting for first user to register as admin")
            return

        # Verwende konfiguriertes Passwort oder generiere eines.
        # Das generierte Passwort wird genau EINMAL in eine Datei geschrieben (chmod 600),
        # damit es nicht dauerhaft im Container-Log steht.
        from pathlib import Path

        if settings.INITIAL_ADMIN_PASSWORD:
            password = settings.INITIAL_ADMIN_PASSWORD
            logger.info("Creating initial admin from INITIAL_ADMIN_PASSWORD env (please remove from .env after first login)")
        else:
            password = generate_random_password(20)
            try:
                creds_path = Path("/app/.initial-admin-password")
                creds_path.write_text(
                    "# Initial admin credentials – delete this file after first login!\n"
                    f"username: admin\npassword: {password}\n",
                    encoding="utf-8",
                )
                try:
                    creds_path.chmod(0o600)
                except Exception:
                    pass
                logger.warning(
                    "Initial admin generated. Credentials stored at /app/.initial-admin-password – "
                    "log into the panel, then DELETE that file."
                )
            except Exception:
                # Fallback: Log-Warnung, falls Schreiben fehlschlägt
                logger.warning("Could not write /app/.initial-admin-password; printing once to log:")
                logger.warning(f"INITIAL ADMIN PASSWORD (admin/{password}) – save this!")

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
        logger.info("Initial admin user created (username: admin)")
