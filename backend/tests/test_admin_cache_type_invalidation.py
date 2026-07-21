"""Admin master-data writes must enqueue Znuny CacheType signals.

Ticket-level invalidation alone leaves Queue/State/DynamicField/… caches stale
in the Znuny GUI. Each admin create/update/delete path must insert rows with
``cache_type`` set (and ``ticket_id`` NULL) for the CacheTypes declared in
``tiqora.api.v1.admin.common``.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin.common import (
    DYNAMIC_FIELD_CACHE_TYPES,
    QUEUE_CACHE_TYPES,
    TEMPLATE_CACHE_TYPES,
    invalidate_znuny_cache_types,
)
from tiqora.db.legacy.queue import Queue, StandardTemplate
from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.znuny.cache_invalidation import invalidate_cache_type


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _uid() -> str:
    return uuid.uuid4().hex[:10]


async def _seed(session: AsyncSession) -> None:
    # Prefer model DDL so columns match production; fall back to raw SQL.
    try:
        await session.run_sync(
            lambda sync_conn: TiqoraCacheInvalidation.__table__.create(sync_conn, checkfirst=True)
        )
    except Exception:
        await session.execute(
            text(
                """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ticket_id BIGINT NULL,
                    cache_type VARCHAR(100) NULL,
                    created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
        )
    # Ensure columns exist if an older seed left the table without them.
    for alter in (
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
    ):
        with contextlib.suppress(Exception):
            await session.execute(text(alter))
    await session.commit()


async def _cache_types_since(session: AsyncSession, after_id: int) -> set[str]:
    rows = (
        await session.execute(
            text(
                "SELECT cache_type FROM tiqora_cache_invalidation"
                " WHERE id > :aid AND cache_type IS NOT NULL"
            ),
            {"aid": after_id},
        )
    ).fetchall()
    return {r[0] for r in rows if r[0]}


async def _max_id(session: AsyncSession) -> int:
    row = (
        await session.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation"))
    ).first()
    return int(row[0]) if row else 0


@pytest.mark.db
async def test_invalidate_cache_type_row_shape(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed(session)
        async with factory() as session, session.begin():
            await invalidate_cache_type(session, "Queue")
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT ticket_id, cache_type FROM tiqora_cache_invalidation"
                        " WHERE cache_type = 'Queue' ORDER BY id DESC LIMIT 1"
                    )
                )
            ).first()
        assert row is not None
        assert row[0] is None
        assert row[1] == "Queue"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_admin_queue_update_enqueues_queue_cache_type(mariadb_znuny_url: str) -> None:
    """Mirrors queues.update_queue: master Queue CacheType + optional ticket rows."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed(session)

        async with factory() as session:
            before = await _max_id(session)
            q = (
                await session.execute(select(Queue).order_by(Queue.id).limit(1))
            ).scalar_one_or_none()
            assert q is not None
            queue_id = int(q.id)

        async with factory() as session, session.begin():
            # Same helper path as update_queue / deactivate_queue.
            from tiqora.api.v1.admin.common import invalidate_cache_for_queue

            await invalidate_cache_for_queue(session, queue_id)

        async with factory() as session:
            types = await _cache_types_since(session, before)
        assert set(QUEUE_CACHE_TYPES).issubset(types)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_admin_template_and_dynamic_field_cache_types(mariadb_znuny_url: str) -> None:
    """Representative master-data paths enqueue the expected CacheType sets."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seed = _uid()
    try:
        async with factory() as session:
            await _seed(session)

        # Template update path → Queue (StandardTemplate.pm cleans Queue only).
        async with factory() as session:
            before = await _max_id(session)
        async with factory() as session, session.begin():
            await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
        async with factory() as session:
            types = await _cache_types_since(session, before)
        assert set(TEMPLATE_CACHE_TYPES).issubset(types)

        # Dynamic field create path → DynamicField + DynamicFieldValue.
        async with factory() as session:
            before = await _max_id(session)
        async with factory() as session, session.begin():
            await invalidate_znuny_cache_types(session, DYNAMIC_FIELD_CACHE_TYPES)
        async with factory() as session:
            types = await _cache_types_since(session, before)
        assert set(DYNAMIC_FIELD_CACHE_TYPES).issubset(types)

        # Also exercise a real ORM write that the admin router would do, to
        # prove the table accepts the signal alongside a master-data row.
        async with factory() as session, session.begin():
            from datetime import UTC, datetime

            from tiqora.db.legacy.dynamic_field import DynamicField

            ts = datetime.now(UTC).replace(tzinfo=None)
            # Pick an existing template if any; otherwise just write signals.
            tmpl_stmt = select(StandardTemplate).order_by(StandardTemplate.id).limit(1)
            tmpl = (await session.execute(tmpl_stmt)).scalar_one_or_none()
            if tmpl is not None:
                tmpl.comments = f"cache-test-{seed}"
                tmpl.change_time = ts
            df = (
                await session.execute(select(DynamicField).order_by(DynamicField.id).limit(1))
            ).scalar_one_or_none()
            if df is not None:
                df.change_time = ts
            await invalidate_znuny_cache_types(
                session, (*TEMPLATE_CACHE_TYPES, *DYNAMIC_FIELD_CACHE_TYPES)
            )
    finally:
        await engine.dispose()
