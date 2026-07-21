"""DB tests for the tiqora_cache_invalidation writer."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.znuny.cache_invalidation import invalidate_cache_type, invalidate_ticket_cache


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _ensure_table(engine: AsyncEngine) -> None:
    """Create/align tiqora_cache_invalidation with the current model columns."""
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: TiqoraCacheInvalidation.__table__.create(sync_conn, checkfirst=True)
        )
        # Reused testcontainers may still hold the pre-cache_type schema.
        for alter in (
            "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
            "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
        ):
            with contextlib.suppress(Exception):
                await conn.execute(text(alter))


@pytest.mark.db
async def test_invalidate_writes_row(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _ensure_table(engine)

        async with factory() as session:
            await invalidate_ticket_cache(session, ticket_id=42)
            await invalidate_ticket_cache(session, ticket_id=42)
            await session.commit()

            rows = (
                await session.execute(
                    text(
                        "SELECT id, ticket_id, cache_type, created FROM tiqora_cache_invalidation"
                        " WHERE ticket_id = 42 ORDER BY id"
                    )
                )
            ).fetchall()
        assert len(rows) == 2
        # ids are monotonically increasing (bigint autoincrement pk)
        assert int(rows[1][0]) > int(rows[0][0])
        assert all(r[1] == 42 for r in rows)
        assert all(r[2] is None for r in rows)
        assert all(r[3] is not None for r in rows)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_invalidate_cache_type_writes_row(mariadb_znuny_url: str) -> None:
    """Master-data signal: cache_type set, ticket_id NULL."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _ensure_table(engine)

        async with factory() as session:
            await invalidate_cache_type(session, "Queue")
            await invalidate_cache_type(session, "  DynamicField  ")
            await invalidate_cache_type(session, "")  # ignored
            await invalidate_cache_type(session, "   ")  # ignored
            await session.commit()

            rows = (
                await session.execute(
                    text(
                        "SELECT ticket_id, cache_type FROM tiqora_cache_invalidation"
                        " WHERE cache_type IS NOT NULL ORDER BY id"
                    )
                )
            ).fetchall()

        # At least our two signals (table may retain rows from prior tests).
        types = {r[1] for r in rows}
        assert "Queue" in types
        assert "DynamicField" in types
        for ticket_id, cache_type in rows:
            if cache_type in ("Queue", "DynamicField"):
                assert ticket_id is None
    finally:
        await engine.dispose()
