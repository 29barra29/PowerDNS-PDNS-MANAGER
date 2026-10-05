"""Login-Abschluss und User-JSON (F10 5.1, Plan B.4).

Aus ``routers/auth.py`` herausgeloest, damit ``setup.py`` und ``routers/sso.py`` den
Login-Abschluss ohne Router-Import nutzen koennen. ``routers/auth.py`` behaelt die
Aliase ``_user_to_dict``, ``_set_session_cookie`` und ``_complete_login``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as prom
from app.core.auth import create_access_token
from app.core.client_ip import get_client_ip
from app.core.config import settings
from app.core.login_rate_limit import clear_login_fails
from app.core.request_context import actor_username_ctx, client_ip_ctx
from app.core.timeutil import iso_utc
from app.models.models import User, UserZoneAccess
from app.services.audit import write_audit

OIDC_STATE_COOKIE = "pdnsmgr_oidc"
OIDC_STATE_COOKIE_PATH = "/api/v1/auth/oidc"
TWO_FACTOR_COOKIE = "pdnsmgr_2fa"
TWO_FACTOR_COOKIE_PATH = "/api/v1/auth/login/2fa"

# Methoden des Login-Abschlusses (Audit details.method)
LOGIN_METHODS = ("password", "password+totp", "passkey", "ldap", "ldap+totp", "oidc", "oidc+totp", "setup")


async def user_to_dict(user: User, db: AsyncSession) -> dict:
    """User-JSON fuer ``/auth/me`` und alle Antworten mit User-Objekt.

    Gegenueber 2.4.1 zusaetzlich: ``must_change_password`` (F3), ``auth_source`` (F10,
    Default ``"local"``), ``totp_unreadable`` (F5: 2FA aktiv, Geheimnis nicht entschluesselbar).
    """
    from app.core.secrets import is_unreadable  # lazy, F5-Kern aus W0-SECRETS

    result = await db.execute(
        select(UserZoneAccess.zone_name, UserZoneAccess.permission).where(
            UserZoneAccess.user_id == user.id
        )
    )
    rows = result.all()
    zones = [row[0] for row in rows]
    zone_permissions = {row[0]: (row[1] or "manage") for row in rows}
    totp_enabled = bool(getattr(user, "totp_enabled", False))

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "zones": zones,
        "zone_permissions": zone_permissions,
        "created_at": iso_utc(user.created_at),
        "last_login": iso_utc(user.last_login),
        "phone": getattr(user, "phone", None),
        "company": getattr(user, "company", None),
        "street": getattr(user, "street", None),
        "postal_code": getattr(user, "postal_code", None),
        "city": getattr(user, "city", None),
        "country": getattr(user, "country", None),
        "date_of_birth": user.date_of_birth.isoformat()[:10] if getattr(user, "date_of_birth", None) else None,
        "preferred_language": getattr(user, "preferred_language", None) or None,
        "totp_enabled": totp_enabled,
        "totp_unreadable": totp_enabled and is_unreadable(getattr(user, "totp_secret", None)),
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "auth_source": getattr(user, "auth_source", None) or "local",
    }


def set_session_cookie(response: Response, token: str) -> Response:
    """Setzt das HttpOnly-Session-Cookie (Parameter identisch zu 2.4.1)."""
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=settings.AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return response


def transient_cookie_secure(base_url: Optional[str]) -> bool:
    """Kurzlebige Flow-Cookies (OIDC-State, 2FA) mit ``Secure``, wenn global erzwungen oder die Basis-URL https ist."""
    return bool(settings.AUTH_COOKIE_SECURE) or (base_url or "").strip().lower().startswith("https://")


def set_transient_cookie(response: Response, name: str, value: str, *, path: str, max_age: int, secure: bool) -> None:
    """HttpOnly-Cookie mit festem ``SameSite=lax`` und engem Pfad (OIDC-State, 2FA-Zwischenschritt)."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=int(max_age),
        httponly=True,
        secure=bool(secure),
        samesite="lax",
        path=path,
    )


def delete_transient_cookie(response: Response, name: str, *, path: str) -> None:
    response.delete_cookie(key=name, path=path, httponly=True, samesite="lax")


def login_metric_method(method: str) -> str:
    """Audit-Methode -> Metrik-Label: ``*+totp`` zaehlt als ``totp`` (letzter Faktor), sonst die Basis."""
    m = (method or "").strip().lower()
    if m.endswith("+totp"):
        return "totp"
    return m.split("+", 1)[0]


async def complete_login(
    db: AsyncSession,
    user: User,
    request: Request,
    *,
    method: str,
    response: Optional[Response] = None,
    status_code: int = 200,
    body_extra: Optional[dict] = None,
    audit_extra: Optional[dict] = None,
    clear_fails: bool = True,
) -> Response:
    """Schliesst eine erfolgreiche Anmeldung ab.

    ``last_login`` setzen, Benutzer-Fehlzaehler des Paars (IP, Benutzername) loeschen,
    Audit ``LOGIN`` (``details.ip``, ``details.method`` + ``audit_extra``), Metrik
    ``login_attempts{result="success"}``, Session-Cookie setzen. Ohne ``response`` wird
    eine JSONResponse ``{**body_extra, "user": user_to_dict(...)}`` erzeugt; eine
    uebergebene Response (z. B. RedirectResponse) wird durchgereicht.
    """
    client_ip = get_client_ip(request) or "unknown"
    user.last_login = datetime.now(timezone.utc)
    await db.flush()
    if clear_fails:
        clear_login_fails(client_ip, user.username)
    # Audit-Kontext: der Anmeldende ist der Akteur dieses Requests
    if actor_username_ctx.get() is None:
        actor_username_ctx.set(user.username)
    if client_ip_ctx.get() is None:
        client_ip_ctx.set(client_ip)
    details = {"ip": client_ip, "method": method, **(audit_extra or {})}
    await write_audit(db, "LOGIN", "user", user.username, user_id=user.id, details=details)
    prom.record_login(login_metric_method(method), "success")
    token = create_access_token(data={"sub": str(user.id), "role": user.role}, user=user)
    if response is None:
        response = JSONResponse(
            status_code=status_code,
            content={**(body_extra or {}), "user": await user_to_dict(user, db)},
        )
    return set_session_cookie(response, token)
