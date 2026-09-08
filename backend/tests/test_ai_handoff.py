"""DB tests for ``tiqora.ai.handoff`` (the AI→human handoff flag).

Follows the direct-service-call pattern from ``test_ai_usage_pricing.py``:
local testcontainer only, real async session, no network. Seed ids use the
896xx range.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import handoff
from tiqora.ai.models import TiqoraAiTicketState
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _delete_state_rows(conn)
    engine.dispose()


def _delete_state_rows(conn: Connection) -> None:
    conn.execute(text("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id BETWEEN 89600 AND 89699"))


@pytest.fixture(autouse=True, scope="module")
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    """Clearing only on the way *in* left the last test's rows behind."""
    yield
    engine = create_engine(mariadb_znuny_url)
    try:
        with engine.begin() as conn:
            _delete_state_rows(conn)
    finally:
        engine.dispose()


async def test_mark_ai_escalated_creates_row_and_is_idempotent(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await handoff.mark_ai_escalated(session, 89601)

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, 89601)
            assert state is not None
            first_ts = state.ai_escalated_at
            assert first_ts is not None

        # Second call is a no-op — the original timestamp is preserved.
        async with factory() as session, session.begin():
            await handoff.mark_ai_escalated(session, 89601)

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, 89601)
            assert state is not None
            assert state.ai_escalated_at == first_ts
    finally:
        await engine.dispose()


async def test_clear_ai_escalated_resets_flag(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await handoff.mark_ai_escalated(session, 89602)

        async with factory() as session, session.begin():
            await handoff.clear_ai_escalated(session, 89602)

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, 89602)
            assert state is not None
            assert state.ai_escalated_at is None
    finally:
        await engine.dispose()


async def test_clear_ai_escalated_noop_when_row_missing(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await handoff.clear_ai_escalated(session, 89603)  # no row for this ticket

        async with factory() as session:
            assert await session.get(TiqoraAiTicketState, 89603) is None
    finally:
        await engine.dispose()


async def test_ai_escalated_ticket_ids_filters_to_flagged_only(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await handoff.mark_ai_escalated(session, 89604)
            # 89605 has a state row but no escalation flag.
            session.add(TiqoraAiTicketState(ticket_id=89605))

        async with factory() as session:
            ids = await handoff.ai_escalated_ticket_ids(session, [89604, 89605, 89606])
            assert ids == {89604}
    finally:
        await engine.dispose()
