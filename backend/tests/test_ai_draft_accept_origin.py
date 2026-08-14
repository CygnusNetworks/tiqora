"""Accepted drafts leave an AI-origin marker (badge + tool trace) on the
article they became — see ``tiqora.ai.drafts.record_accepted_origin`` and the
``ai_draft_id`` hook in ``tiqora.api.v1.tickets``."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import drafts as ai_drafts
from tiqora.ai.models import TiqoraAiArticleOrigin
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.anyio


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


_ARTICLE_ID = 990_063_001
_TICKET_ID = 990_063_002
_TRACE = '[{"role": "tool", "name": "kb_search", "content": "{\\"hits\\": 1}"}]'


async def _cleanup(session: AsyncSession) -> None:
    await session.execute(
        text("DELETE FROM tiqora_ai_article_origin WHERE article_id = :a"), {"a": _ARTICLE_ID}
    )
    await session.execute(
        text("DELETE FROM tiqora_ai_draft WHERE ticket_id = :t"), {"t": _TICKET_ID}
    )
    await session.commit()


async def test_accepted_draft_records_origin_with_trace(mariadb_znuny_url: str) -> None:
    sync_engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                draft = await ai_drafts.create_draft(
                    session,
                    ticket_id=_TICKET_ID,
                    queue_id=7701,
                    kind="reply",
                    body="Antworttext",
                    subject=None,
                    based_on_article_id=None,
                    tool_trace_json=_TRACE,
                    created_by_user_id=1,
                    source="manual",
                    actor_user_id=1,
                )
                accepted = await ai_drafts.mark_accepted(
                    session, draft.id, article_id=_ARTICLE_ID, actor_user_id=1
                )
                assert accepted is not None
                await ai_drafts.record_accepted_origin(
                    session, accepted, article_id=_ARTICLE_ID, actor_user_id=1
                )

                origin = await session.get(TiqoraAiArticleOrigin, _ARTICLE_ID)
                assert origin is not None
                assert origin.source == "accepted"
                assert origin.draft_id == draft.id
                assert origin.queue_id == 7701
                assert origin.service_user_id == 1
                assert origin.tool_trace_json == _TRACE
            finally:
                await _cleanup(session)
    finally:
        await engine.dispose()
