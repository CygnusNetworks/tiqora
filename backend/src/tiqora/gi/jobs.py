"""Job handlers ``core.gi_invoke`` / ``core.gi_invoke_sync`` (G11) - one invoker call per job.

The handler runs the Znuny ``Requester.pm`` pipeline (:func:`tiqora.gi.requester.run_requester`)
for one queued call. Two kinds share the run code and differ in their lane only:

* ``core.gi_invoke`` (lane ``gi``, 2 slots): invokers with ``Asynchronous: 1``, response actions,
  transition actions, resends.
* ``core.gi_invoke_sync`` (lane ``gi_sync``, 1 slot): invokers with ``Asynchronous: 0``; they
  run strictly in job id order, like Znuny's synchronous event handling.

Retries are **new jobs**, exactly like Znuny's scheduler ``TaskAdd`` from ``RequestRetry``: a
failed call that error handling wants repeated enqueues a fresh job (``due_at`` and
``past_execution`` set) and then succeeds itself - it did its work by deciding to retry. The
runner's own retry (``max_attempts``) is only for interruptions before the send checkpoint.

Interruption: the checkpoint ``sending`` is set right before the request leaves
(``before_send``). A job interrupted before it is requeued; one interrupted after it is failed
with :data:`INTERRUPTED_AFTER_SEND_MESSAGE` and never sent again automatically (the remote
system may or may not have seen the request). A call that failed before sending and is retried
gets the checkpoint ``rescheduled`` in the transaction that commits its follow-up job; an
interruption after that fails it with :data:`INTERRUPTED_AFTER_RESCHEDULE_MESSAGE`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import get_settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.gi.debug_store import DbDebugSink
from tiqora.gi.error_handling import ErrorHandlingConfigError, PastExecutionData
from tiqora.gi.loader import load_gi_webservice
from tiqora.gi.requester import run_requester
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job
from tiqora.plugins.gi_points import MAX_INVOKER_TIMEOUT
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

GI_INVOKE_KIND: Final = "core.gi_invoke"
GI_INVOKE_SYNC_KIND: Final = "core.gi_invoke_sync"
GI_LANE: Final = "gi"  # async invokers, 2 slots
GI_SYNC_LANE: Final = "gi_sync"  # Asynchronous: 0 invokers, 1 slot, strict id order
CHECKPOINT_SENDING: Final = "sending"
CHECKPOINT_RESCHEDULED: Final = "rescheduled"  # failed before sending, follow-up job queued

WEBSERVICE_GONE_MESSAGE: Final = "Der Webservice ist nicht mehr gültig."
INTERRUPTED_AFTER_SEND_MESSAGE: Final = (
    "Der Aufruf wurde unterbrochen; ob er angekommen ist, ist unklar."
)
INTERRUPTED_AFTER_RESCHEDULE_MESSAGE: Final = (
    "Der Aufruf ist fehlgeschlagen und wurde danach unterbrochen; ein neuer Versuch ist bereits"
    " eingeplant."
)
FUTURE_TASK_TIME_DIFF_SETTING: Final = (
    "Daemon::SchedulerGenericInterfaceTaskManager::FutureTaskTimeDiff"
)
FUTURE_TASK_TIME_DIFF_DEFAULT: Final = 300
_MAX_ERROR_CHARS: Final = 500

Origin = Literal["event", "transition_action", "response_action", "resend", "dry_run"]


class GiInvokePayload(BaseModel):
    webservice_id: int
    invoker: str
    data: dict[str, Any]  # event data incl. "Event", or ExecuteInvoker data - never secrets
    asynchronous: bool
    origin: Origin
    history_id: int | None = None  # source ticket_history.id (idempotency key, debugger)
    past_execution: dict[str, Any] | None = None  # PastExecutionData.to_json()
    dry_run: bool = False
    # How many OTRS_AsynchronousInvokerExecution hops led to this call (ruling F13; the
    # invoker refuses items at MAX_CHAIN_DEPTH). Reschedules keep it, resends start at 0.
    chain_depth: int = Field(default=0, ge=0)


def _dedupe_key(payload: GiInvokePayload) -> str | None:
    """Only first runs of event jobs carry a key. Reschedules are enqueued while the original
    job is still running: a shared key would return the running job and no retry row would
    ever exist. Resends, dry runs, response and transition actions are never deduplicated."""
    if (
        payload.origin == "event"
        and payload.past_execution is None
        and payload.history_id is not None
    ):
        return f"gi:{payload.history_id}:{payload.webservice_id}:{payload.invoker}"
    return None


async def enqueue_invocation(
    session: AsyncSession,
    *,
    payload: GiInvokePayload,
    created_by: int | None,
    due_at: datetime | None = None,
) -> TiqoraJob:
    """Queue one invoker call (flushes, never commits). ``asynchronous=False`` goes to the
    ``core.gi_invoke_sync`` kind and its single-slot lane."""
    kind = GI_INVOKE_KIND if payload.asynchronous else GI_INVOKE_SYNC_KIND
    return await enqueue_job(
        session,
        kind=kind,
        payload=payload,
        created_by=created_by,
        dedupe_key=_dedupe_key(payload),
        due_at=due_at,
    )


def _requester_extras() -> dict[str, Any]:
    """Extra ``run_requester`` keyword arguments. Production passes none; tests replace this
    function to inject a mock transport, a resolver and an egress policy."""
    return {}


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


async def _future_task_time_diff(session: AsyncSession) -> int:
    raw = await SysConfig(session).get(FUTURE_TASK_TIME_DIFF_SETTING)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return FUTURE_TASK_TIME_DIFF_DEFAULT
    return value if value > 0 else FUTURE_TASK_TIME_DIFF_DEFAULT


def _shorten(message: str) -> str:
    message = " ".join(message.split())
    return message if len(message) <= _MAX_ERROR_CHARS else message[: _MAX_ERROR_CHARS - 1] + "…"


async def run_gi_invoke(ctx: JobContext, payload: GiInvokePayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        ws = await load_gi_webservice(session, SysConfig(session), payload.webservice_id)
    if ws is None:
        raise JobFailed(WEBSERVICE_GONE_MESSAGE)
    try:
        past = PastExecutionData.from_json(payload.past_execution)
    except ErrorHandlingConfigError as exc:
        raise JobFailed(_shorten(f"Die Daten des letzten Versuchs sind ungültig: {exc}")) from exc

    async def enqueue(item: dict[str, Any]) -> None:
        """``OTRS_AsynchronousInvokerExecution``: one more call, queued in its own transaction."""
        async with ctx.session_factory() as session:
            await enqueue_invocation(
                session, payload=GiInvokePayload.model_validate(item), created_by=ctx.created_by
            )
            await session.commit()

    sent = False

    async def before_send() -> None:
        nonlocal sent
        await ctx.checkpoint(CHECKPOINT_SENDING)
        sent = True

    outcome = await run_requester(
        session_factory=ctx.session_factory,
        ws=ws,
        invoker=payload.invoker,
        data=payload.data,
        asynchronous=payload.asynchronous,
        past=past,
        sysconfig_factory=SysConfig,
        sink=DbDebugSink(ctx.session_factory),
        settings=ctx.settings,
        before_send=before_send,
        dry_run=payload.dry_run,
        enqueue=enqueue,
        chain_depth=payload.chain_depth,
        **_requester_extras(),
    )
    if outcome.success:
        if payload.dry_run:
            shown = outcome.data
            if isinstance(shown, dict) and set(shown) == {"request"}:
                shown = shown["request"]  # REST wraps the masked request once more
            return {"communication_id": outcome.communication_id, "request": shown}
        return {"communication_id": outcome.communication_id, "status": "ok"}

    decision = outcome.reschedule
    # A dry run never schedules a follow-up call: it fails with the error shown to the admin.
    if (
        not payload.dry_run
        and decision is not None
        and decision.reschedule
        and decision.past is not None
    ):
        async with ctx.session_factory() as session:
            next_at = decision.execution_time
            if next_at is None:
                next_at = _naive_utc_now() + timedelta(
                    seconds=await _future_task_time_diff(session)
                )
            await enqueue_invocation(
                session,
                payload=payload.model_copy(update={"past_execution": decision.past.to_json()}),
                created_by=ctx.created_by,
                due_at=next_at,
            )
            if not sent:
                # Ruling GI-RESCHEDULED-CHECKPOINT: the mark and the follow-up commit in one
                # transaction. Without the mark an interrupted job is requeued (a second retry
                # chain, sent twice); a mark without the follow-up would lose the retry. A lost
                # claim raises JobCancelled here and the follow-up rolls back with it.
                await ctx.checkpoint(CHECKPOINT_RESCHEDULED, session=session)
            await session.commit()
        return {
            "communication_id": outcome.communication_id,
            "status": "rescheduled",
            "next_at": next_at.isoformat(timespec="seconds"),
        }
    raise JobFailed(_shorten(outcome.error_message or "Der Aufruf ist fehlgeschlagen."))


async def on_gi_invoke_interrupted(ctx: JobContext, payload: GiInvokePayload) -> None:
    """The runner failed the job after an interruption (the generic text talks about restarting
    the job). Past the send checkpoint say that the delivery is unclear; past the
    ``rescheduled`` mark say that the retry is already queued (it committed with the mark)."""
    messages = {
        CHECKPOINT_SENDING: INTERRUPTED_AFTER_SEND_MESSAGE,
        CHECKPOINT_RESCHEDULED: INTERRUPTED_AFTER_RESCHEDULE_MESSAGE,
    }
    async with ctx.session_factory() as session:
        checkpoint = (
            await session.execute(select(TiqoraJob.checkpoint).where(TiqoraJob.id == ctx.job_id))
        ).scalar_one_or_none()
        message = messages.get(checkpoint or "")
        if message is None:
            return
        await session.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == ctx.job_id, TiqoraJob.status == "failed")
            .values(error=message)
        )
        await session.commit()
    logger.warning(
        "gi_invoke_interrupted_after_send"
        if checkpoint == CHECKPOINT_SENDING
        else "gi_invoke_interrupted_after_reschedule",
        job_id=ctx.job_id,
        webservice_id=payload.webservice_id,
        invoker=payload.invoker,
    )


def _handler(kind: str, lane: str) -> JobHandler:
    return JobHandler(
        kind=kind,
        payload_model=GiInvokePayload,
        run=run_gi_invoke,
        title_key="jobs.kinds.core.gi_invoke",
        timeout_seconds=get_settings().gi_max_timeout_seconds + 120 + 2 * MAX_INVOKER_TIMEOUT,
        lane=lane,
        max_attempts=3,
        requeue_if_interrupted=True,
        on_interrupted=on_gi_invoke_interrupted,
        result_private=True,
    )


GI_INVOKE_JOB = _handler(GI_INVOKE_KIND, GI_LANE)
GI_INVOKE_SYNC_JOB = _handler(GI_INVOKE_SYNC_KIND, GI_SYNC_LANE)
