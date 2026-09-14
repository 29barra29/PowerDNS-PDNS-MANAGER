"""Authentication and authorization utilities."""
import hashlib
import time
import pyotp
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
from app.core.client_ip import get_client_ip
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
# WebAuthn-Challenges: kurzlebige, signierte Tokens, die die ausgegebene Challenge
# an die jeweilige Zeremonie binden (gleiches stateless-Pattern wie 2FA-Pending).
TOKEN_TYPE_WEBAUTHN_REG = "webauthn_reg"
TOKEN_TYPE_WEBAUTHN_AUTH = "webauthn_auth"

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


def password_version(hashed_password: Optional[str]) -> str:
    """Kurzer Fingerabdruck des aktuellen Passwort-Hashes.

    Wird in Session- und Reset-Tokens eingebettet: aendert sich das Passwort, passen
    alte Tokens nicht mehr -> alle Sessions sind sofort ungueltig, ein Reset-Link ist
    nur einmal nutzbar.
    """
    return hashlib.sha256((hashed_password or "").encode("utf-8")).hexdigest()[:16]


# --- Replay-Schutz fuer TOTP und WebAuthn-Challenges (In-Memory, ein Worker) ---------
_TOTP_USED: dict[tuple[int, str], float] = {}
_TOTP_TTL = 120.0  # laenger als 2x30s-Fenster (valid_window=1)


def totp_verify_once(user_id: int, secret: str, code: str) -> bool:
    """Prueft einen TOTP-Code und akzeptiert denselben Code pro Nutzer nur EINMAL.

    pyotp allein wuerde einen mitgelesenen Code innerhalb von +-1 Zeitschritt
    (bis ~90 s) beliebig oft akzeptieren.
    """
    code = (code or "").strip().replace(" ", "")
    if not code or not secret:
        return False
    now = time.time()
    for k, exp in list(_TOTP_USED.items()):
        if exp < now:
            _TOTP_USED.pop(k, None)
    key = (int(user_id), code)
    if key in _TOTP_USED:
        return False
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False
    _TOTP_USED[key] = now + _TOTP_TTL
    return True


_WEBAUTHN_USED: dict[str, float] = {}
_WEBAUTHN_TTL = 6 * 60.0  # Challenge-Token leben 5 min


def _consume_webauthn_challenge(challenge_b64: str) -> bool:
    """Markiert eine Challenge als verbraucht; False, wenn sie schon benutzt wurde."""
    now = time.time()
    for k, exp in list(_WEBAUTHN_USED.items()):
        if exp < now:
            _WEBAUTHN_USED.pop(k, None)
    if challenge_b64 in _WEBAUTHN_USED:
        return False
    _WEBAUTHN_USED[challenge_b64] = now + _WEBAUTHN_TTL
    return True


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None, *, user=None) -> str:
    """Create a JWT access (session) token. Always tagged with typ=access.

    ``user`` (empfohlen) bindet die Session an den aktuellen Passwort-Hash (Claim ``pwv``).
    """
    to_encode = data.copy()
    to_encode["typ"] = TOKEN_TYPE_ACCESS
    if user is not None:
        to_encode["pwv"] = password_version(getattr(user, "hashed_password", None))
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


def create_password_reset_token(user_id: int, hashed_password: Optional[str] = None) -> str:
    """Create a short-lived JWT for password reset (1 hour). Tagged typ=password_reset.

    Enthaelt ``pwv`` (Fingerabdruck des aktuellen Hashes): sobald das Passwort gesetzt
    wurde, passt der Link nicht mehr -> Einmal-Nutzung ohne DB-Tabelle.
    """
    to_encode = {"sub": str(user_id), "typ": TOKEN_TYPE_PASSWORD_RESET, "pwv": password_version(hashed_password)}
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


def create_webauthn_challenge_token(
    challenge_b64: str,
    *,
    purpose: str,
    user_id: Optional[int] = None,
) -> str:
    """Kurzlebiger JWT (5 min), der eine WebAuthn-Challenge an die Zeremonie bindet.

    ``purpose`` ist TOKEN_TYPE_WEBAUTHN_REG oder TOKEN_TYPE_WEBAUTHN_AUTH. Bei der
    Registrierung wird zusätzlich die user_id eingebettet, damit ein Token nicht für
    ein fremdes Konto wiederverwendet werden kann.
    """
    to_encode: dict = {"chal": challenge_b64, "typ": purpose}
    if user_id is not None:
        to_encode["sub"] = str(user_id)
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_webauthn_challenge_token(token: str, *, purpose: str) -> Optional[dict]:
    """Validiert ein WebAuthn-Challenge-Token und liefert das Payload (chal, ggf. sub) oder None."""
    payload = decode_token(token)
    if not payload:
        return None
    typ = payload.get("typ") or payload.get("type")
    if typ != purpose:
        return None
    if not payload.get("chal"):
        return None
    # Einmal-Nutzung: dieselbe Challenge darf keine zweite Zeremonie abschliessen
    # (sonst waere eine mitgeschnittene Assertion bei sign_count 0 wiederverwendbar).
    if not _consume_webauthn_challenge(str(payload["chal"])):
        return None
    return payload


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


def decode_password_reset_payload(token: str) -> Optional[dict]:
    """Wie decode_password_reset_token, liefert aber das ganze Payload (sub, pwv)."""
    payload = decode_token(token)
    if not payload:
        return None
    typ = payload.get("typ") or payload.get("type")
    if typ != TOKEN_TYPE_PASSWORD_RESET or not payload.get("sub"):
        return None
    return payload


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
            r_ip = get_client_ip(request)
            now = datetime.now(timezone.utc)
            pt.last_used_at = now
            if r_ip:
                pt.last_used_ip = r_ip
            await db.flush()
            result = await db.execute(select(User).where(User.id == pt.user_id))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                request.state.auth_via = "panel_token"
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

    # Session an den Passwort-Hash gebunden: nach Passwortaenderung/-reset sind alle
    # bestehenden Sessions ungueltig. Tokens ohne pwv (vor diesem Update) ebenfalls.
    if payload.get("pwv") != password_version(user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sitzung abgelaufen – bitte erneut anmelden",
        )

    request.state.auth_via = "session"
    return user


async def get_session_user(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """Wie get_current_user, aber nur fuer echte Browser-Sessions (kein Panel-API-Token).

    Credential-Verwaltung (Passwort, E-Mail, TOTP, Passkeys, Panel-Tokens) darf nicht mit
    einem geleakten API-Token moeglich sein, sonst laesst sich dauerhafter Zugang verankern.
    """
    if getattr(request.state, "auth_via", None) == "panel_token":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Aktion ist mit einem API-Token nicht erlaubt – bitte im Browser anmelden",
        )
    return current_user


async def get_admin_session_user(
    current_user: User = Depends(get_session_user),
) -> User:
    """Admin UND echte Browser-Session (kein Panel-API-Token) – fuer Benutzer-/Credential-Verwaltung."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin-Rechte erforderlich")
    return current_user


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
