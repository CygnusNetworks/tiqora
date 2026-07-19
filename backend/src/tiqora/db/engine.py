"""Async SQLAlchemy engine factory (PostgreSQL via asyncpg, MySQL via aiomysql)."""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tiqora.config import Settings, get_settings


def _normalize_url(url: str) -> str:
    """Ensure the URL uses an asyncio-compatible SQLAlchemy driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+aiomysql://", 1)
    if url.startswith("mariadb://"):
        return url.replace("mariadb://", "mysql+aiomysql://", 1)
    return url


@lru_cache
def get_engine(database_url: str | None = None) -> AsyncEngine:
    """Create (and cache) an async engine for the configured database URL."""
    settings = get_settings()
    url = _normalize_url(database_url or settings.database_url)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        echo=settings.debug,
    )


def get_session_factory(
    engine: AsyncEngine | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the given or default engine."""
    eng = engine or get_engine()
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped async session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def check_database(settings: Settings | None = None) -> bool:
    """Return True if a simple connectivity probe succeeds."""
    from sqlalchemy import text

    cfg = settings or get_settings()
    engine = get_engine(cfg.database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — readiness probe must never raise
        return False
