"""Article-object dynamic-field writes (``ObjectType/Article.pm::PostValueSet``).

Seed ids use the 7606 / 7616-7620 band (user 7606, queue 7617, ticket 7618,
dynamic fields 7619 + 7620).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.dynamic_field_values import (
    DynamicFieldValueError,
    load_article_dynamic_field,
    set_article_dynamic_field,
)
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

OLD = datetime(2020, 1, 1, 12, 0, 0)
AGENT = 7606
QUEUE_ID = 7617
TICKET_ID = 7618
ART_FIELD = 7619
TICKET_FIELD = 7620


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
        "DELETE FROM dynamic_field_value WHERE object_id IN"
        f" (SELECT id FROM article WHERE ticket_id = {TICKET_ID})",
        f"DELETE FROM dynamic_field_value WHERE field_id IN ({ART_FIELD}, {TICKET_FIELD})",
        f"DELETE FROM dynamic_field WHERE id IN ({ART_FIELD}, {TICKET_FIELD})",
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
                " VALUES (:i, 'w1a.agent', 'x', 'Art', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"i": AGENT, "t": OLD},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'W1ArtQueue', 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
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
                " VALUES (:tid, '20260201760003', 'Art DF ticket', :q, 1, 1, :o, 1, 3, 4,"
                " 'CUST1', 'alice@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "q": QUEUE_ID, "o": AGENT, "t": OLD},
        )
        for fid, name, object_type in (
            (ART_FIELD, "WaveArtText", "Article"),
            (TICKET_FIELD, "WaveTicketOnly", "Ticket"),
        ):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :o, 'Text', :ot, '---', 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "n": name, "o": fid, "ot": object_type, "t": OLD},
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
                subject="DF note",
                body="b",
                channel="note",
            ),
            user_id=AGENT,
            sysconfig=SysConfig(fetch=_fetch),
        )


async def _set(
    factory: async_sessionmaker[AsyncSession], article_id: int, values: list[str]
) -> bool:
    async with factory() as session, session.begin():
        field = await load_article_dynamic_field(session, "WaveArtText")
        assert field is not None
        return await set_article_dynamic_field(
            session,
            ticket_id=TICKET_ID,
            article_id=article_id,
            field=field,
            values=values,
            user_id=AGENT,
        )


def _values(url: str) -> list[tuple[Any, Any]]:
    rows = _query(
        url,
        "SELECT object_id, value_text FROM dynamic_field_value WHERE field_id = :f ORDER BY id",
        f=ART_FIELD,
    )
    return [(int(r[0]), r[1]) for r in rows]


def _change(url: str) -> tuple[datetime, int]:
    row = _query(url, "SELECT change_time, change_by FROM ticket WHERE id = :i", i=TICKET_ID)[0]
    return row[0], int(row[1])


async def test_load_article_dynamic_field_only_finds_valid_article_fields(env: Any) -> None:
    _url, factory = env
    async with factory() as session:
        field = await load_article_dynamic_field(session, "WaveArtText")
        assert field is not None
        assert (field.id, field.name, field.field_type) == (ART_FIELD, "WaveArtText", "Text")
        assert await load_article_dynamic_field(session, "WaveTicketOnly") is None
        assert await load_article_dynamic_field(session, "NoSuchField") is None


async def test_set_article_dynamic_field(env: Any) -> None:
    url, factory = env
    article_id = await _article(factory)
    _query(
        url, "UPDATE ticket SET change_time = :t, change_by = 1 WHERE id = :i", t=OLD, i=TICKET_ID
    )
    _query(url, "DELETE FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID)
    history_before = _query(
        url, "SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t", t=TICKET_ID
    )[0][0]

    assert await _set(factory, article_id, ["a"]) is True
    assert _values(url) == [(article_id, "a")]
    changed_at, changed_by = _change(url)
    assert changed_by == AGENT
    assert changed_at > OLD
    events = _query(
        url, "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID
    )
    assert [(e[0], json.loads(e[1])) for e in events] == [
        (
            "ArticleDynamicFieldUpdate",
            {"field": "WaveArtText", "values": ["a"], "article_id": article_id},
        )
    ]
    # Article DFs never write a ticket_history row (PostValueSet of the Article object type).
    assert (
        _query(url, "SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t", t=TICKET_ID)[0][0]
        == history_before
    )

    # Same value again: nothing written at all.
    _query(
        url, "UPDATE ticket SET change_time = :t, change_by = 1 WHERE id = :i", t=OLD, i=TICKET_ID
    )
    _query(url, "DELETE FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID)
    assert await _set(factory, article_id, ["a"]) is False
    assert _change(url) == (OLD, 1)
    assert (
        _query(url, "SELECT COUNT(*) FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID)[
            0
        ][0]
        == 0
    )


async def test_article_dynamic_field_replace_clear_and_isolation(env: Any) -> None:
    url, factory = env
    first = await _article(factory)
    second = await _article(factory)
    assert await _set(factory, first, ["a"]) is True
    assert await _set(factory, second, ["b"]) is True
    assert await _set(factory, first, ["c"]) is True
    assert sorted(_values(url)) == sorted([(first, "c"), (second, "b")])
    assert await _set(factory, first, []) is True
    assert _values(url) == [(second, "b")]


async def test_article_dynamic_field_rejects_invalid_values(env: Any) -> None:
    url, factory = env
    article_id = await _article(factory)
    with pytest.raises(DynamicFieldValueError):
        await _set(factory, article_id, ["a", "b"])
    assert _values(url) == []
