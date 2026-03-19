from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


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
            "ALTER TABLE users ADD COLUMN phone VARCHAR(50)",
            "ALTER TABLE users ADD COLUMN company VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN street VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN postal_code VARCHAR(20)",
            "ALTER TABLE users ADD COLUMN city VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN country VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN date_of_birth DATETIME",
            "ALTER TABLE users ADD COLUMN preferred_language VARCHAR(10)",
        ]:
            try:
                await session.execute(text(stmt))
                await session.commit()
            except Exception:
                await session.rollback()
                # Spalte existiert bereits – ignorieren
                pass
