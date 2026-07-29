"""Async SQLAlchemy engine factory (PostgreSQL via asyncpg, MySQL via aiomysql)."""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

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


def _utc_connect_args(url: str) -> dict[str, Any]:
    """Force every DB connection's session timezone to UTC.

    Znuny's contract is that all stored timestamps are UTC (``OTRSTimeZone`` =
    UTC). Our INSERT/UPDATE statements use SQL ``current_timestamp``, which
    resolves to the *session* timezone — MariaDB defaults to ``SYSTEM`` (the
    container's local tz, e.g. CEST), so without this Tiqora-written rows would
    land in local time, 1–2h off from Znuny-written rows. Pinning the session
    to UTC keeps every writer consistent.
    """
    if url.startswith("mysql+aiomysql://"):
        return {"init_command": "SET time_zone = '+00:00'"}
    if url.startswith("postgresql+asyncpg://"):
        return {"server_settings": {"timezone": "UTC"}}
    return {}


@lru_cache
def get_engine(database_url: str | None = None) -> AsyncEngine:
    """Create (and cache) an async engine for the configured database URL."""
    settings = get_settings()
    url = _normalize_url(database_url or settings.database_url)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        # Below MariaDB's default wait_timeout (8h): idle connections in low-traffic
        # periods (e.g. overnight) get server-closed past that point, and pre_ping's
        # validation ping then hits a dead transport, raising a RuntimeError that
        # SQLAlchemy's aiomysql dialect doesn't recognize as "stale, discard & retry" —
        # it propagates as an unhandled 500 instead. Recycling proactively avoids this.
        pool_recycle=1800,
        echo=settings.debug,
        connect_args=_utc_connect_args(url),
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
