"""DB tests for tiqora.ai.drafts (plan §3.1/§3.4 draft lifecycle).

Follows the direct-service-call pattern from ``test_ai_admin.py``: local
testcontainer only, real async session, no network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import drafts as ai_drafts
from tiqora.ai.models import (
    DRAFT_STATUS_ACCEPTED,
    DRAFT_STATUS_DISCARDED,
    DRAFT_STATUS_OPEN,
    DRAFT_STATUS_SUPERSEDED,
)
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_ai_draft"))
    engine.dispose()


async def test_create_draft_supersedes_open_draft_same_key(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            first = await ai_drafts.create_draft(
                session,
                ticket_id=9500,
                queue_id=1,
                kind="reply",
                body="First draft",
                based_on_article_id=100,
                actor_user_id=1,
                source="auto",
            )
            assert first.status == DRAFT_STATUS_OPEN

            second = await ai_drafts.create_draft(
                session,
                ticket_id=9500,
                queue_id=1,
                kind="reply",
                body="Second draft",
                based_on_article_id=100,
                actor_user_id=1,
                source="auto",
            )
            assert second.status == DRAFT_STATUS_OPEN

            refreshed_first = await ai_drafts.get_draft(session, first.id)
            assert refreshed_first is not None
            assert refreshed_first.status == DRAFT_STATUS_SUPERSEDED

            # A different based_on_article_id is a different key — no supersede.
            third = await ai_drafts.create_draft(
                session,
                ticket_id=9500,
                queue_id=1,
                kind="reply",
                body="Third draft, different article",
                based_on_article_id=101,
                actor_user_id=1,
                source="auto",
            )
            assert third.status == DRAFT_STATUS_OPEN
            still_open_second = await ai_drafts.get_draft(session, second.id)
            assert still_open_second is not None
            assert still_open_second.status == DRAFT_STATUS_OPEN

            drafts = await ai_drafts.list_for_ticket(session, 9500)
            assert {d.id for d in drafts} == {first.id, second.id, third.id}
    finally:
        await engine.dispose()


async def test_discard_draft(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            draft = await ai_drafts.create_draft(
                session,
                ticket_id=9501,
                queue_id=1,
                kind="clarify",
                body="Please clarify",
                based_on_article_id=None,
                actor_user_id=1,
                source="manual",
                created_by_user_id=7,
            )
            discarded = await ai_drafts.discard_draft(session, draft, actor_user_id=7)
            assert discarded.status == DRAFT_STATUS_DISCARDED

            with pytest.raises(ai_drafts.DraftStateError):
                await ai_drafts.discard_draft(session, discarded, actor_user_id=7)
    finally:
        await engine.dispose()


async def test_mark_accepted_only_once_and_only_when_open(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            draft = await ai_drafts.create_draft(
                session,
                ticket_id=9502,
                queue_id=1,
                kind="reply",
                body="Answer",
                based_on_article_id=200,
                actor_user_id=1,
                source="manual",
                created_by_user_id=7,
            )
            accepted = await ai_drafts.mark_accepted(
                session, draft.id, article_id=555, actor_user_id=7
            )
            assert accepted is not None
            assert accepted.status == DRAFT_STATUS_ACCEPTED
            assert accepted.accepted_article_id == 555

            # Already accepted -> no-op (returns None), never re-points the article.
            again = await ai_drafts.mark_accepted(
                session, draft.id, article_id=999, actor_user_id=7
            )
            assert again is None
            reloaded = await ai_drafts.get_draft(session, draft.id)
            assert reloaded is not None
            assert reloaded.accepted_article_id == 555

            missing = await ai_drafts.mark_accepted(session, 999999, article_id=1, actor_user_id=7)
            assert missing is None
    finally:
        await engine.dispose()
