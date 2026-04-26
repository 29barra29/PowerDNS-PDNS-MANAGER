"""Panel-API-Token: voller Zugriff wie Session-JWT (nicht an Zonen gescoped)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import PANEL_TOKEN_PREFIX
from app.models.models import PanelToken

_TOKEN_BYTES = 32


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _token_prefix(plaintext: str) -> str:
    return (plaintext[:16] if len(plaintext) >= 16 else plaintext) + "…"


async def list_tokens(db: AsyncSession, user_id: int) -> List[PanelToken]:
    r = await db.execute(
        select(PanelToken)
        .where(PanelToken.user_id == user_id, PanelToken.is_active.is_(True))
        .order_by(PanelToken.id.desc())
    )
    return list(r.scalars().all())


async def create_token(db: AsyncSession, user_id: int, name: str) -> Tuple[PanelToken, str]:
    body = secrets.token_urlsafe(_TOKEN_BYTES)
    plaintext = f"{PANEL_TOKEN_PREFIX}{body}"
    row = PanelToken(
        user_id=user_id,
        name=name[:100],
        token_prefix=_token_prefix(plaintext),
        token_hash=_hash_token(plaintext),
    )
    db.add(row)
    await db.flush()
    return row, plaintext


async def delete_token(db: AsyncSession, user_id: int, token_id: int) -> bool:
    r = await db.execute(
        select(PanelToken).where(PanelToken.id == token_id, PanelToken.user_id == user_id)
    )
    row = r.scalar_one_or_none()
    if not row:
        return False
    row.is_active = False
    await db.flush()
    return True
