"""Panel-API-Token-Verwaltung (F14 3.2/3.3/3.5) – eigener Router seit 3.0 (Bauplan B.13, ROUTER_ORDER 27).

Die Pfade sind unveraendert gegenueber 2.4.1 (``/api/v1/auth/me/panel-tokens…``). Alle Routen verlangen eine
Browser-Session (``get_session_user``): mit einem Token lassen sich keine weiteren Tokens anlegen, auflisten
oder widerrufen. Bearbeiten (PUT) und die Admin-Sicht auf fremde Tokens folgen mit WS-F14-APP.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_session_user
from app.core.database import DbRead, DbWrite
from app.core.timeutil import iso_utc, utcnow
from app.models.models import User
from app.services.audit import write_audit

ROUTER_ORDER = 27

router = APIRouter(prefix="/auth", tags=["Panel-API-Token"])

CREATED_WARNING = "Dieser Token wird nur einmal angezeigt – bitte sicher speichern."


class PanelTokenCreate(BaseModel):
    """Anlage-Body (F14 3.3). Ohne neue Felder: alle Zonen, Lesen & Schreiben, kein Ablauf, keine Admin-Freigabe."""

    name: str = Field(..., min_length=1, max_length=100)
    scope_zones: Optional[list[Annotated[str, Field(max_length=255)]]] = Field(default=None, max_length=500)
    permission: Literal["manage", "read"] = "manage"
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)
    allow_admin: bool = False


async def _token_out_list(db, user: User, rows) -> list[dict]:
    from app.services import panel_token as ptk

    owner_zones = None if user.role == "admin" else await ptk.owner_zone_set(db, user.id)
    now = utcnow()
    return [ptk.serialize_token(r, owner_role=user.role, owner_zones=owner_zones, now=now) for r in rows]


@router.get("/me/panel-tokens")
async def list_panel_tokens(
    db: DbRead,
    current_user: User = Depends(get_session_user),
):
    """Eigene, nicht widerrufene Tokens (aktiv, pausiert, abgelaufen), neueste zuerst."""
    from app.services import panel_token as ptk

    rows = await ptk.list_tokens(db, current_user.id)
    return {
        "tokens": await _token_out_list(db, current_user, rows),
        "max_tokens": ptk.MAX_TOKENS_PER_USER,
        "max_expiry_days": ptk.MAX_EXPIRY_DAYS,
    }


@router.post("/me/panel-tokens", status_code=201)
async def create_panel_token(
    data: PanelTokenCreate,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Legt einen Token an; der Klartext steht genau einmal in der Antwort."""
    from app.services import panel_token as ptk

    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Bezeichnung darf nicht leer sein")
    try:
        zones = ptk.normalize_scope_zones(data.scope_zones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.allow_admin and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin-Funktionen kann nur ein Administrator freigeben")
    if data.allow_admin and zones is not None:
        raise HTTPException(
            status_code=400,
            detail="Admin-Funktionen sind nur für Tokens ohne Zonen-Beschränkung möglich",
        )
    if current_user.role != "admin" and zones is not None:
        owned = await ptk.owner_zone_set(db, current_user.id)
        for z in zones:
            if z not in owned:
                raise HTTPException(
                    status_code=403,
                    detail=f"Keine Berechtigung für Zone „{z}“ – ein Token darf nur eigene Zonen enthalten",
                )
    if await ptk.count_open_tokens(db, current_user.id) >= ptk.MAX_TOKENS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Maximal {ptk.MAX_TOKENS_PER_USER} API-Tokens pro Benutzer – bitte alte Tokens widerrufen",
        )
    expires_at = utcnow() + timedelta(days=data.expires_in_days) if data.expires_in_days else None
    row, plain = await ptk.create_token(
        db,
        current_user.id,
        name,
        scope_zones=zones,
        permission=data.permission,
        expires_at=expires_at,
        allow_admin=data.allow_admin,
    )
    out = (await _token_out_list(db, current_user, [row]))[0]
    await write_audit(
        db, "PANEL_TOKEN_CREATE", "user", current_user.username, user_id=current_user.id,
        details={
            "token_id": row.id,
            "name": row.name,
            "prefix": row.token_prefix,
            "scope_zones": zones,
            "permission": data.permission,
            "expires_at": iso_utc(row.expires_at),
            "allow_admin": bool(row.allow_admin),
        },
    )
    return {"token": out, "plaintext_token": plain, "warning": CREATED_WARNING}


@router.delete("/me/panel-tokens/{token_id}")
async def delete_panel_token(
    token_id: int,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Widerruft einen eigenen Token endgueltig (``revoked_at``); 404, wenn fremd/unbekannt/schon widerrufen."""
    from app.services import panel_token as ptk

    row = await ptk.revoke_token(db, current_user.id, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    await write_audit(
        db, "PANEL_TOKEN_DELETE", "user", current_user.username, user_id=current_user.id,
        details={"token_id": row.id, "name": row.name, "prefix": row.token_prefix},
    )
    return {"message": "Token widerrufen"}
