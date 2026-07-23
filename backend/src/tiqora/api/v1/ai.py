"""Agent-facing AI API — ``/api/v1/tickets/{ticket_id}/ai/*`` (plan §Phase B).

Distinct from ``tiqora.api.v1.admin.ai`` (queue policy / provider / MCP admin
CRUD): every route here is used by a normal ticket agent working a ticket,
gated by the same ticket permission check as the rest of ``tickets.py``
(``ro`` to view state, ``note`` to trigger Manual Assist — the same key
:class:`~tiqora.domain.ticket_write_service.TicketWriteService` requires for
posting a reply/note on that queue).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.ai import drafts as ai_drafts
from tiqora.ai.acl import check_feature_access
from tiqora.ai.context import article_from_address, latest_customer_article_id, load_articles
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.kb_wiring import build_llm_client, kb_bundle, kb_get_article_fn, kb_search_fn
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.models import TiqoraAiTicketState
from tiqora.ai.policies import get_queue_policy_by_queue
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
from tiqora.ai.senders import matches_ignored
from tiqora.ai.summary import TRIGGER_MANUAL as SUMMARY_TRIGGER_MANUAL
from tiqora.ai.summary import (
    SummaryAclDeniedError,
    SummaryAclLimitExceededError,
    SummaryError,
    SummaryPolicyDisabledError,
    SummaryResult,
    summarize_ticket,
)
from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.domain.ticket_service import TicketAccessDenied, TicketNotFound, TicketService
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tickets/{ticket_id}/ai", tags=["ai"])


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
    create_time: datetime


class AiStateOut(BaseModel):
    manual_assist_available: bool
    summary_available: bool
    can_summarize: bool
    operation_mode_ready: bool
    drafts: list[AiDraftOut]
    summary_body: str | None
    last_summary_upto_article_id: int | None
    summary_created_at: datetime | None


class AiSummarizeOut(BaseModel):
    status: str
    summary_body: str | None = None
    upto_article_id: int | None = None


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


def _map_summary_error(exc: SummaryError) -> HTTPException:
    if isinstance(exc, SummaryAclLimitExceededError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, SummaryAclDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, SummaryPolicyDisabledError):
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
    # operation_mode_ready only describes whether *auto-reply* may run (plan
    # §3.0 v1.1 relaxation, Phase E) — Manual Assist and Summary availability
    # no longer depend on it, since neither is gated.
    ready = await is_tiqora_primary(session)

    manual_available = False
    summary_available = False
    if policy is not None:
        if policy.enabled_manual_assist:
            manual_available = await check_feature_access(session, user.id, "manual_assist")
        if policy.enabled_summary:
            summary_available = await check_feature_access(session, user.id, "summary")

    drafts = await ai_drafts.list_for_ticket(session, ticket_id)
    state = await session.get(TiqoraAiTicketState, ticket_id)

    can_summarize = False
    if summary_available:
        upto = state.last_summary_upto_article_id if state else None
        articles = await load_articles(session, ticket_id)
        can_summarize = any(a.id > upto for a in articles) if upto is not None else bool(articles)

    return AiStateOut(
        manual_assist_available=manual_available,
        summary_available=summary_available,
        can_summarize=can_summarize,
        operation_mode_ready=ready,
        drafts=[_draft_out(d) for d in drafts],
        summary_body=state.summary_body if state else None,
        last_summary_upto_article_id=state.last_summary_upto_article_id if state else None,
        summary_created_at=state.summary_created_at if state else None,
    )


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

    if policy.ignore_senders_manual:
        ignored_senders = parse_str_list(policy.ignored_senders)
        if ignored_senders:
            latest_id = await latest_customer_article_id(session, ticket_id)
            from_address = await article_from_address(session, latest_id) if latest_id else None
            if matches_ignored(from_address, ignored_senders):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Sender is on the ignored-senders list for this queue",
                )

    llm = await build_llm_client(session, settings, policy.llm_provider_id, policy.model_override)
    bundle = await kb_bundle(session, settings, user.id, policy)

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
            kb_bundle=bundle,
            kb_search_fn=kb_search_fn(session, settings, user.id),
            kb_get_article_fn=kb_get_article_fn(session, settings, user.id),
        )
    except AgentRunError as exc:
        raise _map_run_error(exc) from exc

    return AiDraftRequestOut(
        status=result.status,
        draft_id=result.draft_id,
        article_id=result.article_id,
        notes=result.notes,
    )


@router.post("/summarize", response_model=AiSummarizeOut, status_code=status.HTTP_200_OK)
async def request_summarize(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> AiSummarizeOut:
    """Manual "Zusammenfassen" trigger (plan §3.5) — state-only, never an
    article/note. Reuses the same ``note`` permission as Manual Assist since
    it is likewise an agent action on the ticket."""
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or not policy.enabled_summary:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Summary is disabled for this queue"
        )

    llm = await build_llm_client(session, settings, policy.llm_provider_id, policy.model_override)

    try:
        result: SummaryResult = await summarize_ticket(
            session,
            llm=llm,
            ticket_id=ticket_id,
            trigger=SUMMARY_TRIGGER_MANUAL,
            acting_user_id=user.id,
        )
    except SummaryError as exc:
        raise _map_summary_error(exc) from exc

    return AiSummarizeOut(
        status=result.status,
        summary_body=result.summary_body,
        upto_article_id=result.upto_article_id,
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
