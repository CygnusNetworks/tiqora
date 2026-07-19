"""Orphan-FK report tests (Phase 5, subtask 2) — read-only, no cleanup."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.orphan_report import build_orphan_report

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _reset(session: AsyncSession) -> None:
    for table in ("article", "ticket"):
        with contextlib.suppress(Exception):
            await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()


async def _insert_ticket(
    session: AsyncSession, tn: str, queue_id: int, *, disable_fk: bool = False
) -> int:
    if disable_fk:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, :qid, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "qid": queue_id},
    )
    if disable_fk:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


@pytest.mark.db
async def test_orphan_report_detects_dangling_queue_reference(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _reset(session)
        # queue_id 999999 does not exist -> dangling ticket.queue_id reference.
        # Znuny enforces a real FK here, so disable checks to seed the orphan
        # (simulating pre-existing drift found by the read-only report).
        await _insert_ticket(session, "orphan-1", queue_id=999999, disable_fk=True)

        rows = await build_orphan_report(session)
        by_relation = {r.relation: r.orphan_count for r in rows}
        assert by_relation["ticket.queue_id -> queue.id"] >= 1
    await engine.dispose()


@pytest.mark.db
async def test_orphan_report_zero_for_clean_data(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _reset(session)
        # queue_id 1 exists from Znuny initial_insert seed data.
        await _insert_ticket(session, "clean-1", queue_id=1)

        rows = await build_orphan_report(session)
        by_relation = {r.relation: r.orphan_count for r in rows}
        assert by_relation["ticket.queue_id -> queue.id"] == 0
    await engine.dispose()


@pytest.mark.db
async def test_orphan_report_covers_at_least_fifteen_relations(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        rows = await build_orphan_report(session)
        assert len(rows) >= 15
    await engine.dispose()
