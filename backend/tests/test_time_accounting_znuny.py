"""``add_time_accounting`` == Znuny ``TicketAccountTime`` (normalisation, history, change_time).

Seed ids use the 7605 / 7613-7615 band (user 7605, queue 7614, ticket 7615).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    add_article,
    add_time_accounting,
)
from tiqora.worker import outbox_drain
from tiqora.znuny.history import TYPE_TIME_ACCOUNTING
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

OLD = datetime(2020, 1, 1, 12, 0, 0)
AGENT = 7605
QUEUE_ID = 7614
TICKET_ID = 7615


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _cleanup(conn: Any) -> None:
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM time_accounting WHERE ticket_id = {TICKET_ID}",
        "DELETE FROM article_data_mime WHERE article_id IN"
        f" (SELECT id FROM article WHERE ticket_id = {TICKET_ID})",
        f"DELETE FROM article WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket WHERE id = {TICKET_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM users WHERE id = {AGENT}",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, 'w1t.agent', 'x', 'Time', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"i": AGENT, "t": OLD},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'W1TimeQueue', 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_ID, "t": OLD},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20260201760002', 'Time ticket', :q, 1, 1, :o, 1, 3, 4,"
                " 'CUST1', 'alice@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "q": QUEUE_ID, "o": AGENT, "t": OLD},
        )
    engine.dispose()


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        rows = result.fetchall() if result.returns_rows else []
    engine.dispose()
    return rows


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield sync_url, factory
    finally:
        await engine.dispose()
        e = create_engine(sync_url)
        with e.begin() as conn:
            _cleanup(conn)
        e.dispose()


async def _article(factory: async_sessionmaker[AsyncSession]) -> int:
    async def _fetch(name: str) -> Any:
        return None

    async with factory() as session, session.begin():
        return await add_article(
            session,
            ticket_id=TICKET_ID,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="Booked",
                body="b",
                channel="note",
            ),
            user_id=AGENT,
            sysconfig=SysConfig(fetch=_fetch),
        )


async def _account(
    factory: async_sessionmaker[AsyncSession], unit: str | float, article_id: int | None = None
) -> int:
    async with factory() as session, session.begin():
        return await add_time_accounting(
            session,
            ticket_id=TICKET_ID,
            article_id=article_id,
            time_unit=unit,
            user_id=AGENT,
        )


def _history(url: str) -> list[tuple[str, int | None]]:
    rows = _query(
        url,
        "SELECT h.name, h.article_id FROM ticket_history h"
        " JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.ticket_id = :t AND t.name = :n ORDER BY h.id",
        t=TICKET_ID,
        n=TYPE_TIME_ACCOUNTING,
    )
    return [(str(r[0]), r[1]) for r in rows]


def _units(url: str) -> list[Decimal]:
    rows = _query(
        url, "SELECT time_unit FROM time_accounting WHERE ticket_id = :t ORDER BY id", t=TICKET_ID
    )
    return [Decimal(str(r[0])) for r in rows]


async def test_account_time_matches_znuny(env: Any) -> None:
    url, factory = env
    article_id = await _article(factory)
    _query(
        url, "UPDATE ticket SET change_time = :t, change_by = 1 WHERE id = :i", t=OLD, i=TICKET_ID
    )
    _query(url, "DELETE FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID)

    await _account(factory, "1,5", article_id)
    assert _units(url) == [Decimal("1.50")]
    ticket = _query(url, "SELECT change_time, change_by FROM ticket WHERE id = :i", i=TICKET_ID)[0]
    assert ticket[0] > OLD
    assert ticket[1] == AGENT

    await _account(factory, 2, article_id)
    assert _units(url) == [Decimal("1.50"), Decimal("2.00")]
    assert _history(url) == [("%%1.5%%1.5", article_id), ("%%2%%3.5", article_id)]

    events = _query(
        url,
        "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id",
        t=TICKET_ID,
    )
    assert [(e[0], json.loads(e[1])) for e in events] == [
        ("TicketAccountTime", {"article_id": article_id})
    ] * 2
    invalidations = _query(
        url, "SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE ticket_id = :t", t=TICKET_ID
    )
    assert invalidations[0][0] >= 1

    # The drain relays the new event like any other ticket event (SSE / webhooks).
    with (
        patch.object(outbox_drain, "SearchIndexService") as search,
        patch.object(outbox_drain, "get_pubsub_redis", return_value=object()),
        patch.object(outbox_drain, "publish_ticket_event", new=AsyncMock()) as publish,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()) as webhooks,
    ):
        search.return_value.index_tickets = AsyncMock()
        search.return_value.close = AsyncMock()
        await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)
    delivered = {(row[0], row[1]) for row in webhooks.call_args.args[0]}
    assert ("TicketAccountTime", TICKET_ID) in delivered
    assert any(c.args[2] == "TicketAccountTime" for c in publish.call_args_list)


@pytest.mark.parametrize(
    ("raw", "stored", "history_unit"),
    [
        ("0,256", Decimal("0.25"), "0.25"),
        (0.256, Decimal("0.25"), "0.25"),
        (" 1 000.5 ", Decimal("1000.50"), "1000.5"),
        ("0.255", Decimal("0.25"), "0.25"),
        ("7", Decimal("7.00"), "7"),
        (30, Decimal("30.00"), "30"),
    ],
)
async def test_unit_is_normalised_like_perl(
    env: Any, raw: str | float, stored: Decimal, history_unit: str
) -> None:
    url, factory = env
    await _account(factory, raw)
    assert _units(url) == [stored]
    assert _history(url) == [(f"%%{history_unit}%%{history_unit}", None)]


@pytest.mark.parametrize("raw", ["abc", "1e3", "", "0", 0, "1.5.5.x"])
async def test_invalid_or_missing_unit_is_rejected_without_writes(env: Any, raw: str | int) -> None:
    url, factory = env
    with pytest.raises(InvalidInput):
        await _account(factory, raw)
    assert _units(url) == []
    assert _history(url) == []
    change = _query(url, "SELECT change_time FROM ticket WHERE id = :i", i=TICKET_ID)[0][0]
    assert change == OLD
