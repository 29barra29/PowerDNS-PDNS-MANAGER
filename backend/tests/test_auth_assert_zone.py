import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from app.core.auth import assert_zone_access
from app.models.models import User


def _user(role: str) -> User:
    u = User(
        id=1,
        username="u1",
        email=None,
        hashed_password="x",
        display_name="U",
        role=role,
        is_active=True,
    )
    return u


@pytest.mark.asyncio
async def test_admin_always_ok():
    db = AsyncMock()
    u = _user("admin")
    await assert_zone_access(db, u, "example.com.", write=True)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_user_write_blocks_when_read():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "read"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(HTTPException) as e:
        await assert_zone_access(db, u, "example.com", write=True)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_user_write_ok_when_manage():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "manage"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    await assert_zone_access(db, u, "EXAMPLE.com", write=True)


@pytest.mark.asyncio
async def test_user_read_ok_when_read():
    u = _user("user")
    res = MagicMock()
    res.scalar_one_or_none.return_value = "read"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    await assert_zone_access(db, u, "example.com.", write=False)
