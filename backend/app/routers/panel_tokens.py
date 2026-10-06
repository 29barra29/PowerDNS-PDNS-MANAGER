"""Panel-API-Token-Verwaltung (F14 3.2-3.8) – eigener Router seit 3.0 (Bauplan B.13, ROUTER_ORDER 27).

Die Pfade der eigenen Tokens sind unveraendert gegenueber 2.4.1 (``/api/v1/auth/me/panel-tokens…``); neu sind
``PUT /auth/me/panel-tokens/{token_id}`` (Bezeichnung, Scope, Berechtigung, Ablauf, Admin-Freigabe, Pausieren)
und die Admin-Sicht auf die Tokens anderer Benutzer (``/auth/users/{user_id}/panel-tokens…``: lesen und
widerrufen, nie bearbeiten).

Alle Routen verlangen eine Browser-Session (``get_session_user`` bzw. ``get_admin_session_user``): mit einem
Token lassen sich keine weiteren Tokens anlegen, auflisten, aendern oder widerrufen. Antwortobjekt je Token ist
``PanelTokenOut`` aus ``services.panel_token.serialize_token`` (nie Hash oder Klartext). Webhook-Events gibt es
fuer die Token-Verwaltung nicht (kein DNS-Ereignis, F14 3.12).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth import get_admin_session_user, get_session_user, is_effective_admin
from app.core.database import DbRead, DbWrite
from app.core.timeutil import iso_utc, utcnow
from app.models.models import PanelToken, User
from app.schemas.panel_tokens import PanelTokenCreate, PanelTokenUpdate
from app.services.audit import write_audit

ROUTER_ORDER = 27

router = APIRouter(prefix="/auth", tags=["Panel-API-Token"])

__all__ = ["router", "PanelTokenCreate", "PanelTokenUpdate"]  # Schemas bleiben hier importierbar (Tests, 0b)

CREATED_WARNING = "Dieser Token wird nur einmal angezeigt – bitte sicher speichern."
NAME_EMPTY_DETAIL = "Bezeichnung darf nicht leer sein"
ADMIN_GRANT_DETAIL = "Admin-Funktionen kann nur ein Administrator freigeben"
ADMIN_NEEDS_ALL_ZONES_DETAIL = "Admin-Funktionen sind nur für Tokens ohne Zonen-Beschränkung möglich"
TOKEN_NOT_FOUND_DETAIL = "Token nicht gefunden"
USER_NOT_FOUND_DETAIL = "Benutzer nicht gefunden"


def _owner_is_admin(owner: User) -> bool:
    """Rolle des Token-BESITZERS fuer die Anzeige (``admin_effective``, ``inaccessible_zones``).

    Keine Rechtepruefung des Aufrufers: alle Routen hier sind Session-only, die Admin-Sicht prueft der Aufrufer
    ueber ``get_admin_session_user``.
    """
    return getattr(owner, "role", None) == "admin"  # static-ok: role-admin (Besitzer-Attribut, Anzeige)


async def _token_out_list(db, owner: User, rows: Iterable[PanelToken]) -> list[dict]:
    from app.services import panel_token as ptk

    rows = list(rows)
    is_admin_owner = _owner_is_admin(owner)
    owner_zones = None if is_admin_owner or not rows else await ptk.owner_zone_set(db, owner.id)
    now = utcnow()
    role = "admin" if is_admin_owner else (owner.role or "user")
    return [ptk.serialize_token(r, owner_role=role, owner_zones=owner_zones, now=now) for r in rows]


def _normalize_zones_or_400(zones: Optional[list[str]]) -> Optional[list[str]]:
    from app.services import panel_token as ptk

    try:
        return ptk.normalize_scope_zones(zones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _clean_name_or_400(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=NAME_EMPTY_DETAIL)
    return cleaned


async def _assert_own_zones(db, user: User, zones: Iterable[str]) -> None:
    """Nicht-Admin: jede Zone muss in den eigenen Zonenrechten stehen (Fehlertext nennt nur eigene Eingaben)."""
    from app.services import panel_token as ptk

    pending = sorted(set(zones))
    if not pending:
        return
    owned = await ptk.owner_zone_set(db, user.id)
    for z in pending:
        if z not in owned:
            raise HTTPException(
                status_code=403,
                detail=f"Keine Berechtigung für Zone „{z}“ – ein Token darf nur eigene Zonen enthalten",
            )


def _scope_of(row: PanelToken) -> Optional[list[str]]:
    from app.core.names import normalize_zone_name

    raw = row.scope_zones
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    return sorted({z for z in (normalize_zone_name(str(x)) for x in raw) if z})


async def _get_user_or_404(db, user_id: int) -> User:
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_DETAIL)
    return user


def _revoke_details(target: User, rows: list[PanelToken]) -> dict[str, Any]:
    return {
        "target_user_id": target.id,
        "count": len(rows),
        "token_ids": [r.id for r in rows],
        "names": [r.name for r in rows],
        "prefixes": [r.token_prefix for r in rows],
    }


# ---------------------------------------------------------------------------------------------
# Eigene Tokens
# ---------------------------------------------------------------------------------------------
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
    """Legt einen Token an; der Klartext steht genau einmal in der Antwort.

    ``allow_admin`` nur durch Administratoren und nur ohne Zonen-Scope; Nicht-Admins duerfen nur eigene Zonen
    eintragen. Ohne ``expires_in_days`` laeuft der Token nicht ab.
    """
    from app.services import panel_token as ptk

    name = _clean_name_or_400(data.name)
    zones = _normalize_zones_or_400(data.scope_zones)
    is_admin = is_effective_admin(current_user)
    if data.allow_admin and not is_admin:
        raise HTTPException(status_code=403, detail=ADMIN_GRANT_DETAIL)
    if data.allow_admin and zones is not None:
        raise HTTPException(status_code=400, detail=ADMIN_NEEDS_ALL_ZONES_DETAIL)
    if not is_admin and zones is not None:
        await _assert_own_zones(db, current_user, zones)
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


@router.put("/me/panel-tokens/{token_id}")
async def update_panel_token(
    token_id: int,
    data: PanelTokenUpdate,
    db: DbWrite,
    current_user: User = Depends(get_session_user),
):
    """Aendert einen eigenen Token (F14 3.4); der Klartext bleibt gueltig, es wird nie ein neuer Token erzeugt.

    Nur Felder im Request werden beruecksichtigt (``scope_zones: null`` = alle Zonen, ``expires_in_days: null`` =
    kein Ablauf, eine neue Laufzeit zaehlt ab jetzt). Die Kombination Admin-Freigabe + Zonen-Scope wird auf dem
    Zielzustand geprueft. Ohne Aenderung: 200 "Keine Änderungen" ohne Audit.
    """
    from app.services import panel_token as ptk

    row = await ptk.get_open_token(db, current_user.id, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail=TOKEN_NOT_FOUND_DETAIL)
    is_admin = is_effective_admin(current_user)

    old_scope = _scope_of(row)
    new_name = row.name
    new_active = bool(row.is_active)
    new_scope = old_scope
    old_perm = row.permission if row.permission in ptk.PERMISSIONS else "read"  # wie serialize_token
    new_perm = old_perm
    new_allow_admin = bool(row.allow_admin)
    new_expires = row.expires_at
    scope_given = data.given("scope_zones")
    expires_given = data.given("expires_in_days")

    if data.name is not None:
        new_name = _clean_name_or_400(data.name)
    if data.is_active is not None:
        new_active = bool(data.is_active)
    if scope_given:
        new_scope = _normalize_zones_or_400(data.scope_zones)
    if data.permission is not None:
        new_perm = data.permission
    if expires_given:
        new_expires = utcnow() + timedelta(days=data.expires_in_days) if data.expires_in_days else None
    if data.allow_admin is not None:
        if data.allow_admin and not is_admin:
            raise HTTPException(status_code=403, detail=ADMIN_GRANT_DETAIL)
        new_allow_admin = bool(data.allow_admin)

    if new_allow_admin and new_scope is not None:
        raise HTTPException(status_code=400, detail=ADMIN_NEEDS_ALL_ZONES_DETAIL)
    if not is_admin and scope_given and new_scope is not None:
        # Nur neu hinzugekommene Zonen pruefen; unveraenderte Altzonen bleiben (UI: "kein Zugriff mehr").
        await _assert_own_zones(db, current_user, set(new_scope) - set(old_scope or []))

    changed: dict[str, dict[str, Any]] = {}
    if new_name != row.name:
        changed["name"] = {"from": row.name, "to": new_name}
    if new_active != bool(row.is_active):
        changed["is_active"] = {"from": bool(row.is_active), "to": new_active}
    if new_scope != old_scope:
        changed["scope_zones"] = {"from": old_scope, "to": new_scope}
    if new_perm != old_perm:
        changed["permission"] = {"from": old_perm, "to": new_perm}
    if new_allow_admin != bool(row.allow_admin):
        changed["allow_admin"] = {"from": bool(row.allow_admin), "to": new_allow_admin}
    if iso_utc(new_expires) != iso_utc(row.expires_at):
        changed["expires_at"] = {"from": iso_utc(row.expires_at), "to": iso_utc(new_expires)}

    if not changed:
        out = (await _token_out_list(db, current_user, [row]))[0]
        return {"message": "Keine Änderungen", "token": out}

    old_name, prefix = row.name, row.token_prefix
    if "name" in changed:
        row.name = new_name
    if "is_active" in changed:
        row.is_active = new_active
    if "scope_zones" in changed:
        row.scope_zones = list(new_scope) if new_scope is not None else None
        flag_modified(row, "scope_zones")
    if "permission" in changed:
        row.permission = new_perm
    if "allow_admin" in changed:
        row.allow_admin = new_allow_admin
    if "expires_at" in changed:
        row.expires_at = new_expires
    await db.flush()

    out = (await _token_out_list(db, current_user, [row]))[0]
    await write_audit(
        db, "PANEL_TOKEN_UPDATE", "user", current_user.username, user_id=current_user.id,
        details={"token_id": row.id, "name": old_name, "prefix": prefix, "changed": changed},
    )
    return {"message": "Token gespeichert", "token": out}


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
        raise HTTPException(status_code=404, detail=TOKEN_NOT_FOUND_DETAIL)
    await write_audit(
        db, "PANEL_TOKEN_DELETE", "user", current_user.username, user_id=current_user.id,
        details={"token_id": row.id, "name": row.name, "prefix": row.token_prefix},
    )
    return {"message": "Token widerrufen"}


# ---------------------------------------------------------------------------------------------
# Admin-Sicht auf die Tokens anderer Benutzer (lesen + widerrufen, F14 3.6-3.8)
# ---------------------------------------------------------------------------------------------
@router.get("/users/{user_id}/panel-tokens")
async def list_user_panel_tokens(
    user_id: int,
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Nicht widerrufene Tokens eines Benutzers (aktiv, pausiert, abgelaufen) – nur Admin mit Browser-Session."""
    from app.services import panel_token as ptk

    target = await _get_user_or_404(db, user_id)
    rows = await ptk.list_tokens(db, target.id)
    return {
        "user_id": target.id,
        "username": target.username,
        "tokens": await _token_out_list(db, target, rows),
    }


@router.delete("/users/{user_id}/panel-tokens/{token_id}")
async def revoke_user_panel_token(
    user_id: int,
    token_id: int,
    db: DbWrite,
    admin: User = Depends(get_admin_session_user),
):
    """Widerruft einen Token eines anderen (oder des eigenen) Kontos endgueltig; Audit ``PANEL_TOKEN_ADMIN_REVOKE``."""
    from app.services import panel_token as ptk

    target = await _get_user_or_404(db, user_id)
    row = await ptk.revoke_token(db, target.id, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail=TOKEN_NOT_FOUND_DETAIL)
    await write_audit(
        db, "PANEL_TOKEN_ADMIN_REVOKE", "user", target.username, user_id=admin.id,
        details=_revoke_details(target, [row]),
    )
    return {"message": "Token widerrufen"}


@router.delete("/users/{user_id}/panel-tokens")
async def revoke_all_user_panel_tokens(
    user_id: int,
    db: DbWrite,
    admin: User = Depends(get_admin_session_user),
):
    """Widerruft alle nicht widerrufenen Tokens eines Benutzers (auch pausierte/abgelaufene); Audit nur bei n > 0."""
    from app.services import panel_token as ptk

    target = await _get_user_or_404(db, user_id)
    rows = await ptk.revoke_all_for_user(db, target.id)
    if rows:
        await write_audit(
            db, "PANEL_TOKEN_ADMIN_REVOKE", "user", target.username, user_id=admin.id,
            details=_revoke_details(target, rows),
        )
    n = len(rows)
    return {"message": f"{n} Token widerrufen", "revoked": n}
