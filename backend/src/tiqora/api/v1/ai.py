"""Agent-facing AI API — ``/api/v1/tickets/{ticket_id}/ai/*`` (plan §Phase B).

Distinct from ``tiqora.api.v1.admin.ai`` (queue policy / provider / MCP admin
CRUD): every route here is used by a normal ticket agent working a ticket,
gated by the same ticket permission check as the rest of ``tickets.py``
(``ro`` to view state, ``note`` to trigger Manual Assist — the same key
:class:`~tiqora.domain.ticket_write_service.TicketWriteService` requires for
posting a reply/note on that queue).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import drafts as ai_drafts
from tiqora.ai import triage as triage_service
from tiqora.ai.acl import check_feature_access
from tiqora.ai.context import (
    article_from_address,
    customer_user_name,
    get_or_create_state,
    latest_customer_article_id,
    load_articles,
)
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.kb_wiring import build_llm_client, kb_bundle, kb_get_article_fn, kb_search_fn
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.llm import LlmEmptyOutputError, LlmError, LlmHttpError, LlmTimeoutError
from tiqora.ai.models import (
    FEATURE_REFINE,
    TRIAGE_STATUS_ACCEPTED,
    TRIAGE_STATUS_OPEN,
    TRIAGE_STATUS_REJECTED,
    TiqoraAiTicketState,
    TiqoraAiTriage,
)
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.ai.refine import (
    MAX_TOTAL_CHARS as REFINE_MAX_TOTAL_CHARS,
)
from tiqora.ai.refine import (
    REFINE_TONES,
    TONE_STANDARD,
    RefineAclDeniedError,
    RefineAclLimitExceededError,
    RefineEmptyOutputError,
    RefineError,
    RefinePolicyDisabledError,
    refine_text,
)
from tiqora.ai.refine import Segment as RefineSegment
from tiqora.ai.runtime import (
    _LOCK_MAX_AGE,
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
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.ticket_service import TicketAccessDenied, TicketNotFound, TicketService
from tiqora.domain.ticket_write_service import TicketWriteService
from tiqora.domain.ticket_write_service import resume_ai_automation as _resume_ai_automation
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tickets/{ticket_id}/ai", tags=["ai"])
# Composer refine is queue-scoped, not ticket-scoped: the New-ticket form
# has no ticket id yet, and the reply dialog can supply one for audit.
refine_router = APIRouter(prefix="/ai", tags=["ai"])

# Manual Assist background runs (nginx-90s-timeout fix, see
# request_manual_draft): asyncio.create_task() does not keep a strong
# reference to the task, so it can be garbage-collected mid-flight — every
# task is added here and discarded via its done-callback to prevent that.
_background_run_tasks: set[asyncio.Task[None]] = set()

# Stale-guard threshold for a manual run stuck in "running" (matches the
# per-ticket run-lock's own stale age — see ai.runtime._acquire_lock).
_MANUAL_RUN_STALE_AGE = _LOCK_MAX_AGE


class AiToolTraceOut(BaseModel):
    name: str
    content: str
    arguments: str | None = None
    """JSON the tool was called with. ``None`` for runs recorded before the
    arguments were kept — those traces show only what a tool returned."""


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
    tool_trace: list[AiToolTraceOut]


class AiTriageOut(BaseModel):
    """A pending triage proposal for this ticket (status ``open`` only).

    Both halves are independent: a ticket can have a queue proposal, a
    customer proposal, or both, and an agent accepts them separately.
    """

    id: int
    status: str
    source_queue_id: int
    suggested_queue_id: int | None = None
    suggested_queue_name: str | None = None
    queue_confidence: int | None = None
    queue_reason: str | None = None
    queue_votes: int | None = None
    extracted_email: str | None = None
    suggested_customer_user_id: str | None = None
    suggested_customer_name: str | None = None
    customer_confidence: int | None = None
    created_at: datetime | None = None


class AiTriageDecisionIn(BaseModel):
    """Which halves of the proposal to accept. Defaults to both."""

    queue: bool = True
    customer: bool = True


class AiTriageRejectIn(BaseModel):
    note: str | None = None


class AiStateOut(BaseModel):
    manual_assist_available: bool
    summary_available: bool
    can_summarize: bool
    operation_mode_ready: bool
    drafts: list[AiDraftOut]
    summary_body: str | None
    last_summary_upto_article_id: int | None
    summary_created_at: datetime | None
    manual_run_status: str | None = None
    manual_run_notes: str | None = None
    manual_run_error_code: str | None = None
    manual_run_started_at: datetime | None = None
    ai_escalated_at: datetime | None = None
    triage: AiTriageOut | None = None


class AiRefineSegmentIn(BaseModel):
    """One run of the composer body, as segmented by the frontend
    (``frontend/src/lib/replyQuote.ts``). Quote segments are sent so the model
    can see what an inline answer refers to; they are never rewritten and
    never come back in the response."""

    kind: Literal["own", "quote"]
    text: str


class AiRefineIn(BaseModel):
    """Exactly one of ``ticket_id`` / ``queue_id`` addresses the queue policy.

    Replying inside a ticket names the ticket and the server reads its queue —
    the composer has no queue of its own, and a client-sent one could disagree
    with the ticket's. The New-ticket form has no ticket yet and names the
    queue the agent picked in the form.
    """

    ticket_id: int | None = None
    queue_id: int | None = None
    #: New-ticket form only: the customer the composer was opened for, so its
    #: name can be PII-masked. With a ticket the names come from the ticket
    #: itself — see ``tiqora.ai.refine._name_masking_inputs``.
    customer_user_id: str | None = None
    tone: str = TONE_STANDARD
    segments: list[AiRefineSegmentIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> AiRefineIn:
        if (self.ticket_id is None) == (self.queue_id is None):
            raise ValueError("Pass exactly one of ticket_id or queue_id")
        if self.customer_user_id is not None and self.ticket_id is not None:
            raise ValueError("customer_user_id is only for the queue_id form")
        return self

    @field_validator("tone")
    @classmethod
    def _known_tone(cls, value: str) -> str:
        if value not in REFINE_TONES:
            raise ValueError(f"Unknown tone {value!r}")
        return value

    @model_validator(mode="after")
    def _within_size_cap(self) -> AiRefineIn:
        total = sum(len(s.text) for s in self.segments)
        if total > REFINE_MAX_TOTAL_CHARS:
            raise ValueError(f"Text too long to refine ({total} characters)")
        return self


class AiRefineSectionOut(BaseModel):
    #: Index of the segment this text replaces, as sent in the request.
    id: int
    text: str


class AiRefineOut(BaseModel):
    sections: list[AiRefineSectionOut]


class AiRefineAvailabilityOut(BaseModel):
    available: bool


class AiSummarizeIn(BaseModel):
    """Per-run scope choice — the agent picks it in the ticket's AI panel;
    ``None`` falls back to the queue policy's ``summary_detail``."""

    detail: Literal["standard", "detailed"] | None = None


class AiSummarizeOut(BaseModel):
    status: str
    summary_body: str | None = None
    upto_article_id: int | None = None


class AiDraftRequestOut(BaseModel):
    status: str
    draft_id: int | None = None
    article_id: int | None = None
    notes: str | None = None


def parse_tool_trace(raw: str | None) -> list[AiToolTraceOut]:
    """Parse the stored tool-message trace of a draft into display items.

    The trace is the list of ``role == "tool"`` wire messages recorded when
    the draft was created (see :mod:`tiqora.ai.runtime`). It is shown to the
    *agent* alongside the draft — it must never become part of the article
    body a customer could see (the accept flow only ever uses the body the
    agent submits). Malformed/legacy payloads degrade to an empty list.
    """
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    out: list[AiToolTraceOut] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        name = item.get("name")
        arguments = item.get("arguments")
        out.append(
            AiToolTraceOut(
                name=name if isinstance(name, str) else "tool",
                content=content,
                arguments=arguments if isinstance(arguments, str) else None,
            )
        )
    return out


def _draft_out(draft: object) -> AiDraftOut:
    fields = {f: getattr(draft, f) for f in AiDraftOut.model_fields if f != "tool_trace"}
    fields["tool_trace"] = parse_tool_trace(getattr(draft, "tool_trace_json", None))
    return AiDraftOut.model_validate(fields)


def _run_error_code(exc: AgentRunError | LlmError) -> str:
    """Stable error-code classification for a run-abort/LLM-client error —
    shared by :func:`_map_run_error` (synchronous-request detail prefix) and
    the background-task status write in :func:`_finish_manual_run` (the
    ``manual_run_error_code`` column the frontend polls). Every branch here
    must match the corresponding branch there — see ``AiPanel.mapRunError``.
    """
    if isinstance(exc, LockHeldError):
        return "ai_run_locked"
    # LlmEmptyOutputError is a subclass of LlmError — checked first here so
    # it takes the specific branch instead of the generic LlmError catch-all.
    if isinstance(exc, LlmEmptyOutputError):
        return "llm_empty_output"
    if isinstance(exc, LlmTimeoutError):
        return "llm_timeout"
    if isinstance(exc, LlmError):
        return "llm_provider_error"
    return "internal_error"


def _map_run_error(exc: AgentRunError | LlmError) -> HTTPException:
    """Map a run-abort/LLM-client error to an HTTPException with a stable
    ``detail`` code prefix (``"<code>: <human text>"``) the frontend matches
    on for a specific i18n message — see ``AiPanel.mapRunError``. There is no
    existing structured-detail convention elsewhere in this API (plain
    strings only), so a string prefix is used rather than introducing a new
    JSON-detail shape just for this route.
    """
    if isinstance(exc, LockHeldError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=f"ai_run_locked: {exc}")
    if isinstance(exc, AclLimitExceededError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, AclDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, PolicyDisabledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, LlmEmptyOutputError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"llm_empty_output: {exc}"
        )
    if isinstance(exc, LlmTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=f"llm_timeout: {exc}"
        )
    if isinstance(exc, LlmHttpError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm_provider_error: HTTP {exc.status_code}: {exc}",
        )
    if isinstance(exc, LlmError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"llm_provider_error: {exc}"
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _map_summary_error(exc: SummaryError) -> HTTPException:
    if isinstance(exc, SummaryAclLimitExceededError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, SummaryAclDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, SummaryPolicyDisabledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _map_refine_error(exc: RefineError) -> HTTPException:
    """Structured ``"<code>: <message>"`` detail — the composer matches on the
    prefix to show a specific hint instead of the generic failure text."""
    if isinstance(exc, RefineAclLimitExceededError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, RefineAclDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, RefinePolicyDisabledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"refine_disabled: {exc}")
    if isinstance(exc, RefineEmptyOutputError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"refine_empty_output: {exc}"
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


async def _assert_note_permission(session: DbSession, user_id: int, queue_id: int) -> None:
    if not await PermissionEngine(session).check(user_id, queue_id, "note"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _open_triage_out(session: DbSession, ticket_id: int) -> AiTriageOut | None:
    """The pending triage proposal for this ticket, if any.

    Only ``open`` rows are exposed: ``applied`` already happened, and
    ``accepted``/``rejected``/``no_action``/``error`` are history the ticket
    UI has nothing to ask about.
    """
    row = (
        await session.execute(
            select(TiqoraAiTriage).where(
                TiqoraAiTriage.ticket_id == ticket_id,
                TiqoraAiTriage.status == TRIAGE_STATUS_OPEN,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    queue_name: str | None = None
    if row.suggested_queue_id is not None:
        queue_name = (
            await session.execute(
                sa_text("SELECT name FROM queue WHERE id = :qid LIMIT 1"),
                {"qid": row.suggested_queue_id},
            )
        ).scalar_one_or_none()

    votes: int | None = None
    if row.candidates_json and row.suggested_queue_id is not None:
        try:
            wanted = triage_service.queue_key(row.suggested_queue_id)
            votes = next(
                (
                    int(entry.get("votes", 0))
                    for entry in json.loads(row.candidates_json)
                    if entry.get("key") == wanted
                ),
                None,
            )
        except (TypeError, ValueError):
            votes = None

    customer_name: str | None = None
    if row.suggested_customer_user_id:
        first, last = await customer_user_name(session, row.suggested_customer_user_id)
        customer_name = " ".join(p for p in (first, last) if p) or None

    return AiTriageOut(
        id=row.id,
        status=row.status,
        source_queue_id=row.source_queue_id,
        suggested_queue_id=row.suggested_queue_id,
        suggested_queue_name=queue_name,
        queue_confidence=row.queue_confidence,
        queue_reason=row.queue_reason,
        queue_votes=votes,
        extracted_email=row.extracted_email,
        suggested_customer_user_id=row.suggested_customer_user_id,
        suggested_customer_name=customer_name,
        customer_confidence=row.customer_confidence,
        created_at=row.create_time,
    )


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

    manual_run_status = state.manual_run_status if state else None
    manual_run_notes = state.manual_run_notes if state else None
    manual_run_error_code = state.manual_run_error_code if state else None
    manual_run_started_at = state.manual_run_started_at if state else None
    # Stale-guard: a run stuck in "running" (background task crashed without
    # writing an outcome, e.g. the process was killed) is reported as an
    # error rather than polled forever — never written back to the DB, since
    # the background task itself might still land its own outcome later.
    if (
        manual_run_status == "running"
        and manual_run_started_at is not None
        and datetime.now(UTC).replace(tzinfo=None) - manual_run_started_at > _MANUAL_RUN_STALE_AGE
    ):
        manual_run_status = "error"
        manual_run_error_code = "internal_error"

    return AiStateOut(
        manual_assist_available=manual_available,
        summary_available=summary_available,
        can_summarize=can_summarize,
        operation_mode_ready=ready,
        drafts=[_draft_out(d) for d in drafts],
        summary_body=state.summary_body if state else None,
        last_summary_upto_article_id=state.last_summary_upto_article_id if state else None,
        summary_created_at=state.summary_created_at if state else None,
        manual_run_status=manual_run_status,
        manual_run_notes=manual_run_notes,
        manual_run_error_code=manual_run_error_code,
        manual_run_started_at=manual_run_started_at,
        ai_escalated_at=state.ai_escalated_at if state else None,
        triage=await _open_triage_out(session, ticket_id),
    )


async def _write_manual_run_state(
    session: AsyncSession,
    ticket_id: int,
    *,
    run_status: str | None,
    notes: str | None,
    error_code: str | None,
) -> None:
    state = await session.get(TiqoraAiTicketState, ticket_id)
    if state is None:
        return
    state.manual_run_status = run_status
    state.manual_run_notes = notes
    state.manual_run_error_code = error_code
    await session.commit()


async def _finish_manual_run(
    ticket_id: int,
    *,
    run_session: AsyncSession,
    run_status: str | None,
    notes: str | None,
    error_code: str | None,
) -> None:
    """Write the manual-run outcome. Prefers the run's own session (already
    open, no extra connection) but falls back to a fresh one from
    :func:`~tiqora.db.engine.get_session_factory` when that session is
    unusable — e.g. it was left in a failed-transaction state by the
    exception that ended the run, or its request-scoped lifetime already
    ended somehow.
    """
    try:
        await _write_manual_run_state(
            run_session, ticket_id, run_status=run_status, notes=notes, error_code=error_code
        )
        return
    except Exception:  # noqa: BLE001 — fall back to a fresh session below
        logger.warning(
            "ai_manual_run_status_write_failed_on_run_session",
            ticket_id=ticket_id,
            exc_info=True,
        )
    factory = get_session_factory()
    async with factory() as fresh_session:
        await _write_manual_run_state(
            fresh_session, ticket_id, run_status=run_status, notes=notes, error_code=error_code
        )


async def _run_manual_draft_background(
    *, ticket_id: int, queue_id: int, user_id: int, settings: Settings, run_id: str
) -> None:
    """Manual Assist's actual agent run (nginx-90s-timeout fix): started via
    ``asyncio.create_task`` from :func:`request_manual_draft` right after
    that route has already returned its ``"started"`` response, so it needs
    its own DB session — the request-scoped one is closed by then.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            policy = await get_queue_policy_by_queue(session, queue_id)
            if policy is None or not policy.enabled_manual_assist:
                raise PolicyDisabledError(f"Manual Assist is disabled for queue {queue_id}")
            llm = await build_llm_client(
                session,
                settings,
                policy.llm_provider_id,
                policy.model_override,
                policy.llm_fallback_json,
            )
            bundle = await kb_bundle(session, settings, user_id, policy)
            result: AgentRunResult = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=ticket_id,
                trigger=TRIGGER_MANUAL,
                acting_user_id=user_id,
                run_id=run_id,
                worker_instance="api",
                kb_bundle=bundle,
                kb_search_fn=kb_search_fn(session, settings, user_id),
                kb_get_article_fn=kb_get_article_fn(session, settings, user_id),
            )
        except (AgentRunError, LlmError, HTTPException) as exc:
            error_code = (
                _run_error_code(exc)
                if isinstance(exc, AgentRunError | LlmError)
                else "internal_error"
            )
            logger.warning(
                "ai_manual_draft_background_run_failed",
                ticket_id=ticket_id,
                error=str(exc),
                error_code=error_code,
            )
            await _finish_manual_run(
                ticket_id,
                run_session=session,
                run_status="error",
                notes=str(exc),
                error_code=error_code,
            )
            return
        except Exception as exc:  # noqa: BLE001 — background task must never crash silently
            logger.exception("ai_manual_draft_background_unexpected_error", ticket_id=ticket_id)
            await _finish_manual_run(
                ticket_id,
                run_session=session,
                run_status="error",
                notes=str(exc),
                error_code="internal_error",
            )
            return

        await _finish_manual_run(
            ticket_id,
            run_session=session,
            run_status=result.status,
            notes=result.notes,
            error_code=None,
        )


@router.post("/draft", response_model=AiDraftRequestOut, status_code=status.HTTP_200_OK)
async def request_manual_draft(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> AiDraftRequestOut:
    """Manual Assist: kick off the agent run in the background and return
    immediately.

    Hetzner-hosted reasoning models can take 4-7 minutes per run — long past
    nginx's ``proxy_read_timeout 90s`` in front of this API — so the run
    itself happens in an ``asyncio.create_task`` (see
    :func:`_run_manual_draft_background`) started *after* the pre-flight
    checks and the lock below, both still synchronous so a second POST while
    a run is in flight gets a deterministic 423 rather than racing the
    background task. Always draft-path (plan §3.4) — never sends a
    customer-visible article, regardless of the queue's autonomy setting.
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

    state = await get_or_create_state(session, ticket_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    lock_fresh = (
        state.run_lock_owner is not None
        and state.run_lock_at is not None
        and (now - state.run_lock_at) < _LOCK_MAX_AGE
    )
    manual_run_fresh = (
        state.manual_run_status == "running"
        and state.manual_run_started_at is not None
        and (now - state.manual_run_started_at) < _MANUAL_RUN_STALE_AGE
    )
    if lock_fresh or manual_run_fresh:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"ai_run_locked: A Manual Assist run is already in progress for ticket {ticket_id}"
            ),
        )

    state.manual_run_status = "running"
    state.manual_run_started_at = now
    state.manual_run_notes = None
    state.manual_run_error_code = None
    await session.commit()

    run_id = uuid.uuid4().hex
    task = asyncio.create_task(
        _run_manual_draft_background(
            ticket_id=ticket_id,
            queue_id=ticket.queue_id,
            user_id=user.id,
            settings=settings,
            run_id=run_id,
        )
    )
    _background_run_tasks.add(task)
    task.add_done_callback(_background_run_tasks.discard)

    return AiDraftRequestOut(status="started", draft_id=None, article_id=None, notes=None)


@router.post("/summarize", response_model=AiSummarizeOut, status_code=status.HTTP_200_OK)
async def request_summarize(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    body: AiSummarizeIn | None = None,
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

    llm = await build_llm_client(
        session, settings, policy.llm_provider_id, policy.model_override, policy.llm_fallback_json
    )

    try:
        result: SummaryResult = await summarize_ticket(
            session,
            llm=llm,
            ticket_id=ticket_id,
            trigger=SUMMARY_TRIGGER_MANUAL,
            acting_user_id=user.id,
            detail=body.detail if body else None,
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


async def _load_open_triage(
    session: DbSession, *, ticket_id: int, triage_id: int
) -> TiqoraAiTriage:
    row = await session.get(TiqoraAiTriage, triage_id)
    if row is None or row.ticket_id != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Triage not found")
    return row


@router.post("/triage/{triage_id}/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_ai_triage(
    ticket_id: int,
    triage_id: int,
    user: CurrentUser,
    session: DbSession,
    body: AiTriageDecisionIn | None = None,
) -> None:
    """Apply a pending triage proposal, in whole or in half.

    Applied with **the agent's own permissions**, not the AI service user's:
    the worker's automatic path deliberately bypasses the ``move_into``
    check (the admin-configured target allowlist is its authorization), but
    a human confirming an action must be checked like any other human
    action. A 403 here therefore means the agent may not move into that
    queue, even though the worker could have.

    Idempotent: a proposal that is no longer ``open`` returns 204 without
    doing anything, so a double click cannot move a ticket twice.
    """
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    row = await _load_open_triage(session, ticket_id=ticket_id, triage_id=triage_id)
    if row.status != TRIAGE_STATUS_OPEN:
        return

    decision = body or AiTriageDecisionIn()
    writer = TicketWriteService(session, get_session_factory(), SysConfig(session))

    async def _move(new_queue_id: int) -> None:
        await writer.move_queue(user.id, ticket_id, new_queue_id)

    async def _set_customer(customer_id: str | None, customer_user_id: str | None) -> None:
        await writer.set_customer(
            user.id,
            ticket_id,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
        )

    try:
        applied = await triage_service.apply_decision(
            session,
            sysconfig=SysConfig(session),
            row=row,
            acting_user_id=user.id,
            apply_queue=decision.queue,
            apply_customer=decision.customer,
            move_fn=_move,
            set_customer_fn=_set_customer,
        )
    except TicketAccessDenied as exc:
        # The agent may note on the source queue but not move into the target.
        # The worker's automatic path would have succeeded here (it uses the
        # permission-free mutator, authorized by the admin's target
        # allowlist) -- this asymmetry is deliberate and surfaced in the
        # admin UI as a warning when configuring the allowlist.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    row.status = TRIAGE_STATUS_ACCEPTED
    row.decided_by_user_id = user.id
    row.decided_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    logger.info(
        "ai_triage_accepted", ticket_id=ticket_id, triage_id=triage_id, applied=applied
    )


@router.post("/triage/{triage_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_ai_triage(
    ticket_id: int,
    triage_id: int,
    user: CurrentUser,
    session: DbSession,
    body: AiTriageRejectIn | None = None,
) -> None:
    """Dismiss a triage proposal. The row stays — rejections are what
    calibrate the confidence thresholds."""
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    row = await _load_open_triage(session, ticket_id=ticket_id, triage_id=triage_id)
    if row.status != TRIAGE_STATUS_OPEN:
        return

    row.status = TRIAGE_STATUS_REJECTED
    row.decided_by_user_id = user.id
    row.decided_note = (body.note if body else None) or None
    row.decided_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()


@router.post("/resume", status_code=status.HTTP_204_NO_CONTENT)
async def resume_ai_route(ticket_id: int, user: CurrentUser, session: DbSession) -> None:
    """Manually clear the AI->human handoff flag so auto-reply can resume.

    Normally only a human agent's customer-visible reply (or the ticket
    closing) clears ``ai_escalated_at`` — see
    :func:`tiqora.domain.ticket_write_service.resume_ai_automation`. This is
    the explicit override for when a human decides the ticket is safe to
    hand back without writing a customer-visible reply, e.g. after fixing
    the underlying issue that caused a bad escalation.
    """
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    await _assert_note_permission(session, user.id, ticket.queue_id)

    await _resume_ai_automation(
        session,
        ticket_id=ticket_id,
        user_id=user.id,
        sysconfig=SysConfig(session),
    )
    await session.commit()


async def _resolve_refine_queue(
    session: DbSession, user_id: int, *, ticket_id: int | None, queue_id: int | None
) -> int:
    """Queue whose AI policy governs this composer, after the write permission
    the composer would eventually need: ``note`` for a reply on an existing
    ticket, ``create`` for the New-ticket form.

    With a ticket, its own queue is authoritative — the caller never supplies
    one (see :class:`AiRefineIn`), so the policy can't be shopped for.
    """
    if ticket_id is not None:
        ticket = await TicketService(session).get_ticket(user_id, ticket_id)
        queue_id = ticket.queue_id
        key = "note"
    elif queue_id is not None:
        key = "create"
    else:  # pragma: no cover — AiRefineIn and the query-param check enforce this
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pass exactly one of ticket_id or queue_id",
        )
    if not await PermissionEngine(session).check(user_id, queue_id, key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return queue_id


@refine_router.get("/refine/availability", response_model=AiRefineAvailabilityOut)
async def refine_availability(
    user: CurrentUser,
    session: DbSession,
    ticket_id: int | None = None,
    queue_id: int | None = None,
) -> AiRefineAvailabilityOut:
    """Whether to offer the "refine" button at all. Answers ``False`` rather
    than raising for a queue the agent may not write to, or a ticket they
    cannot see — the button is simply absent, which is all the composer needs
    to know."""
    if (ticket_id is None) == (queue_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pass exactly one of ticket_id or queue_id",
        )
    try:
        resolved = await _resolve_refine_queue(
            session, user.id, ticket_id=ticket_id, queue_id=queue_id
        )
    except (TicketNotFound, TicketAccessDenied, HTTPException):
        return AiRefineAvailabilityOut(available=False)

    policy = await get_queue_policy_by_queue(session, resolved)
    if policy is None or not policy.enabled_refine:
        return AiRefineAvailabilityOut(available=False)
    return AiRefineAvailabilityOut(
        available=await check_feature_access(session, user.id, FEATURE_REFINE)
    )


@refine_router.post("/refine", response_model=AiRefineOut, status_code=status.HTTP_200_OK)
async def request_refine(
    body: AiRefineIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> AiRefineOut:
    """Rewrite the agent's own text in the composer (plan "Text verfeinern").

    Stateless: nothing is written to the ticket, and quoted text is returned
    to nobody — only the ``own`` sections come back, keyed by the index they
    replace, so the composer can re-assemble the body around the untouched
    quotes itself.
    """
    try:
        queue_id = await _resolve_refine_queue(
            session, user.id, ticket_id=body.ticket_id, queue_id=body.queue_id
        )
    except (TicketNotFound, TicketAccessDenied) as exc:
        if isinstance(exc, TicketNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc

    policy = await get_queue_policy_by_queue(session, queue_id)
    if policy is None or not policy.enabled_refine:
        raise _map_refine_error(
            RefinePolicyDisabledError(f"Refine is disabled for queue {queue_id}")
        )

    llm = await build_llm_client(
        session, settings, policy.llm_provider_id, policy.model_override, policy.llm_fallback_json
    )

    try:
        result = await refine_text(
            session,
            llm=llm,
            queue_id=queue_id,
            segments=[RefineSegment(kind=s.kind, text=s.text) for s in body.segments],
            tone=body.tone,
            acting_user_id=user.id,
            ticket_id=body.ticket_id,
            customer_user_id=body.customer_user_id,
            settings=settings,
        )
    except RefineError as exc:
        raise _map_refine_error(exc) from exc
    except (LlmTimeoutError, LlmHttpError, LlmEmptyOutputError, LlmError) as exc:
        raise _map_run_error(exc) from exc

    return AiRefineOut(
        sections=[AiRefineSectionOut(id=sid, text=text) for sid, text in result.sections.items()]
    )


__all__ = ["refine_router", "router"]
