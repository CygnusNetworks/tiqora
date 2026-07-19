"""DB tests for the ticket index accelerator (StaticDB / RuntimeDB)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective
from tiqora.znuny.ticket_index import (
    ticket_accelerator_add,
    ticket_accelerator_delete,
    ticket_accelerator_update,
)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _sysconfig(index_module: str) -> SysConfig:
    values: dict[str, Any] = {
        "Ticket::IndexModule": yaml_encode_effective(
            f"Kernel::System::Ticket::IndexAccelerator::{index_module}"
        ),
    }

    async def fetch(name: str) -> Any | None:
        return values.get(name)

    return SysConfig(fetch=fetch)


async def _insert_ticket(session: AsyncSession, tn: str, state_id: int = 1) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, 1, 1, 1, 1, 3, :sid, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "sid": state_id},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _index_row(session: AsyncSession, ticket_id: int) -> Any:
    return (
        await session.execute(
            text(
                "SELECT queue_id, queue, group_id, s_lock, s_state"
                " FROM ticket_index WHERE ticket_id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()


@pytest.mark.db
async def test_static_db_add_update_delete(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _sysconfig("StaticDB")
    try:
        async with factory() as session:
            ticket_id = await _insert_ticket(session, "IDX_TEST_1", state_id=1)
            await session.commit()

            # Add: viewable 'new' ticket → row inserted with queue/lock/state names
            await ticket_accelerator_add(session, ticket_id, sysconfig)
            await session.commit()
            row = await _index_row(session, ticket_id)
            assert row is not None
            assert int(row[0]) == 1  # queue_id
            assert row[3] == "unlock"  # s_lock (lock type 1)
            assert row[4] == "new"  # s_state

            # Update: change state to 'open' (id 4 in seed) → index resynced
            open_row = (
                await session.execute(
                    text("SELECT id FROM ticket_state WHERE name = 'open' LIMIT 1")
                )
            ).first()
            assert open_row is not None
            await session.execute(
                text("UPDATE ticket SET ticket_state_id = :sid WHERE id = :tid"),
                {"sid": int(open_row[0]), "tid": ticket_id},
            )
            await ticket_accelerator_update(session, ticket_id, sysconfig)
            await session.commit()
            row = await _index_row(session, ticket_id)
            assert row is not None
            assert row[4] == "open"

            # Update to a closed state → removed from index
            closed_row = (
                await session.execute(
                    text(
                        "SELECT ts.id FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE tst.name = 'closed' LIMIT 1"
                    )
                )
            ).first()
            assert closed_row is not None
            await session.execute(
                text("UPDATE ticket SET ticket_state_id = :sid WHERE id = :tid"),
                {"sid": int(closed_row[0]), "tid": ticket_id},
            )
            await ticket_accelerator_update(session, ticket_id, sysconfig)
            await session.commit()
            assert await _index_row(session, ticket_id) is None

            # Delete explicitly (idempotent)
            await ticket_accelerator_delete(session, ticket_id, sysconfig)
            await session.commit()
            assert await _index_row(session, ticket_id) is None
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_runtime_db_is_noop(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _sysconfig("RuntimeDB")
    try:
        async with factory() as session:
            ticket_id = await _insert_ticket(session, "IDX_TEST_RT", state_id=1)
            await session.commit()

            await ticket_accelerator_add(session, ticket_id, sysconfig)
            await session.commit()
            assert await _index_row(session, ticket_id) is None
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_static_db_skips_archived_ticket(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _sysconfig("StaticDB")
    try:
        async with factory() as session:
            ticket_id = await _insert_ticket(session, "IDX_TEST_ARCH", state_id=1)
            await session.execute(
                text("UPDATE ticket SET archive_flag = 1 WHERE id = :tid"),
                {"tid": ticket_id},
            )
            await session.commit()

            await ticket_accelerator_add(session, ticket_id, sysconfig)
            await session.commit()
            assert await _index_row(session, ticket_id) is None
    finally:
        await engine.dispose()
