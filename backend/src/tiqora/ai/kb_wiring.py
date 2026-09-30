"""Shared KB wiring for the agent runtime (plan §3.4 step 7-8).

Extracted from ``tiqora.api.v1.ai`` (Phase B) so the auto-reply worker
(``tiqora.ai.auto_worker``, Phase D) does not copy-paste the same KB plumbing
per queue — both the manual-assist API route and the worker build the KB
bundle/search/get-article seams the same way. Which LLM a queue uses is
resolved in :mod:`tiqora.ai.llm_routing`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.listfields import parse_int_list, parse_str_list
from tiqora.ai.models import TiqoraAiQueuePolicy
from tiqora.config import Settings

logger = structlog.get_logger(__name__)

_KB_ARTICLE_BUNDLE_LIMIT = 20
_KB_ARTICLE_BODY_CHARS = 2000


async def kb_bundle(
    session: AsyncSession, settings: Settings, user_id: int, policy: TiqoraAiQueuePolicy
) -> str | None:
    """Small tag/category-bound knowledge bundle (plan §3.4 step 7 "hybrid").

    Uses :meth:`KbService.get_knowledge`, which never touches Meilisearch
    (pure SQL by tags/category) — safe to call unconditionally.
    """
    tags = parse_str_list(policy.kb_tags) or None
    category_ids = parse_int_list(policy.kb_category_ids) or None
    category_id = category_ids[0] if category_ids else None
    if not tags and category_id is None:
        return None

    from tiqora.kb.service import KbService

    svc = KbService(session, settings)
    try:
        pairs = await svc.get_knowledge(user_id, tags=tags, category_id=category_id)
    finally:
        await svc.close()

    if not pairs:
        return None
    parts = []
    for article, tag_names in pairs[:_KB_ARTICLE_BUNDLE_LIMIT]:
        # The id is what kb_get_article takes: without it the model guessed
        # ids and fetched unrelated draft articles (production replays).
        meta = f"article_id: {article.id}" + (
            f"; tags: {', '.join(tag_names)}" if tag_names else ""
        )
        body = article.content_md[:_KB_ARTICLE_BODY_CHARS]
        if len(article.content_md) > _KB_ARTICLE_BODY_CHARS:
            body += f"\n[… truncated — full text: kb_get_article({article.id})]"
        parts.append(f"### {article.title} ({meta})\n{body}")
    return "\n\n".join(parts)


def kb_search_fn(
    session: AsyncSession, settings: Settings, user_id: int
) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def _search(query: str, *, limit: int) -> list[dict[str, Any]]:
        from tiqora.kb.service import KbService

        svc = KbService(session, settings)
        try:
            result = await svc.search_agent(user_id, query, limit=limit)
        except Exception:  # noqa: BLE001 — Meilisearch unavailable/unconfigured
            logger.warning("ai_kb_search_unavailable", exc_info=True)
            return []
        finally:
            await svc.close()
        return [
            {"article_id": h.article_id, "title": h.title, "snippet": h.content[:300]}
            for h in result.hits
        ]

    return _search


def kb_get_article_fn(
    session: AsyncSession, settings: Settings, user_id: int
) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    async def _get(article_id: int) -> dict[str, Any] | None:
        from tiqora.kb.service import KbForbidden, KbNotFound, KbService

        svc = KbService(session, settings)
        try:
            article = await svc.get_article_scoped(user_id, article_id)
        except (KbNotFound, KbForbidden):
            return None
        finally:
            await svc.close()
        return {"id": article.id, "title": article.title, "body": article.content_md}

    return _get


__all__ = [
    "kb_bundle",
    "kb_get_article_fn",
    "kb_search_fn",
]
