"""Tests for worker/outbox_drain.py — outbox drain, idempotency, error isolation.

Meilisearch indexing, webhook dispatch, and pub/sub publish are mocked (they
have their own dedicated test coverage elsewhere); this file focuses on
outbox_drain's own logic: the enabled-flag gate, batch selection, marking
rows processed exactly once, and that webhook/pubsub failures never abort
the drain (the ``processed`` mark must still happen — see the
``# noqa: BLE001`` comments in the source for the documented intent).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_OUTBOX_ENABLED, set_setting
from tiqora.worker import outbox_drain

pytestmark = pytest.mark.db


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


def _reset(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_event_outbox"))
        conn.execute(
            TiqoraSettings.__table__.delete().where(TiqoraSettings.key == KEY_OUTBOX_ENABLED)
        )
    engine.dispose()


def _insert_outbox_row(
    sync_url: str, *, event_type: str, ticket_id: int, payload: str = "{}"
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_event_outbox (event_type, ticket_id, payload, processed)"
                " VALUES (:et, :tid, :p, :proc)"
            ),
            {"et": event_type, "tid": ticket_id, "p": payload, "proc": False},
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def _mock_pubsub() -> object:
    with patch.object(outbox_drain, "get_pubsub_redis", return_value=object()) as m:
        yield m


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_disabled_is_a_noop(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session, session.begin():
        await set_setting(session, KEY_OUTBOX_ENABLED, "0")

    _insert_outbox_row(sync_url, event_type="TicketCreate", ticket_id=1)

    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()) as mock_wh,
    ):
        result = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert result == {"enabled": 0}
    mock_svc.assert_not_called()
    mock_wh.assert_not_called()

    async with factory() as session:
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_event_outbox WHERE NOT processed")
            )
        ).scalar_one()
        assert remaining == 1, "disabled drain must not touch outbox rows"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_no_rows_returns_zero(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()) as mock_wh,
    ):
        result = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert result == {"processed": 0, "ticket_ids": 0}
    mock_svc.assert_not_called()
    mock_wh.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_indexes_and_marks_processed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    _insert_outbox_row(sync_url, event_type="TicketCreate", ticket_id=101)
    _insert_outbox_row(sync_url, event_type="ArticleCreate", ticket_id=102)
    _insert_outbox_row(sync_url, event_type="ArticleCreate", ticket_id=101)  # dup ticket
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mock_index = AsyncMock(return_value=3)
    mock_close = AsyncMock()
    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc_cls,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()) as mock_wh,
        patch.object(outbox_drain, "publish_ticket_event", new=AsyncMock()) as mock_pub,
    ):
        mock_svc_cls.return_value.index_tickets = mock_index
        mock_svc_cls.return_value.close = mock_close

        result = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert result == {"processed": 3, "ticket_ids": 2}
    mock_index.assert_awaited_once()
    (indexed_ids,), _ = mock_index.call_args
    assert sorted(indexed_ids) == [101, 102]
    mock_close.assert_awaited_once()
    mock_wh.assert_awaited_once()
    # 3 rows, all distinct (ticket_id, event_type) pairs (101/TicketCreate,
    # 102/ArticleCreate, 101/ArticleCreate) -> one publish per pair.
    assert mock_pub.await_count == 3

    async with factory() as session:
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_event_outbox WHERE NOT processed")
            )
        ).scalar_one()
        assert remaining == 0

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_twice_does_not_reprocess_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Idempotency: a second drain call sees no unprocessed rows and is a no-op."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    _insert_outbox_row(sync_url, event_type="TicketCreate", ticket_id=201)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc_cls,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()),
        patch.object(outbox_drain, "publish_ticket_event", new=AsyncMock()),
    ):
        mock_svc_cls.return_value.index_tickets = AsyncMock(return_value=1)
        mock_svc_cls.return_value.close = AsyncMock()

        first = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)
        second = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert first == {"processed": 1, "ticket_ids": 1}
    assert second == {"processed": 0, "ticket_ids": 0}
    assert mock_svc_cls.return_value.index_tickets.await_count == 1

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_webhook_failure_does_not_abort_or_block_processed_mark(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """dispatch_webhooks raising must be swallowed — the drain still marks rows processed."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    _insert_outbox_row(sync_url, event_type="TicketCreate", ticket_id=301)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc_cls,
        patch.object(
            outbox_drain, "dispatch_webhooks", new=AsyncMock(side_effect=RuntimeError("boom"))
        ),
        patch.object(outbox_drain, "publish_ticket_event", new=AsyncMock()),
    ):
        mock_svc_cls.return_value.index_tickets = AsyncMock(return_value=1)
        mock_svc_cls.return_value.close = AsyncMock()

        result = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert result == {"processed": 1, "ticket_ids": 1}

    async with factory() as session:
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_event_outbox WHERE NOT processed")
            )
        ).scalar_one()
        assert remaining == 0, "webhook failure must not prevent marking rows processed"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_drain_pubsub_failure_does_not_abort_or_block_processed_mark(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset(sync_url)
    _insert_outbox_row(sync_url, event_type="TicketCreate", ticket_id=401)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch.object(outbox_drain, "SearchIndexService") as mock_svc_cls,
        patch.object(outbox_drain, "dispatch_webhooks", new=AsyncMock()),
        patch.object(outbox_drain, "get_pubsub_redis", side_effect=RuntimeError("no redis")),
    ):
        mock_svc_cls.return_value.index_tickets = AsyncMock(return_value=1)
        mock_svc_cls.return_value.close = AsyncMock()

        result = await outbox_drain.drain_outbox(settings=get_settings(), session_factory=factory)

    assert result == {"processed": 1, "ticket_ids": 1}

    async with factory() as session:
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_event_outbox WHERE NOT processed")
            )
        ).scalar_one()
        assert remaining == 0, "pubsub failure must not prevent marking rows processed"

    await engine.dispose()
