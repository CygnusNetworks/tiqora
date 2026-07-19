"""DB test for the tiqora_cache_invalidation writer."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


@pytest.mark.db
async def test_invalidate_writes_row(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Create the tiqora_* table (normally applied by the Alembic
        # versions_tiqora chain; the Znuny fixture schema does not include it).
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: TiqoraCacheInvalidation.__table__.create(
                    sync_conn, checkfirst=True
                )
            )

        async with factory() as session:
            await invalidate_ticket_cache(session, ticket_id=42)
            await invalidate_ticket_cache(session, ticket_id=42)
            await session.commit()

            rows = (
                await session.execute(
                    text(
                        "SELECT id, ticket_id, created FROM tiqora_cache_invalidation"
                        " WHERE ticket_id = 42 ORDER BY id"
                    )
                )
            ).fetchall()
        assert len(rows) == 2
        # ids are monotonically increasing (bigint autoincrement pk)
        assert int(rows[1][0]) > int(rows[0][0])
        assert all(r[2] is not None for r in rows)
    finally:
        await engine.dispose()
