"""Authentifizierung und Autorisierung (Bauplan B.3, F14 5.2, F2/F3 5.6).

Prinzipale einer Anfrage:
- Browser-Session (JWT im HttpOnly-Cookie oder als Bearer), ``auth_via = "session"``;
- Panel-API-Token (``dnsmgr_usr_...``, nur aus dem ``Authorization``-Header), ``auth_via = "panel_token"``,
  optional eingeschraenkt auf Zonen (``scope_zones``), Leserecht (``permission = "read"``), Ablauf und
  Admin-Freigabe (``allow_admin``) – die effektiven Rechte sind die Schnittmenge aus Benutzer- und Token-Rechten;
- eigene Token-Arten (ACME, spaeter DynDNS) setzen den Kontext ueber ``set_auth_context``.

Durchsetzung zentral: ``get_current_user`` (Token-Zustand, Methodenregel, Passwortwechsel-Gate),
``assert_zone_access`` (Token-Scope vor dem Admin-Shortcut), ``get_admin_user`` (``allow_admin``),
``get_session_user``/``get_admin_session_user`` (nur Browser-Session). Inline-Pruefungen
``role == "admin"`` ausserhalb dieses Moduls sind verboten – stattdessen ``is_effective_admin``.

F10 (Welle 2, additiv): Pending-2FA-Token mit Methode (``create_two_factor_pending_token(..., method=)``,
``decode_two_factor_pending_payload``), Re-Exporte des OIDC-State-Cookies aus ``services/sso_oidc.py`` und
``verify_step_up`` (erneute Bestaetigung vor kritischen Aktionen, B.3 [S8]).
"""
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
from pydantic import BaseModel, Field
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.client_ip import get_client_ip
from app.core.database import get_db
from app.core.names import normalize_zone_name
# Re-Export (F6/F7/F9 importieren den Kontext auch aus app.core.auth)
from app.core.request_context import (  # noqa: F401
    TokenScope,
    actor_username_ctx,
    auth_via_ctx,
    client_ip_ctx,
    current_token_scope,
    get_auth_via,
    get_token_scope,
    reset_request_context,
)
from app.core.timeutil import to_naive_utc, utcnow
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

# --- Texte und Regeln der Token-Durchsetzung (F14 2.8) -----------------------------------------
# Methoden, die ein Lese-Token ausfuehren darf (alles andere -> 403 vor jeder Endpunktlogik).
_TOKEN_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOKEN_INVALID_DETAIL = "Ungültiger API-Token"
TOKEN_PAUSED_DETAIL = "API-Token ist deaktiviert"
TOKEN_EXPIRED_DETAIL = "API-Token ist abgelaufen"
TOKEN_READ_ONLY_DETAIL = "Dieser API-Token hat nur Leserechte"
TOKEN_NO_ADMIN_DETAIL = (
    "Dieser API-Token hat keine Admin-Rechte – Token mit „Admin-Funktionen erlauben“ "
    "anlegen oder im Browser anmelden"
)
SESSION_REQUIRED_DETAIL = "Diese Aktion ist mit einem API-Token nicht erlaubt – bitte im Browser anmelden"
ADMIN_ONLY_DETAIL = "Nur Administratoren haben Zugriff"

# --- Gate "Passwortwechsel erforderlich" (F2/F3 3.2.11, 5.6) ------------------------------------
# Gilt nur fuer Browser-Sessions; Panel-Tokens sind ausgenommen (F3 E7).
PASSWORD_CHANGE_REQUIRED_DETAIL = "Passwortänderung erforderlich – bitte zuerst ein neues Passwort festlegen"
PASSWORD_CHANGE_HEADER = "X-Password-Change-Required"
_PASSWORD_CHANGE_ALLOWED = frozenset({
    ("GET", "/api/v1/auth/me"),
    ("PUT", "/api/v1/auth/me/password"),
    ("POST", "/api/v1/auth/logout"),
})


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
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))
    # iat = Anmeldezeitpunkt (request.state.auth_time; Step-up fuer externe Konten, Bauplan B.3 [S8])
    to_encode.setdefault("iat", now)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token without enforcing a token type."""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_password_reset_token(
    user_id: int,
    hashed_password: Optional[str] = None,
    *,
    expires_minutes: int = 60,
) -> str:
    """Kurzlebiger JWT fuer den Passwort-Reset (Default 60 Minuten), ``typ=password_reset``.

    Enthaelt ``pwv`` (Fingerabdruck des aktuellen Hashes): sobald das Passwort gesetzt
    wurde, passt der Link nicht mehr -> Einmal-Nutzung ohne DB-Tabelle. Admin-Reset-Links
    (F3) nutzen eine laengere Gueltigkeit ueber ``expires_minutes``.
    """
    to_encode = {"sub": str(user_id), "typ": TOKEN_TYPE_PASSWORD_RESET, "pwv": password_version(hashed_password)}
    expire = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(expires_minutes)))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# Erste Anmeldestufe vor dem TOTP-Schritt (Claim "m" im Pending-Token, F10 5.2). Unbekannte Werte gelten als
# "password" (strengste Regel: lokale Anmeldung muss erlaubt sein).
TWO_FACTOR_METHODS = ("password", "ldap", "oidc")


def create_two_factor_pending_token(user_id: int, *, method: str = "password") -> str:
    """Kurzlebiger JWT (5 min) – beweist die erste Anmeldestufe vor dem TOTP-Abschluss.

    ``method`` (``password`` | ``ldap`` | ``oidc``) steht im Claim ``m``; ``POST /auth/login/2fa`` schreibt daraus
    die Audit-Methode (``<method>+totp``) und prueft bei ``password`` die Abschaltung der lokalen Anmeldung.
    """
    m = method if method in TWO_FACTOR_METHODS else "password"
    to_encode = {"sub": str(user_id), "typ": TOKEN_TYPE_2FA_PENDING, "m": m}
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_two_factor_pending_payload(token: Optional[str]) -> Optional[dict]:
    """Payload eines pending-2FA-Tokens (``sub`` als int, ``m`` normalisiert) oder None.

    Tokens aus 2.4.x (ohne ``m``) gelten als ``password``.
    """
    if not token or not isinstance(token, str) or len(token) > 4096:
        return None
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
        uid = int(sub)
    except (TypeError, ValueError):
        return None
    m = payload.get("m")
    return {**payload, "sub": uid, "m": m if m in TWO_FACTOR_METHODS else "password"}


def decode_two_factor_pending_token(token: str) -> Optional[int]:
    """Liefert user_id aus einem pending-2FA-Token oder None."""
    payload = decode_two_factor_pending_payload(token)
    return payload["sub"] if payload else None


# --- OIDC-State-Cookie (F10 5.2) ---------------------------------------------------------------
# Die Implementierung liegt in ``services/sso_oidc.py`` (WS-F10-SVC, dort von den Pflichttests genutzt). Hier nur
# delegierende Re-Exporte mit spaetem Import: ``sso_oidc`` importiert dieses Modul, ein Import auf Modulebene
# waere zirkulaer.
TOKEN_TYPE_OIDC_STATE = "oidc_state"


def create_oidc_state_token(*, state: str, nonce: str, code_verifier: str, issuer: str, intent: str,
                            user_id: Optional[int] = None, pwv: Optional[str] = None) -> str:
    """Signierter Kurzzeit-JWT (10 min) fuer das OIDC-State-Cookie (siehe ``sso_oidc.create_oidc_state_token``)."""
    from app.services import sso_oidc

    return sso_oidc.create_oidc_state_token(state=state, nonce=nonce, code_verifier=code_verifier, issuer=issuer,
                                            intent=intent, user_id=user_id, pwv=pwv)


def decode_oidc_state_token(token: Optional[str]) -> Optional[dict]:
    """Payload des State-Cookies oder None (Signatur, Ablauf, Typ, Pflichtfelder) – ohne Verbrauch."""
    from app.services import sso_oidc

    return sso_oidc.decode_oidc_state_token(token)


def _consume_oidc_state(state: str) -> bool:
    """Einmal-Verbrauch des OIDC-States (False bei Wiederverwendung)."""
    from app.services import sso_oidc

    return sso_oidc.consume_oidc_state(state)


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


# Alias (Bestandsname): lower + Trailing-Dot – passt zur Speicherung in ``UserZoneAccess``.
_normalize_zone_name = normalize_zone_name


# ---------------------------------------------------------------------------------------------
# Request-Kontext und Token-Scope (F14 5.2)
# ---------------------------------------------------------------------------------------------
def scope_from_token(pt: PanelToken) -> TokenScope:
    """Baut den ``TokenScope`` eines Panel-Tokens – defensiv (fail-safe) bei unerwarteten DB-Werten.

    Unbekannte ``permission`` gilt als Leserecht; ``scope_zones`` in einem anderen Format als Liste
    ergibt einen leeren Scope (kein Zonenzugriff), ``NULL`` bedeutet alle Zonen des Besitzers.
    """
    raw = pt.scope_zones
    if raw is None:
        zones = None
    elif isinstance(raw, list):
        zones = frozenset(z for z in (_normalize_zone_name(str(x)) for x in raw) if z)
    else:
        logger.warning(
            "Panel-Token %s: scope_zones hat ungueltiges Format – Token erhaelt keinen Zonenzugriff",
            pt.token_prefix,
        )
        zones = frozenset()
    perm = (pt.permission or "manage").strip().lower()
    if perm not in ("manage", "read"):
        perm = "read"
    return TokenScope(
        token_id=pt.id,
        name=pt.name,
        token_prefix=pt.token_prefix,
        zones=zones,
        permission=perm,
        allow_admin=bool(pt.allow_admin),
    )


def set_auth_context(
    request: Optional[Request],
    via: Optional[str],
    scope: Optional[TokenScope] = None,
    token_row=None,
    *,
    username: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> None:
    """Einziger Setter fuer den Auth-Kontext einer Anfrage (auch fuer ACME/DynDNS-Tokens).

    Setzt ``request.state.auth_via/token_scope/panel_token`` und alle ContextVars aus
    ``core.request_context`` (``auth_via``, Token-Scope, Akteur und Client-IP fuer das Audit).
    ``set_auth_context(request, None)`` verwirft den Kontext vollstaendig.
    """
    if request is not None:
        request.state.auth_via = via
        request.state.token_scope = scope
        request.state.panel_token = token_row
    auth_via_ctx.set(via)
    current_token_scope.set(scope)
    actor_username_ctx.set((username or "")[:100] or None)
    client_ip_ctx.set((client_ip or "")[:64] or None)


def is_effective_admin(user) -> bool:
    """Admin-Rolle UND (Browser-Session/kein Token-Kontext oder Token mit ``allow_admin``).

    Ersetzt Inline-Pruefungen ``role == "admin"`` in Routern und Services.
    """
    if getattr(user, "role", None) != "admin":
        return False
    scope = current_token_scope.get()
    return scope is None or scope.allow_admin


def assert_token_scope(zone_id: str, *, write: bool = False) -> None:
    """Reine Token-Pruefung ohne DB (No-op ohne Token-Kontext).

    ``write=True`` und Lese-Token -> 403; Zone ausserhalb ``scope_zones`` -> 403.
    """
    scope = current_token_scope.get()
    if scope is None:
        return
    if write and scope.read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TOKEN_READ_ONLY_DETAIL)
    if not scope.zone_limited:
        return
    zone_name = _normalize_zone_name(zone_id)
    if not zone_name or not scope.covers_zone(zone_name):
        logger.info("Panel-Token %s: Zone %s ausserhalb des Scopes", scope.token_prefix, zone_name or "-")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Dieser API-Token ist für die Zone „{zone_name}“ nicht freigegeben",
        )


def assert_not_zone_scoped(detail: str) -> None:
    """403 mit ``detail``, wenn die Anfrage mit einem auf Zonen beschraenkten Token laeuft."""
    scope = current_token_scope.get()
    if scope is not None and scope.zone_limited:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _invalid_api_token(detail: str = TOKEN_INVALID_DETAIL) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _request_path(request: Request) -> str:
    """Pfad ohne ``root_path`` und ohne Schraegstrich am Ende (fuer Allowlist-Vergleiche)."""
    path = request.scope.get("path", "") or ""
    root = request.scope.get("root_path", "") or ""
    if root and path.startswith(root):
        path = path[len(root):]
    return path.rstrip("/") or "/"


def _password_change_allowed(request: Request) -> bool:
    """True fuer die drei Pfade, die bei erzwungenem Passwortwechsel erlaubt bleiben."""
    return (request.method.upper(), _request_path(request)) in _PASSWORD_CHANGE_ALLOWED


async def _authenticate_panel_token(request: Request, db: AsyncSession, token: str) -> User:
    """Panel-Token-Zweig von ``get_current_user`` (F14 3.11, Texte F14 2.8)."""
    method = request.method.upper()
    hdr = (request.headers.get("authorization") or "").strip()
    if not hdr.lower().startswith("bearer ") or hdr[7:].strip() != token:
        # Panel-Tokens nur aus dem Authorization-Header – nie aus dem Session-Cookie
        logger.info("Panel-Token ausserhalb des Authorization-Headers abgelehnt (%s %s)", method, _request_path(request))
        raise _invalid_api_token()
    t_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await db.execute(select(PanelToken).where(PanelToken.token_hash == t_hash))
    pt = result.scalar_one_or_none()
    if pt is None or pt.revoked_at is not None:
        logger.info("Unbekannter oder widerrufener Panel-Token (%s %s)", method, _request_path(request))
        raise _invalid_api_token()
    if not pt.is_active:
        logger.info("Panel-Token %s ist pausiert (%s %s)", pt.token_prefix, method, _request_path(request))
        raise _invalid_api_token(TOKEN_PAUSED_DETAIL)
    now = utcnow()
    if pt.expires_at is not None and to_naive_utc(pt.expires_at) <= now:
        logger.info("Panel-Token %s ist abgelaufen (%s %s)", pt.token_prefix, method, _request_path(request))
        raise _invalid_api_token(TOKEN_EXPIRED_DETAIL)
    result = await db.execute(select(User).where(User.id == pt.user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        logger.info("Panel-Token %s: Besitzer fehlt oder ist deaktiviert", pt.token_prefix)
        raise _invalid_api_token()
    scope = scope_from_token(pt)
    if scope.read_only and method not in _TOKEN_SAFE_METHODS:
        logger.info("Panel-Token %s (Leserecht): %s %s abgelehnt", pt.token_prefix, method, _request_path(request))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TOKEN_READ_ONLY_DETAIL)
    client_ip = get_client_ip(request)
    from app.services.panel_token import touch_last_used  # lazy: panel_token importiert dieses Modul

    if touch_last_used(pt, client_ip, now):
        await db.flush()
    set_auth_context(request, "panel_token", scope, pt, username=user.username, client_ip=client_ip)
    return user


# Die Session-Abhaengigkeit laeuft mit scope="function" (wie ``DbWrite``): schreibende Handler teilen sich
# damit dieselbe Session mit der Authentifizierung (ein Commit VOR dem Senden der Antwort, Bauplan B.7 [D2]),
# und ``current_user`` gehoert zur Handler-Session.
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    token: Optional[str] = Depends(get_token_from_cookie_or_bearer),
) -> User:
    """Aktuellen Benutzer aus JWT (Cookie oder Bearer) oder Panel-API-Token (nur Header) holen.

    Lehnt Tokens ab, die keine Session-Tokens sind (z. B. Password-Reset). Setzt den Auth-Kontext
    (``request.state`` + ContextVars) und prueft bei Browser-Sessions das Passwortwechsel-Gate.
    """
    set_auth_context(request, None)  # Altwerte verwerfen
    request.state.auth_time = None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht angemeldet",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- Panel-API-Token (Bearer, Prefix dnsmgr_usr_ – kein JWT) -------------------
    if token.startswith(PANEL_TOKEN_PREFIX):
        return await _authenticate_panel_token(request, db, token)

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

    set_auth_context(request, "session", None, None, username=user.username, client_ip=get_client_ip(request))
    iat = payload.get("iat")
    request.state.auth_time = iat if isinstance(iat, (int, float)) else None

    # Gate: erzwungener Passwortwechsel (nach der Session-Kennzeichnung, F2/F3 5.6)
    if getattr(user, "must_change_password", False) and not _password_change_allowed(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PASSWORD_CHANGE_REQUIRED_DETAIL,
            headers={PASSWORD_CHANGE_HEADER: "1"},
        )
    return user


async def get_session_user(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """Wie get_current_user, aber nur fuer echte Browser-Sessions.

    Credential-, Settings- und Token-Verwaltung darf nicht mit einem geleakten API-Token (oder
    einer anderen Token-Art) moeglich sein, sonst laesst sich dauerhafter Zugang verankern.
    Fail-closed: ohne gesetzten Kontext (``auth_via`` fehlt) gilt die Anfrage nicht als Session [S11].
    """
    if getattr(request.state, "auth_via", None) != "session":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SESSION_REQUIRED_DETAIL)
    return current_user


async def get_admin_session_user(
    current_user: User = Depends(get_session_user),
) -> User:
    """Admin UND echte Browser-Session (kein Token) – Benutzer-, Credential- und Settings-Verwaltung."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin-Rechte erforderlich")
    return current_user


# ---------------------------------------------------------------------------------------------
# Step-up (erneute Bestaetigung vor kritischen Aktionen, Bauplan B.3 [S8])
# ---------------------------------------------------------------------------------------------
STEP_UP_MAX_AGE_SECONDS = 600          # externe Konten: Anmeldung hoechstens 10 min alt
STEP_UP_HEADER = "X-Step-Up-Required"
STEP_UP_REQUIRED_DETAIL = "Bitte zur Bestätigung das aktuelle Passwort eingeben"
STEP_UP_TOTP_REQUIRED_DETAIL = "Bitte zur Bestätigung das aktuelle Passwort und den 2FA-Code eingeben"
STEP_UP_FAILED_DETAIL = "Bestätigung fehlgeschlagen – Passwort oder 2FA-Code ist falsch"
STEP_UP_TOTP_UNREADABLE_DETAIL = (
    "Bestätigung nicht möglich: das 2FA-Geheimnis kann nicht entschlüsselt werden – bitte einen anderen Admin "
    "bitten, die 2FA zurückzusetzen"
)
STEP_UP_REAUTH_DETAIL = (
    "Bitte zur Bestätigung abmelden und erneut anmelden – die Anmeldung ist älter als 10 Minuten"
)
STEP_UP_RATE_LIMIT_DETAIL = "Zu viele fehlgeschlagene Anmeldeversuche. Bitte später erneut versuchen."


class StepUpBody(BaseModel):
    """Bestaetigungsfelder fuer kritische Aktionen (Bauplan B.3): lokale Konten Passwort (+ TOTP)."""

    current_password: Optional[str] = Field(None, max_length=128)
    totp_code: Optional[str] = Field(None, max_length=12)


def _step_up_error(code: str, message: str) -> HTTPException:
    """403 ``{"detail": {"message", "code"}}`` + Header ``X-Step-Up-Required`` (api.js: STEP_UP_CODES)."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"message": message, "code": code},
        headers={STEP_UP_HEADER: code},
    )


def totp_secret_state(user) -> str:
    """``ok`` | ``missing`` | ``unreadable`` des aktiven TOTP-Geheimnisses (F5 5.13, Plan [S4]).

    Auf dem Rohattribut pruefen: ``UnreadableSecret`` verhaelt sich wie "" und waere nach ``strip()`` nicht mehr
    erkennbar.
    """
    from app.core.secrets import is_unreadable

    raw = getattr(user, "totp_secret", None)
    if is_unreadable(raw):
        return "unreadable"
    return "ok" if (raw or "").strip() else "missing"


async def verify_step_up(db: AsyncSession, user, request: Request, body=None) -> str:
    """Erneute Bestaetigung vor kritischen Aktionen [S8]; Rueckgabe: angewandtes Verfahren fuer das Audit.

    - Lokales Konto: ``current_password`` ist Pflicht, bei aktiver 2FA zusaetzlich ``totp_code``. Fehlt etwas ->
      403 ``stepup_required``; falsch -> 403 ``stepup_failed``. Fehlversuche zaehlen in den Login-Zaehlern
      (IP und Benutzername, ``core.login_rate_limit``); ist das Paar gesperrt -> 429 ohne Passwortvergleich.
      Rueckgabe ``"password"``.
    - Externes Konto (``auth_source != local``, kein lokales Passwort): die Anmeldung dieser Sitzung
      (``request.state.auth_time`` = JWT-``iat``) darf hoechstens 10 Minuten alt sein, sonst 403
      ``reauth_required`` (Oberflaeche: abmelden, neu anmelden, zurueck zum Tab). Rueckgabe ``"fresh_session"``.

    ``body``: ``StepUpBody`` oder ein Objekt mit denselben Attributen (z. B. ``schemas.sso.SsoStepUpIn``).
    Nur fuer Browser-Sessions; ohne Session-Kontext gilt die Bestaetigung als nicht erbracht.
    """
    from app.core.login_rate_limit import clear_login_fails, is_login_rate_limited, record_failed_login

    if getattr(request.state, "auth_via", None) != "session":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SESSION_REQUIRED_DETAIL)
    if (getattr(user, "auth_source", None) or "local") != "local":
        auth_time = getattr(request.state, "auth_time", None)
        if not isinstance(auth_time, (int, float)) or isinstance(auth_time, bool) \
                or time.time() - float(auth_time) > STEP_UP_MAX_AGE_SECONDS:
            raise _step_up_error("reauth_required", STEP_UP_REAUTH_DETAIL)
        return "fresh_session"

    client_ip = get_client_ip(request) or "unknown"
    username = getattr(user, "username", None)
    totp_on = bool(getattr(user, "totp_enabled", False))
    password = getattr(body, "current_password", None) if body is not None else None
    code = (getattr(body, "totp_code", None) or "") if body is not None else ""
    code = code.strip().replace(" ", "")
    if not password:
        raise _step_up_error("stepup_required", STEP_UP_TOTP_REQUIRED_DETAIL if totp_on else STEP_UP_REQUIRED_DETAIL)
    if is_login_rate_limited(client_ip, username):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=STEP_UP_RATE_LIMIT_DETAIL)
    if not verify_password(password, getattr(user, "hashed_password", None) or ""):
        record_failed_login(client_ip, username)
        logger.info("Step-up fuer %s abgelehnt (Passwort)", username)
        raise _step_up_error("stepup_failed", STEP_UP_FAILED_DETAIL)
    if totp_on:
        state = totp_secret_state(user)
        if state != "ok":
            raise _step_up_error("stepup_failed", STEP_UP_TOTP_UNREADABLE_DETAIL)
        if not code:
            raise _step_up_error("stepup_required", STEP_UP_TOTP_REQUIRED_DETAIL)
        if not totp_verify_once(user.id, (user.totp_secret or "").strip(), code):
            record_failed_login(client_ip, username)
            logger.info("Step-up fuer %s abgelehnt (2FA-Code)", username)
            raise _step_up_error("stepup_failed", STEP_UP_FAILED_DETAIL)
    clear_login_fails(client_ip, username)
    return "password"


def assert_effective_admin(user) -> None:
    """403, wenn der Aufrufer keine Admin-Funktionen nutzen darf (gleiche Texte wie ``get_admin_user``).

    Fuer Endpunkte, die erst im Handler entscheiden (z. B. Server-Statistik); sonst ``get_admin_user``.
    """
    if getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_ONLY_DETAIL)
    scope = current_token_scope.get()
    if scope is not None and not scope.allow_admin:
        logger.info("Panel-Token %s ohne Admin-Freigabe an Admin-Endpunkt abgelehnt", scope.token_prefix)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TOKEN_NO_ADMIN_DETAIL)


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Admin-Endpunkt: Rolle ``admin`` und – bei Token-Zugriff – Freigabe ``allow_admin``."""
    assert_effective_admin(current_user)
    return current_user


async def assert_zone_access(
    db: AsyncSession,
    user: User,
    zone_id: str,
    *,
    write: bool = False,
) -> None:
    """Prueft den ANFRAGENDEN Prinzipal inkl. Token-Scope.

    Reihenfolge: Token-Scope und Methodenregel (``assert_token_scope``), dann Admin-Shortcut, dann
    ``UserZoneAccess`` (403 ohne Recht; bei ``write=True`` zusaetzlich bei Rolle ``read``).
    Fuer Rechtepruefungen ANDERER Benutzer (z. B. Webhook-Empfaenger) direkt ``UserZoneAccess``
    abfragen, nie diese Funktion.
    """
    assert_token_scope(zone_id, write=write)
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


async def has_zone_access(db: AsyncSession, user: User, zone_id: str, *, write: bool = False) -> bool:
    """Nicht werfende Variante von ``assert_zone_access`` (z. B. PTR: fehlendes Recht -> ueberspringen)."""
    try:
        await assert_zone_access(db, user, zone_id, write=write)
        return True
    except HTTPException as exc:
        if exc.status_code in (400, 403):
            return False
        raise


async def effective_zone_filter(db: AsyncSession, user: User) -> Optional[set[str]]:
    """Sichtbare Zonen des Aufrufers: ``None`` = keine Einschraenkung, sonst normalisierte Zonennamen.

    Admin ohne Token-Scope -> ``None``; Admin mit Scope -> Scope; Benutzer -> eigene Zonen
    (``UserZoneAccess``), bei Token-Scope die Schnittmenge.
    """
    scope = current_token_scope.get()
    user_zones: Optional[set[str]] = None
    if user.role != "admin":
        rows = await db.execute(select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == user.id))
        user_zones = {z for z in (_normalize_zone_name(r[0]) for r in rows.all()) if z}
    if scope is None or not scope.zone_limited:
        return user_zones
    if user_zones is None:
        return set(scope.zones)
    return user_zones & set(scope.zones)


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
