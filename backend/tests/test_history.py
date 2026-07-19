"""DB tests for history helpers — exact Znuny name string formats and snapshots."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.history import (
    add_customer_update,
    add_dynamic_field_update,
    add_move,
    add_new_ticket,
    add_owner_update,
    add_pending_time,
    add_priority_update,
    add_state_update,
    add_subscribe,
    history_add,
)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _pg_async(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _insert_minimal_ticket(session: AsyncSession, tn: str) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _last_history(session: AsyncSession, ticket_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT h.name, ht.name, h.queue_id, h.owner_id, h.priority_id,"
                " h.state_id, h.type_id, h.article_id, h.create_by, h.change_by"
                " FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid ORDER BY h.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    assert row is not None
    return {
        "name": row[0],
        "htype": row[1],
        "queue_id": row[2],
        "owner_id": row[3],
        "priority_id": row[4],
        "state_id": row[5],
        "type_id": row[6],
        "article_id": row[7],
        "create_by": row[8],
        "change_by": row[9],
    }


async def _exercise_history_helpers(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        ticket_id = await _insert_minimal_ticket(session, "HIST_TEST_1")
        await session.commit()

        # NewTicket: %%TN%%Queue%%Priority%%State%%TicketID
        await add_new_ticket(
            session,
            ticket_id=ticket_id,
            tn="HIST_TEST_1",
            queue="Raw",
            priority="3 normal",
            state="new",
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "NewTicket"
        assert row["name"] == f"%%HIST_TEST_1%%Raw%%3 normal%%new%%{ticket_id}"
        # Snapshot columns filled from the ticket row
        assert row["queue_id"] == 1
        assert row["owner_id"] == 1
        assert row["priority_id"] == 3
        assert row["state_id"] == 1
        assert row["type_id"] == 1  # NULL ticket.type_id falls back to 1
        assert row["article_id"] is None
        assert row["create_by"] == 1
        assert row["change_by"] == 1

        # StateUpdate: %%OldState%%NewState%%
        await add_state_update(
            session, ticket_id=ticket_id, old_state="new", new_state="open", user_id=1
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "StateUpdate"
        assert row["name"] == "%%new%%open%%"

        # Move: %%NewQueue%%NewQueueID%%OldQueue%%OldQueueID
        await add_move(
            session,
            ticket_id=ticket_id,
            new_queue="Junk",
            new_queue_id=3,
            old_queue="Raw",
            old_queue_id=1,
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "Move"
        assert row["name"] == "%%Junk%%3%%Raw%%1"
        assert row["queue_id"] == 3  # Move passes the new queue_id explicitly

        # PriorityUpdate: %%OldPriority%%OldPriorityID%%NewPriority%%NewPriorityID
        await add_priority_update(
            session,
            ticket_id=ticket_id,
            old_priority="3 normal",
            old_priority_id=3,
            new_priority="4 high",
            new_priority_id=4,
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "PriorityUpdate"
        assert row["name"] == "%%3 normal%%3%%4 high%%4"

        # OwnerUpdate: %%NewUser%%NewUserID
        await add_owner_update(
            session, ticket_id=ticket_id, new_user="root@localhost", new_user_id=1, user_id=1
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "OwnerUpdate"
        assert row["name"] == "%%root@localhost%%1"

        # CustomerUpdate: %%CustomerID=X;CustomerUser=Y;
        await add_customer_update(
            session,
            ticket_id=ticket_id,
            customer_id="acme",
            customer_user="jdoe",
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "CustomerUpdate"
        assert row["name"] == "%%CustomerID=acme;CustomerUser=jdoe;"

        # SetPendingTime: %%YYYY-MM-DD HH:MM
        await add_pending_time(
            session, ticket_id=ticket_id, year=2026, month=7, day=20, hour=9, minute=5, user_id=1
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "SetPendingTime"
        assert row["name"] == "%%2026-07-20 09:05"

        # Subscribe: %%UserFullname
        await add_subscribe(session, ticket_id=ticket_id, user_fullname="Admin OTRS", user_id=1)
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "Subscribe"
        assert row["name"] == "%%Admin OTRS"

        # TicketDynamicFieldUpdate: %%FieldName%%N%%Value%%V%%OldValue%%OV
        await add_dynamic_field_update(
            session,
            ticket_id=ticket_id,
            field_name="MyField",
            value="new-value",
            old_value="old-value",
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "TicketDynamicFieldUpdate"
        assert row["name"] == "%%FieldName%%MyField%%Value%%new-value%%OldValue%%old-value"

        # Name longer than 200 chars is truncated (Znuny limit)
        await history_add(
            session,
            ticket_id=ticket_id,
            history_type="Misc",
            name="x" * 300,
            user_id=1,
        )
        await session.commit()
        row = await _last_history(session, ticket_id)
        assert row["htype"] == "Misc"
        assert len(row["name"]) == 200


@pytest.mark.db
async def test_history_helpers_mariadb(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _exercise_history_helpers(factory)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_history_helpers_postgres(postgres_znuny_url: str) -> None:
    engine = create_async_engine(_pg_async(postgres_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _exercise_history_helpers(factory)
    finally:
        await engine.dispose()
