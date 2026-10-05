"""Panel-API-Tokens: Bearer-Zugang zur Panel-API, optional auf Zonen, Leserecht und Ablauf beschraenkt (F14).

Datenhaltung: nur SHA-256 und ein 16-Zeichen-Praefix (``dnsmgr_usr_AbCd…``) – der Klartext wird genau
einmal bei der Anlage ausgegeben. Zustaende (``token_status``): ``active``, ``paused`` (``is_active = 0``),
``expired`` (``expires_at`` erreicht) und ``revoked`` (``revoked_at`` gesetzt, endgueltig, nie ausgegeben).
Pausiert hat Vorrang vor abgelaufen. Alle Zeitstempel sind naive UTC (``core.timeutil.utcnow``).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth import PANEL_TOKEN_PREFIX
from app.core.names import normalize_zone_name
from app.core.timeutil import iso_utc, to_naive_utc, utcnow
from app.models.models import PanelToken, UserZoneAccess

PERMISSIONS = ("manage", "read")
MAX_TOKENS_PER_USER = 50
MAX_SCOPE_ZONES = 500
MAX_EXPIRY_DAYS = 3650
LAST_USED_THROTTLE = timedelta(seconds=60)
# Zonenname nach Normalisierung (ASCII/IDNA, lower, Trailing-Dot); keine Wildcards, kein Leerzeichen, kein "/".
ZONE_NAME_RE = re.compile(r"^(?=.{2,254}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+$")

_TOKEN_BYTES = 32
_MAX_IP_LEN = 64


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _token_prefix(plaintext: str) -> str:
    return (plaintext[:16] if len(plaintext) >= 16 else plaintext) + "…"


# ---------------------------------------------------------------------------------------------
# Reine Funktionen
# ---------------------------------------------------------------------------------------------
def normalize_scope_zones(zones: Optional[Iterable[str]]) -> Optional[list[str]]:
    """Normalisiert eine Scope-Liste: strip, IDNA, lower, Trailing-Dot, dedupliziert, sortiert.

    ``None`` bleibt ``None`` (= alle Zonen des Besitzers). Ungueltige Namen, eine leere Liste oder
    mehr als ``MAX_SCOPE_ZONES`` Zonen -> ``ValueError`` mit deutschem Text.
    """
    if zones is None:
        return None
    out: set[str] = set()
    for raw in zones:
        original = "" if raw is None else str(raw)
        z = original.strip()
        if not z:
            raise ValueError(f"Ungültiger Zonenname: {original}")
        if not z.isascii():
            try:
                z = z.rstrip(".").encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(f"Ungültiger Zonenname: {original}") from exc
        z = normalize_zone_name(z)
        if not ZONE_NAME_RE.match(z):
            raise ValueError(f"Ungültiger Zonenname: {original}")
        out.add(z)
    if not out:
        raise ValueError(
            "Mindestens eine Zone angeben – oder scope_zones weglassen bzw. null senden (= alle Zonen)"
        )
    if len(out) > MAX_SCOPE_ZONES:
        raise ValueError(f"Maximal {MAX_SCOPE_ZONES} Zonen pro Token")
    return sorted(out)


def token_status(row: PanelToken, now: datetime) -> str:
    """``revoked`` | ``paused`` | ``expired`` | ``active`` (pausiert vor abgelaufen)."""
    if row.revoked_at is not None:
        return "revoked"
    if not row.is_active:
        return "paused"
    if row.expires_at is not None and to_naive_utc(row.expires_at) <= to_naive_utc(now):
        return "expired"
    return "active"


def touch_last_used(row: PanelToken, ip: Optional[str], now: datetime) -> bool:
    """Setzt ``last_used_at``/``last_used_ip`` gedrosselt (60 s oder IP-Wechsel); True = geaendert.

    Skripte mit hoher Frequenz erzeugen so keine Schreiblast/Row-Locks (F14 7.8).
    """
    ip_val = (ip or "")[:_MAX_IP_LEN] or None
    last = to_naive_utc(row.last_used_at)
    now_n = to_naive_utc(now)
    due = last is None or (now_n - last) >= LAST_USED_THROTTLE
    ip_changed = bool(ip_val) and ip_val != row.last_used_ip
    if not (due or ip_changed):
        return False
    row.last_used_at = now_n
    if ip_val:
        row.last_used_ip = ip_val
    return True


def _scope_list(row: PanelToken) -> Optional[list[str]]:
    raw = row.scope_zones
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []  # ungueltiges Format = kein Zonenzugriff (wie core.auth.scope_from_token)
    return sorted({z for z in (normalize_zone_name(str(x)) for x in raw) if z})


def serialize_token(
    row: PanelToken,
    *,
    owner_role: str,
    owner_zones: Optional[set[str]],
    now: datetime,
) -> dict:
    """``PanelTokenOut`` (F14 3.0) – nie Hash oder Klartext.

    ``inaccessible_zones``: nur bei Nicht-Admin-Besitzern mit Zonen-Scope die Zonen ohne aktuelles
    ``UserZoneAccess``; sonst ``[]``. ``admin_effective`` = ``allow_admin`` und Besitzer ist Admin.
    """
    scope = _scope_list(row)
    perm = (row.permission or "manage").strip().lower()
    if perm not in PERMISSIONS:
        perm = "read"
    inaccessible: list[str] = []
    if owner_role != "admin" and scope is not None:
        inaccessible = sorted(set(scope) - set(owner_zones or set()))
    status = token_status(row, now)
    return {
        "id": row.id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "created_at": iso_utc(row.created_at),
        "last_used_at": iso_utc(row.last_used_at),
        "last_used_ip": row.last_used_ip,
        "is_active": bool(row.is_active),
        "status": status,
        "expires_at": iso_utc(row.expires_at),
        "scope_zones": scope,
        "permission": perm,
        "allow_admin": bool(row.allow_admin),
        "admin_effective": bool(row.allow_admin) and owner_role == "admin",
        "inaccessible_zones": inaccessible,
    }


# ---------------------------------------------------------------------------------------------
# DB-Zugriffe
# ---------------------------------------------------------------------------------------------
async def owner_zone_set(db: AsyncSession, user_id: int) -> set[str]:
    """Normalisierte Zonennamen aus ``UserZoneAccess`` des Benutzers."""
    rows = await db.execute(select(UserZoneAccess.zone_name).where(UserZoneAccess.user_id == user_id))
    return {z for z in (normalize_zone_name(r[0]) for r in rows.all()) if z}


async def list_tokens(db: AsyncSession, user_id: int) -> List[PanelToken]:
    """Nicht widerrufene Tokens (aktiv, pausiert, abgelaufen), neueste zuerst."""
    r = await db.execute(
        select(PanelToken)
        .where(PanelToken.user_id == user_id, PanelToken.revoked_at.is_(None))
        .order_by(PanelToken.id.desc())
    )
    return list(r.scalars().all())


async def count_open_tokens(db: AsyncSession, user_id: int) -> int:
    """Anzahl nicht widerrufener Tokens (Grenze ``MAX_TOKENS_PER_USER``)."""
    r = await db.execute(
        select(func.count(PanelToken.id)).where(PanelToken.user_id == user_id, PanelToken.revoked_at.is_(None))
    )
    return int(r.scalar() or 0)


async def count_active_by_user(db: AsyncSession) -> dict[int, int]:
    """``{user_id: n}`` der benutzbaren Tokens (nicht widerrufen, aktiv, nicht abgelaufen) – eine Abfrage."""
    now = utcnow()
    r = await db.execute(
        select(PanelToken.user_id, func.count(PanelToken.id))
        .where(
            PanelToken.revoked_at.is_(None),
            PanelToken.is_active.is_(True),
            or_(PanelToken.expires_at.is_(None), PanelToken.expires_at > now),
        )
        .group_by(PanelToken.user_id)
    )
    return {int(uid): int(n) for uid, n in r.all()}


async def get_open_token(db: AsyncSession, user_id: int, token_id: int) -> Optional[PanelToken]:
    """Eigener, nicht widerrufener Token oder ``None``."""
    r = await db.execute(
        select(PanelToken).where(
            PanelToken.id == token_id,
            PanelToken.user_id == user_id,
            PanelToken.revoked_at.is_(None),
        )
    )
    return r.scalar_one_or_none()


async def create_token(
    db: AsyncSession,
    user_id: int,
    name: str,
    *,
    scope_zones: Optional[list[str]] = None,
    permission: str = "manage",
    expires_at: Optional[datetime] = None,
    allow_admin: bool = False,
) -> Tuple[PanelToken, str]:
    """Legt einen Token an (Praefix ``dnsmgr_usr_`` + 256 Bit Zufall) und liefert ``(row, klartext)``.

    Gespeichert werden nur SHA-256 und das Anzeige-Praefix. ``scope_zones`` muss bereits
    normalisiert sein (``normalize_scope_zones``).
    """
    if permission not in PERMISSIONS:
        raise ValueError("permission muss manage oder read sein")
    body = secrets.token_urlsafe(_TOKEN_BYTES)
    plaintext = f"{PANEL_TOKEN_PREFIX}{body}"
    row = PanelToken(
        user_id=user_id,
        name=name[:100],
        token_prefix=_token_prefix(plaintext),
        token_hash=_hash_token(plaintext),
        created_at=utcnow(),
        is_active=True,
        scope_zones=list(scope_zones) if scope_zones is not None else None,
        permission=permission,
        expires_at=to_naive_utc(expires_at),
        allow_admin=bool(allow_admin),
        revoked_at=None,
    )
    db.add(row)
    await db.flush()
    return row, plaintext


async def revoke_token(db: AsyncSession, user_id: int, token_id: int) -> Optional[PanelToken]:
    """Widerruft einen eigenen Token endgueltig (``is_active=False``, ``revoked_at=jetzt``).

    ``None``, wenn der Token fremd, unbekannt oder bereits widerrufen ist.
    """
    row = await get_open_token(db, user_id, token_id)
    if row is None:
        return None
    row.is_active = False
    row.revoked_at = utcnow()
    await db.flush()
    return row


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> List[PanelToken]:
    """Widerruft alle nicht widerrufenen Tokens des Benutzers (auch pausierte/abgelaufene)."""
    rows = await list_tokens(db, user_id)
    if not rows:
        return []
    now = utcnow()
    for row in rows:
        row.is_active = False
        row.revoked_at = now
    await db.flush()
    return rows


async def remove_zone_from_scopes(db: AsyncSession, zone_name: str) -> int:
    """Entfernt eine (endgueltig geloeschte) Zone aus allen Token-Scopes; liefert die Anzahl geaenderter Tokens.

    Eine dadurch leer gewordene Liste bleibt ``[]`` (= keine Zone), nie ``NULL`` (= alle Zonen).
    """
    zname = normalize_zone_name(zone_name)
    if not zname:
        return 0
    r = await db.execute(
        select(PanelToken).where(PanelToken.revoked_at.is_(None), PanelToken.scope_zones.is_not(None))
    )
    changed = 0
    for row in r.scalars().all():
        raw = row.scope_zones
        if not isinstance(raw, list):
            continue
        kept = [z for z in raw if normalize_zone_name(str(z)) != zname]
        if len(kept) != len(raw):
            row.scope_zones = kept
            flag_modified(row, "scope_zones")
            changed += 1
    if changed:
        await db.flush()
    return changed
