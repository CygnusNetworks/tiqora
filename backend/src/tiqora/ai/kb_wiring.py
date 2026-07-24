"""Shared LLM-client + KB wiring for the agent runtime (plan §3.4 step 7-8).

Extracted from ``tiqora.api.v1.ai`` (Phase B) so the auto-reply worker
(``tiqora.ai.auto_worker``, Phase D) does not copy-paste the same
provider/KB plumbing per queue — both the manual-assist API route and the
worker build a :class:`~tiqora.ai.llm.LlmClient` and the KB
bundle/search/get-article seams the same way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.listfields import parse_int_list, parse_str_list
from tiqora.ai.llm import LlmClient, OpenAiCompatLlmClient
from tiqora.ai.models import TiqoraAiQueuePolicy
from tiqora.ai.providers import get_provider
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret

logger = structlog.get_logger(__name__)

_KB_ARTICLE_BUNDLE_LIMIT = 20
_KB_ARTICLE_BODY_CHARS = 2000


async def build_llm_client(
    session: AsyncSession,
    settings: Settings,
    provider_id: int | None,
    model_override: str | None,
) -> LlmClient:
    """Build the configured :class:`LlmClient` for a queue policy.

    Raises :class:`fastapi.HTTPException` (409) when the policy has no
    provider configured or the provider row is gone — safe to call from an
    HTTP route. The worker (no HTTP context) catches this and treats it as
    a skip/error like any other :class:`Exception`.
    """
    if provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Queue AI policy has no llm_provider_id configured",
        )
    provider = await get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Configured LLM provider no longer exists"
        )
    api_key = (
        decrypt_secret(settings.secret_key, provider.api_key_enc) if provider.api_key_enc else None
    )
    return OpenAiCompatLlmClient(
        base_url=provider.base_url,
        api_key=api_key,
        model=model_override or provider.default_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


async def build_vision_llm_factory(
    session: AsyncSession,
    settings: Settings,
    vision_provider_id: int | None,
    *,
    audit: AuditContext | None = None,
) -> Callable[[], LlmClient] | None:
    """Resolve ``vision_provider_id`` into a sync ``() -> LlmClient`` factory
    for the attachment vision pre-pass (:mod:`tiqora.ai.attachment_context`).

    Returns ``None`` (never raises) when no provider is configured, the
    provider row is gone, or it does not have ``supports_vision`` set — the
    caller treats this as "images are ignored for this run", matching the
    documented "NULL = Bilder werden ignoriert" policy semantics.

    When ``audit`` is given, every call made through the returned client is
    written to ``tiqora_ai_audit_log`` with ``feature="vision"`` (see
    :mod:`tiqora.ai.audit`) — image data-URLs are never persisted, only a
    byte-count placeholder.
    """
    if vision_provider_id is None:
        return None
    provider = await get_provider(session, vision_provider_id)
    if provider is None or not provider.supports_vision:
        return None
    api_key = (
        decrypt_secret(settings.secret_key, provider.api_key_enc) if provider.api_key_enc else None
    )

    def _factory() -> LlmClient:
        client: LlmClient = OpenAiCompatLlmClient(
            base_url=provider.base_url,
            api_key=api_key,
            model=provider.default_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if audit is not None:
            client = AuditingLlmClient(
                client,
                settings=settings,
                context=replace(
                    audit,
                    feature="vision",
                    provider_id=provider.id,
                    model=provider.default_model,
                ),
                session=session,
            )
        return client

    return _factory


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
        header = f"### {article.title}" + (f" (tags: {', '.join(tag_names)})" if tag_names else "")
        parts.append(f"{header}\n{article.content_md[:_KB_ARTICLE_BODY_CHARS]}")
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
    "build_llm_client",
    "build_vision_llm_factory",
    "kb_bundle",
    "kb_get_article_fn",
    "kb_search_fn",
]
