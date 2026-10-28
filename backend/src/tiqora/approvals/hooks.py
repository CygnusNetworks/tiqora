"""Job ``core.approval_hook`` (A9): decision note and the subject type's ``on_decided``.

The deciding transaction only enqueues the job (dedupe key ``approval:<id>:<status>``); the
consumer hook runs here, after that commit, so a consumer bug can never roll back a vote. The job
does not lock ``tiqora_approval`` before it calls ``on_decided`` (the consumer takes its own locks
first, then the engine's - lock order of the service), and it writes the optional decision note
(a ticket article) only after ``on_decided``: consumer rows before the Znuny ``ticket`` row, as
the contract asks of consumers. One attempt is one transaction: the hook, the optional note and
the ``hook_done`` audit row commit together, so a retry never leaves a second note behind.
Retried up to ``max_attempts`` times (60 s * attempt); the last failure is audited as
``hook_failed`` with the exception class name.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    JOB_HOOK,
    Actor,
    ApprovalOutcome,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import load_approval_settings
from tiqora.extensions.core import get_registry
from tiqora.jobs.context import JobContext, JobFailed, JobRetry
from tiqora.jobs.registry import JobHandler

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS: Final = 5
_RETRY_DELAY_SECONDS: Final = 60
_STATUS_TEXT: Final = {
    "approved": "freigegeben",
    "rejected": "abgelehnt",
    "cancelled": "zurückgezogen",
}
_STEP_STATUS_TEXT: Final = {
    "waiting": "wartet",
    "active": "offen",
    "approved": "freigegeben",
    "rejected": "abgelehnt",
    "cancelled": "zurückgezogen",
}
_REASON_TEXT: Final = {
    "timeout": "Die Frist ist abgelaufen.",
    "no_approvers": "Es gab keine gültigen Freigeber.",
    "superseded": "Die Anfrage wurde durch eine neue Anfrage ersetzt.",
}


class ApprovalHookPayload(BaseModel):
    approval_id: int
    status: Literal["approved", "rejected", "cancelled"]


async def _already_done(session: AsyncSession, approval_id: int) -> bool:
    row = await session.execute(
        select(TiqoraApprovalEvent.id)
        .where(
            TiqoraApprovalEvent.approval_id == approval_id,
            TiqoraApprovalEvent.action == "hook_done",
        )
        .limit(1)
    )
    return row.first() is not None


async def _final_comment(session: AsyncSession, approval: TiqoraApproval) -> str | None:
    """Comment of the vote that decided the request; None for timeout, auto decision, cancel."""
    if approval.reason != "votes" or approval.status == "cancelled":
        return None
    row = await session.execute(
        select(TiqoraApprovalVote.comment)
        .where(TiqoraApprovalVote.approval_id == approval.id)
        .order_by(TiqoraApprovalVote.id.desc())
        .limit(1)
    )
    first = row.first()
    return None if first is None or not first[0] else str(first[0])


async def _outcome(session: AsyncSession, approval: TiqoraApproval) -> ApprovalOutcome:
    assert approval.decided_at is not None
    return ApprovalOutcome(
        approval_id=approval.id,
        ticket_id=approval.ticket_id,
        subject_type=approval.subject_type,
        subject_ref=approval.subject_ref,
        approval_key=approval.approval_key,
        status=approval.status,  # type: ignore[arg-type]
        reason=approval.reason,
        decided_at=approval.decided_at,
        comment=await _final_comment(session, approval),
    )


async def _note_text(session: AsyncSession, approval: TiqoraApproval) -> tuple[str, str]:
    steps = (
        (
            await session.execute(
                select(TiqoraApprovalStep)
                .where(TiqoraApprovalStep.approval_id == approval.id)
                .order_by(TiqoraApprovalStep.position)
            )
        )
        .scalars()
        .all()
    )
    label = _STATUS_TEXT[approval.status]
    subject = f"Freigabe #{approval.id} ({approval.approval_key}): {label}"
    lines = [f"Die Freigabe #{approval.id} ({approval.approval_key}) wurde {label}."]
    reason = _REASON_TEXT.get(approval.reason or "")
    if reason:
        lines.append(reason)
    if steps:
        lines.append("")
        lines.append("Stufen:")
        for step in steps:
            lines.append(f"- Stufe {step.stage}: {_STEP_STATUS_TEXT.get(step.status, step.status)}")
    return subject, "\n".join(lines)


async def _write_note(session: AsyncSession, approval: TiqoraApproval, actor: Actor) -> str:
    """The internal decision note; returns ``written`` or ``skipped``.

    ``add_article`` (module level) does not run the plugin ``article.create`` hooks, so they run
    first exactly as in ``TicketWriteService.add_article``; a veto skips the note, never the hook.
    """
    from tiqora.domain.ticket_write_service import ArticleIn, _article_op, add_article
    from tiqora.plugins import sync_hooks
    from tiqora.znuny.sysconfig import SysConfig

    row = await session.execute(
        text("SELECT id FROM ticket WHERE id = :t"), {"t": approval.ticket_id}
    )
    if row.first() is None:  # F5: the ticket is gone, nothing to write the note on
        return "skipped"
    subject, body = await _note_text(session, approval)
    article = ArticleIn(
        sender_type="system",
        is_visible_for_customer=False,
        subject=subject,
        body=body,
        channel="internal",
    )
    try:
        if sync_hooks.has_sync_hooks("article.create"):
            await sync_hooks.run_sync_hooks(
                "article.create",
                _article_op(approval.ticket_id, article),
                agent_id=actor.write_user_id,
                session=session,
            )
    except sync_hooks.OperationVetoed:
        await audit.record(
            session,
            approval=approval,
            action="note_skipped",
            actor=actor,
            detail={"reason": "vetoed"},
        )
        return "skipped"
    await add_article(
        session,
        ticket_id=approval.ticket_id,
        article=article,
        user_id=actor.write_user_id,
        sysconfig=SysConfig(session),
    )
    return "written"


async def _run_once(
    session: AsyncSession, ctx: JobContext, payload: ApprovalHookPayload
) -> dict[str, Any]:
    approval = (
        await session.execute(
            select(TiqoraApproval).where(TiqoraApproval.id == payload.approval_id)
        )
    ).scalar_one_or_none()
    if approval is None:  # erased with its ticket (GDPR) since the job was queued
        return {"approval_id": payload.approval_id, "hook": "gone"}
    if approval.status == "pending" or approval.status != payload.status:
        raise JobFailed("Der Status der Freigabe passt nicht zum Auftrag.")
    if await _already_done(session, approval.id):
        return {"approval_id": approval.id, "status": approval.status, "hook": "already_done"}

    actor = Actor.system()
    settings = await load_approval_settings(session, ctx.settings)
    subject_type = get_registry().get(APPROVAL_SUBJECT_TYPES, approval.subject_type)
    if subject_type is None:
        raise LookupError(f"subject type not registered: {approval.subject_type}")
    # the consumer's rows first, the ticket (decision note) after: the lock order of the contract
    hook = "none"
    if subject_type.on_decided is not None:
        await subject_type.on_decided(session, await _outcome(session, approval))
        hook = "called"
    note = "off"
    if settings.decision_note:
        note = await _write_note(session, approval, actor)
    await audit.record(
        session,
        approval=approval,
        action="hook_done",
        actor=actor,
        detail={"status": approval.status, "attempt": ctx.attempt},
    )
    return {"approval_id": approval.id, "status": approval.status, "note": note, "hook": hook}


async def _record_failure(
    session: AsyncSession, payload: ApprovalHookPayload, exc: Exception, attempt: int
) -> None:
    approval = (
        await session.execute(
            select(TiqoraApproval).where(TiqoraApproval.id == payload.approval_id)
        )
    ).scalar_one_or_none()
    if approval is None:
        return
    await audit.record(
        session,
        approval=approval,
        action="hook_failed",
        actor=Actor.system(),
        detail={"error": type(exc).__name__, "attempt": attempt},
    )
    await session.commit()


async def run_approval_hook(ctx: JobContext, payload: ApprovalHookPayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        try:
            result = await _run_once(session, ctx, payload)
            await session.commit()
            return result
        except JobFailed:
            await session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - consumer code: any failure is retried
            await session.rollback()
            logger.warning(
                "approval_hook_failed",
                approval_id=payload.approval_id,
                error=type(exc).__name__,
                attempt=ctx.attempt,
            )
            if ctx.attempt < ctx.max_attempts:
                raise JobRetry(
                    "Die Nachbearbeitung der Freigabe wird erneut versucht.",
                    delay_seconds=_RETRY_DELAY_SECONDS * ctx.attempt,
                ) from exc
            try:
                await _record_failure(session, payload, exc, ctx.attempt)
            except Exception:  # noqa: BLE001 - the audit row is best effort
                await session.rollback()
                logger.exception("approval_hook_audit_failed", approval_id=payload.approval_id)
            raise JobFailed("Die Nachbearbeitung der Freigabe ist fehlgeschlagen.") from exc


APPROVAL_HOOK_JOB = JobHandler(
    kind=JOB_HOOK,
    payload_model=ApprovalHookPayload,
    run=run_approval_hook,
    title_key="jobs.kinds.core.approval_hook",
    timeout_seconds=300,
    max_attempts=MAX_ATTEMPTS,
    requeue_if_interrupted=True,
)
