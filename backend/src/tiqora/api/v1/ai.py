"""Agent-facing AI API — ``/api/v1/tickets/{ticket_id}/ai/*`` (plan §Phase B).

Distinct from ``tiqora.api.v1.admin.ai`` (queue policy / provider / MCP admin
CRUD): every route here is used by a normal ticket agent working a ticket,
gated by the same ticket permission check as the rest of ``tickets.py``
(``ro`` to view state, ``note`` to trigger Manual Assist — the same key
:class:`~tiqora.domain.ticket_write_service.TicketWriteService` requires for
posting a reply/note on that queue).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.ai import drafts as ai_drafts
from tiqora.ai.acl import check_feature_access
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.llm import LlmClient, OpenAiCompatLlmClient
from tiqora.ai.models import TiqoraAiQueuePolicy, TiqoraAiTicketState
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.ai.providers import get_provider
from tiqora.ai.runtime import (
    TRIGGER_MANUAL,
    AclDeniedError,
    AclLimitExceededError,
    AgentRunError,
    AgentRunResult,
    LockHeldError,
    PolicyDisabledError,
    run_ticket_agent,
)
from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.crypto.secret import decrypt_secret
from tiqora.domain.ticket_service import TicketAccessDenied, TicketNotFound, TicketService
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tickets/{ticket_id}/ai", tags=["ai"])

_KB_ARTICLE_BUNDLE_LIMIT = 20
_KB_ARTICLE_BODY_CHARS = 2000


class AiDraftOut(BaseModel):
    id: int
    ticket_id: int
    kind: str
    subject: str | None
    body: str
    based_on_article_id: int | None
    status: str
    source: str
    accepted_article_id: int | None


class AiStateOut(BaseModel):
    manual_assist_available: bool
    summary_available: bool
    operation_mode_ready: bool
    drafts: list[AiDraftOut]
    summary_body: str | None
    last_summary_upto_article_id: int | None


class AiDraftRequestOut(BaseModel):
    status: str
    draft_id: int | None = None
    article_id: int | None = None
    notes: str | None = None


def _draft_out(draft: object) -> AiDraftOut:
    return AiDraftOut.model_validate(draft, from_attributes=True)


def _map_run_error(exc: AgentRunError) -> HTTPException:
    if isinstance(exc, LockHeldError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    if isinstance(exc, AclLimitExceededError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, AclDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, PolicyDisabledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


async def _assert_note_permission(session: DbSession, user_id: int, queue_id: int) -> None:
    if not await PermissionEngine(session).check(user_id, queue_id, "note"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("", response_model=AiStateOut)
async def get_ai_state(ticket_id: int, user: CurrentUser, session: DbSession) -> AiStateOut:
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    ready = await is_tiqora_primary(session)

    manual_available = False
    summary_available = False
    if policy is not None and ready:
        if policy.enabled_manual_assist:
            manual_available = await check_feature_access(session, user.id, "manual_assist")
        if policy.enabled_summary:
            summary_available = await check_feature_access(session, user.id, "summary")

    drafts = await ai_drafts.list_for_ticket(session, ticket_id)
    state = await session.get(TiqoraAiTicketState, ticket_id)

    return AiStateOut(
        manual_assist_available=manual_available,
        summary_available=summary_available,
        operation_mode_ready=ready,
        drafts=[_draft_out(d) for d in drafts],
        summary_body=state.summary_body if state else None,
        last_summary_upto_article_id=state.last_summary_upto_article_id if state else None,
    )


async def _build_llm_client(
    session: DbSession,
    settings: AppSettings,
    provider_id: int | None,
    model_override: str | None,
) -> LlmClient:
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
    )


async def _kb_bundle(
    session: DbSession, settings: AppSettings, user_id: int, policy: TiqoraAiQueuePolicy
) -> str | None:
    """Small tag/category-bound knowledge bundle (plan §3.4 step 7 "hybrid").

    Uses :meth:`KbService.get_knowledge`, which never touches Meilisearch
    (pure SQL by tags/category) — safe to call unconditionally.
    """
    tags = json.loads(policy.kb_tags) if policy.kb_tags else None
    category_ids = json.loads(policy.kb_category_ids) if policy.kb_category_ids else None
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


def _kb_search_fn(
    session: DbSession, settings: AppSettings, user_id: int
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


def _kb_get_article_fn(
    session: DbSession, settings: AppSettings, user_id: int
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


@router.post("/draft", response_model=AiDraftRequestOut, status_code=status.HTTP_201_CREATED)
async def request_manual_draft(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> AiDraftRequestOut:
    """Manual Assist: run the agent synchronously and return the outcome.

    Always draft-path (plan §3.4) — never sends a customer-visible article,
    regardless of the queue's autonomy setting.
    """
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or not policy.enabled_manual_assist:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Manual Assist is disabled for this queue"
        )

    llm = await _build_llm_client(session, settings, policy.llm_provider_id, policy.model_override)
    kb_bundle = await _kb_bundle(session, settings, user.id, policy)

    try:
        result: AgentRunResult = await run_ticket_agent(
            session,
            settings=settings,
            llm=llm,
            ticket_id=ticket_id,
            trigger=TRIGGER_MANUAL,
            acting_user_id=user.id,
            run_id=uuid.uuid4().hex,
            worker_instance="api",
            kb_bundle=kb_bundle,
            kb_search_fn=_kb_search_fn(session, settings, user.id),
            kb_get_article_fn=_kb_get_article_fn(session, settings, user.id),
        )
    except AgentRunError as exc:
        raise _map_run_error(exc) from exc

    return AiDraftRequestOut(
        status=result.status,
        draft_id=result.draft_id,
        article_id=result.article_id,
        notes=result.notes,
    )


@router.post("/drafts/{draft_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
async def discard_ai_draft(
    ticket_id: int, draft_id: int, user: CurrentUser, session: DbSession
) -> None:
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    draft = await ai_drafts.get_draft(session, draft_id)
    if draft is None or draft.ticket_id != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    try:
        await ai_drafts.discard_draft(session, draft, actor_user_id=user.id)
    except ai_drafts.DraftStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


__all__ = ["router"]
