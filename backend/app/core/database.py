from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency to get async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migrationen: neue Spalten (falls Tabellen schon existieren)
    from sqlalchemy import text
    async with async_session() as session:
        for stmt in [
            "ALTER TABLE server_configs ADD COLUMN allow_writes TINYINT(1) DEFAULT 1",
            "ALTER TABLE user_zone_access ADD COLUMN permission VARCHAR(20) DEFAULT 'manage'",
            "ALTER TABLE users ADD COLUMN phone VARCHAR(50)",
            "ALTER TABLE users ADD COLUMN company VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN street VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN postal_code VARCHAR(20)",
            "ALTER TABLE users ADD COLUMN city VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN country VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN date_of_birth DATETIME",
            "ALTER TABLE users ADD COLUMN preferred_language VARCHAR(10)",
            "ALTER TABLE users ADD COLUMN totp_enabled TINYINT(1) DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)",
            "ALTER TABLE users ADD COLUMN totp_pending_secret VARCHAR(64)",
        ]:
            try:
                await session.execute(text(stmt))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                msg = str(exc).lower()
                # Spalte existiert bereits – erwartbar bei bestehenden Installationen.
                if "duplicate column" in msg or "1060" in msg:
                    continue
                logger.warning("DB migration statement skipped after error: %s; error=%s", stmt, exc)
