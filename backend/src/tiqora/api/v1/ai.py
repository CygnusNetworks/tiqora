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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import drafts as ai_drafts
from tiqora.ai.acl import check_feature_access
from tiqora.ai.context import (
    article_from_address,
    get_or_create_state,
    latest_customer_article_id,
    load_articles,
)
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.kb_wiring import build_llm_client, kb_bundle, kb_get_article_fn, kb_search_fn
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.llm import LlmEmptyOutputError, LlmError, LlmHttpError, LlmTimeoutError
from tiqora.ai.models import TiqoraAiTicketState
from tiqora.ai.policies import get_queue_policy_by_queue
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
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tickets/{ticket_id}/ai", tags=["ai"])

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
        out.append(AiToolTraceOut(name=name if isinstance(name, str) else "tool", content=content))
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


__all__ = ["router"]
