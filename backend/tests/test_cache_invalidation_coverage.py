"""Regression: every ticket/article-mutating write op must enqueue cache invalidation.

Znuny only sees Tiqora's direct DB writes after the TiqoraSync daemon drains
``tiqora_cache_invalidation``. Ops that skip :func:`invalidate_ticket_cache`
leave the GUI stale. This suite exercises each public write path and asserts
at least one invalidation row for the affected ticket id(s). Two-ticket ops
(merge, link) must cover both ids.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    add_article,
    archive_ticket,
    assign_owner,
    assign_responsible,
    bounce_article,
    change_priority,
    change_state,
    change_title,
    create_ticket,
    forward_article,
    link_tickets,
    lock_ticket,
    merge_tickets,
    move_queue,
    set_customer,
    unlock_ticket,
    unwatch_ticket,
    update_dynamic_field,
    watch_ticket,
)
from tiqora.znuny.sysconfig import SysConfig


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    for ddl in (
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        # Align reused containers that still have the pre-cache_type schema.
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
    ):
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _make_sysconfig() -> SysConfig:
    async def _fetch(_name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _make_ticket(
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    title: str,
) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            sysconfig,
            params=TicketIn(
                title=title,
                queue_id=1,
                state_id=1,
                priority_id=3,
                owner_id=1,
            ),
            user_id=1,
        )


async def _cache_count(session: AsyncSession, ticket_id: int) -> int:
    row = (
        await session.execute(
            text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).first()
    return int(row[0]) if row else 0


async def _assert_new_invalidation(
    factory: async_sessionmaker[AsyncSession],
    ticket_id: int,
    before: int,
    *,
    op: str,
) -> None:
    async with factory() as session:
        after = await _cache_count(session, ticket_id)
    assert after > before, (
        f"{op}: expected new tiqora_cache_invalidation row for ticket_id={ticket_id}"
        f" (before={before}, after={after})"
    )


@pytest.mark.db
async def test_write_ops_enqueue_cache_invalidation(mariadb_znuny_url: str) -> None:
    """Each mutating write op inserts ≥1 cache row for the ticket(s) it touches."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()
    seed = _uid()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-a")
    other_id = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-b")
    merge_src = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-merge-src")
    merge_tgt = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-merge-tgt")
    link_src = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-link-src")
    link_tgt = await _make_ticket(factory, sysconfig, f"cache-cov-{seed}-link-tgt")

    # Snapshot counts after create_ticket (which itself invalidates).
    async with factory() as session:
        base = await _cache_count(session, ticket_id)
        other_base = await _cache_count(session, other_id)
        merge_src_base = await _cache_count(session, merge_src)
        merge_tgt_base = await _cache_count(session, merge_tgt)
        link_src_base = await _cache_count(session, link_src)
        link_tgt_base = await _cache_count(session, link_tgt)

    # --- single-ticket ops ---
    async with factory() as session, session.begin():
        await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject=f"note-{seed}",
                body="body",
                channel="note",
            ),
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="add_article")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await move_queue(
            session, ticket_id=ticket_id, new_queue_id=1, user_id=1, sysconfig=sysconfig
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="move_queue")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await change_state(
            session,
            ticket_id=ticket_id,
            new_state_id=1,
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="change_state")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await change_priority(
            session,
            ticket_id=ticket_id,
            new_priority_id=2,
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="change_priority")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await change_title(session, ticket_id=ticket_id, new_title=f"renamed-{seed}", user_id=1)
    await _assert_new_invalidation(factory, ticket_id, base, op="change_title")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await set_customer(
            session,
            ticket_id=ticket_id,
            customer_id=f"cust-{seed}",
            customer_user_id=f"user-{seed}",
            user_id=1,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="set_customer")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    # assign_owner no-ops when owner is already the same; use a different owner if possible.
    async with factory() as session:
        owner_row = (
            await session.execute(text("SELECT id FROM users WHERE id != 1 ORDER BY id LIMIT 1"))
        ).first()
    if owner_row is not None:
        async with factory() as session, session.begin():
            await assign_owner(
                session,
                ticket_id=ticket_id,
                new_owner_id=int(owner_row[0]),
                user_id=1,
                sysconfig=sysconfig,
            )
        await _assert_new_invalidation(factory, ticket_id, base, op="assign_owner")
        async with factory() as session:
            base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await assign_responsible(
            session,
            ticket_id=ticket_id,
            new_responsible_id=1,
            user_id=1,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="assign_responsible")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await lock_ticket(session, ticket_id=ticket_id, user_id=1, sysconfig=sysconfig)
    await _assert_new_invalidation(factory, ticket_id, base, op="lock_ticket")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await unlock_ticket(session, ticket_id=ticket_id, user_id=1, sysconfig=sysconfig)
    await _assert_new_invalidation(factory, ticket_id, base, op="unlock_ticket")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await watch_ticket(session, ticket_id=ticket_id, watcher_user_id=1, user_id=1)
    await _assert_new_invalidation(factory, ticket_id, base, op="watch_ticket")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await unwatch_ticket(session, ticket_id=ticket_id, watcher_user_id=1, user_id=1)
    await _assert_new_invalidation(factory, ticket_id, base, op="unwatch_ticket")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await archive_ticket(
            session, ticket_id=ticket_id, archive=True, user_id=1, sysconfig=sysconfig
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="archive_ticket")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name="ProcessManagementProcessID",
            values=[f"proc-{seed}"],
            user_id=1,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="update_dynamic_field")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await forward_article(
            session,
            ticket_id=ticket_id,
            subject=f"fwd-{seed}",
            body="forwarded body",
            to_address="forward@example.com",
            cc=None,
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="forward_article")
    async with factory() as session:
        base = await _cache_count(session, ticket_id)

    async with factory() as session, session.begin():
        await bounce_article(
            session,
            ticket_id=ticket_id,
            subject=f"bounce-{seed}",
            body="bounced body",
            content_type="text/plain; charset=utf-8",
            to_address="bounce@example.com",
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, ticket_id, base, op="bounce_article")

    # other_id only used as a sanity second single-ticket target (no extra ops).
    del other_base  # kept for clarity of multi-ticket setup above

    # --- two-ticket: merge (source + target) ---
    async with factory() as session, session.begin():
        await merge_tickets(
            session,
            main_ticket_id=merge_tgt,
            merge_ticket_id=merge_src,
            user_id=1,
            sysconfig=sysconfig,
        )
    await _assert_new_invalidation(factory, merge_tgt, merge_tgt_base, op="merge_tickets(main)")
    await _assert_new_invalidation(factory, merge_src, merge_src_base, op="merge_tickets(merge)")

    # --- two-ticket: link (both ends) ---
    async with factory() as session, session.begin():
        await link_tickets(
            session,
            source_ticket_id=link_src,
            target_ticket_id=link_tgt,
            link_type="Normal",
            user_id=1,
        )
    await _assert_new_invalidation(factory, link_src, link_src_base, op="link_tickets(source)")
    await _assert_new_invalidation(factory, link_tgt, link_tgt_base, op="link_tickets(target)")

    await engine.dispose()
