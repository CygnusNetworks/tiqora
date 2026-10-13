"""Linked-articles job (I10): a note or a reply on every ticket linked to a source ticket.

Used by "note to linked tickets" (``POST /tickets/{id}/linked-articles``), major-incident
updates and "close incidents with workaround". The job writes through
:class:`~tiqora.domain.ticket_write_service.TicketWriteService`, so history, events and
the plugins' sync before-hooks come from the write services (R16: a veto skips that one
target with reason ``vetoed`` and the veto text as detail; nothing of it is written or sent).

Per target ticket, in its own transaction: re-check that it is still linked to the source
and that the acting user holds ``rw`` and ``note`` on its queue, write the article (a reply
goes out as e-mail to the ticket's customer), optionally change the state, record the target
in ``tiqora_itsm_bulk_target`` and commit - the article and the progress row are one
transaction. A re-run (or a restart after the checkpoint) skips every target that already
has a row, so customer e-mails are never sent twice.

Interruption: the checkpoint ``first_write`` is set inside the first target's transaction.
A job interrupted before it is requeued (``max_attempts=2``); one interrupted after it fails
and :func:`on_linked_articles_interrupted` stores what was done from the progress table.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.article_html import sanitize_html
from tiqora.itsm.models import TiqoraItsmBulkTarget
from tiqora.jobs.context import JobCancelled, JobContext
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

LINKED_ARTICLES_KIND = "core.itsm_linked_articles"
CHECKPOINT = "first_write"
SkipReason = Literal["no_rw", "not_linked", "no_customer_address", "vetoed", "error"]
DEFAULT_CLOSE_SUBJECT = "Geschlossen mit Workaround"


class LinkedArticlesPayload(BaseModel):
    source_ticket_id: int
    target_ticket_ids: list[int] = Field(min_length=1)  # de-duplicated, source excluded
    mode: Literal["note", "reply"]
    subject: str = Field(max_length=250)
    body_html: str
    visible_for_customer: bool = False  # note only
    set_state_name: str | None = None  # e.g. workaround_state (close with workaround)
    origin: Literal["linked_note", "major_incident_update", "problem_close_incidents"]
    major_update_id: int | None = None
    acting_user_id: int

    @field_validator("target_ticket_ids")
    @classmethod
    def _dedupe(cls, value: list[int], info: ValidationInfo) -> list[int]:
        seen: set[int] = set()
        out: list[int] = []
        source = info.data.get("source_ticket_id")
        for ticket_id in value:
            if ticket_id == source or ticket_id in seen:
                continue
            seen.add(ticket_id)
            out.append(ticket_id)
        if not out:
            raise ValueError("no target tickets")
        return out


class LinkedArticlesResult(BaseModel):
    done: list[int]
    skipped: list[dict[str, Any]]  # {"ticket_id", "reason", "detail"}


async def linked_ticket_ids(session: AsyncSession, ticket_id: int) -> set[int]:
    """Tickets with a valid ticket-to-ticket link to ``ticket_id`` (any type, any direction)."""
    rows = (
        await session.execute(
            text(
                "SELECT lr.source_key, lr.target_key FROM link_relation lr"
                " JOIN link_object lo ON lo.id = lr.source_object_id AND lo.name = 'Ticket'"
                " JOIN link_state ls ON ls.id = lr.state_id AND ls.name = 'Valid'"
                " WHERE lr.target_object_id = lr.source_object_id"
                " AND (lr.source_key = :k OR lr.target_key = :k)"
            ),
            {"k": str(ticket_id)},
        )
    ).all()
    out: set[int] = set()
    for source_key, target_key in rows:
        other = target_key if str(source_key) == str(ticket_id) else source_key
        try:
            out.add(int(other))
        except (TypeError, ValueError):
            continue
    out.discard(ticket_id)
    return out


async def enqueue_linked_articles(
    session: AsyncSession, payload: LinkedArticlesPayload
) -> TiqoraJob:
    """Queue the job (flushes, never commits). The body is sanitised on write (F12)."""
    safe = payload.model_copy(update={"body_html": sanitize_html(payload.body_html)})
    return await enqueue_job(
        session, kind=LINKED_ARTICLES_KIND, payload=safe, created_by=payload.acting_user_id
    )


async def _done_rows(session: AsyncSession, job_id: int) -> dict[int, TiqoraItsmBulkTarget]:
    rows = (
        (
            await session.execute(
                select(TiqoraItsmBulkTarget).where(TiqoraItsmBulkTarget.job_id == job_id)
            )
        )
        .scalars()
        .all()
    )
    return {int(r.ticket_id): r for r in rows}


async def result_from_table(session: AsyncSession, job_id: int) -> LinkedArticlesResult:
    rows = await _done_rows(session, job_id)
    ordered = sorted(rows.values(), key=lambda r: (r.created_at, r.ticket_id))
    return LinkedArticlesResult(
        done=[int(r.ticket_id) for r in ordered if r.status == "done"],
        skipped=[
            {"ticket_id": int(r.ticket_id), "reason": r.reason or "error", "detail": r.detail}
            for r in ordered
            if r.status != "done"
        ],
    )


def _record(
    session: AsyncSession,
    ctx: JobContext,
    ticket_id: int,
    status: str,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        TiqoraItsmBulkTarget(
            job_id=ctx.job_id,
            ticket_id=ticket_id,
            status=status,
            reason=reason,
            detail=detail[:500] if detail else None,
            created_at=utc_now(),
        )
    )


class _Skip(Exception):
    def __init__(self, reason: SkipReason, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


async def _customer_address(session: AsyncSession, ticket_id: int) -> str | None:
    row = (
        await session.execute(
            text(
                "SELECT cu.email FROM ticket t"
                " JOIN customer_user cu ON cu.login = t.customer_user_id"
                " WHERE t.id = :t"
            ),
            {"t": ticket_id},
        )
    ).first()
    value = str(row[0]).strip() if row and row[0] else ""
    return value or None


async def _state_id(session: AsyncSession, name: str) -> int | None:
    row = (
        await session.execute(
            text("SELECT id FROM ticket_state WHERE name = :n AND valid_id = 1"), {"n": name}
        )
    ).first()
    return None if row is None else int(row[0])


async def _write_one(
    session: AsyncSession,
    ctx: JobContext,
    payload: LinkedArticlesPayload,
    ticket_id: int,
    *,
    first: bool,
) -> None:
    """Checks + the writes of one target (raises ``_Skip``); the caller owns the commit."""
    from tiqora.domain.ticket_write_service import (
        ArticleIn,
        TicketAccessDenied,
        TicketWriteService,
    )
    from tiqora.plugins.sync_hooks import OperationVetoed
    from tiqora.znuny.sysconfig import SysConfig

    ticket = (
        await session.execute(text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": ticket_id})
    ).first()
    # unreadable and missing collapse to no_rw: the skip list must not leak ticket facts
    if ticket is None:
        raise _Skip("no_rw")
    queue_id = int(ticket[0])
    engine = PermissionEngine(session)
    user_id = payload.acting_user_id
    if not (
        await engine.check(user_id, queue_id, "rw")
        and await engine.check(user_id, queue_id, "note")
    ):
        raise _Skip("no_rw")
    if ticket_id not in await linked_ticket_ids(session, payload.source_ticket_id):
        raise _Skip("not_linked")

    to_address: str | None = None
    if payload.mode == "reply":
        to_address = await _customer_address(session, ticket_id)
        if to_address is None:
            raise _Skip("no_customer_address")
    state_id: int | None = None
    if payload.set_state_name:
        state_id = await _state_id(session, payload.set_state_name)
        if state_id is None:
            raise _Skip("error", "unknown_state")

    body = sanitize_html(payload.body_html)
    if payload.mode == "reply":
        article = ArticleIn(
            sender_type="agent",
            is_visible_for_customer=True,
            subject=payload.subject,
            body=body,
            content_type="text/html; charset=utf-8",
            to_address=to_address,
            channel="email",
        )
    else:
        article = ArticleIn(
            sender_type="agent",
            is_visible_for_customer=payload.visible_for_customer,
            subject=payload.subject,
            body=body,
            content_type="text/html; charset=utf-8",
            channel="note",
        )

    if first:
        # Point of no return; written in this transaction, so it appears with the article.
        await ctx.checkpoint(CHECKPOINT, session=session)
    writer = TicketWriteService(session, ctx.session_factory, SysConfig(session))
    try:
        if state_id is not None:
            # R16 / F1 order: a state veto must come before the article (a reply is sent)
            await writer.check_state_change_hooks(user_id, ticket_id, state_id)
        await writer.add_article(user_id, ticket_id, article)
        if state_id is not None:
            await writer.change_state(user_id, ticket_id, state_id, hooks_checked=True)
    except TicketAccessDenied as exc:
        raise _Skip("no_rw") from exc
    except OperationVetoed as exc:
        # R16: a plugin's sync hook refused this ticket; skip it, the job goes on
        raise _Skip("vetoed", str(exc)) from exc


async def run_linked_articles(ctx: JobContext, payload: LinkedArticlesPayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        known = set(await _done_rows(session, ctx.job_id))
    total = len(payload.target_ticket_ids)
    wrote_before = bool(known)  # a re-run after the checkpoint never needs it again
    first = not wrote_before
    cancelled = False
    for index, ticket_id in enumerate(payload.target_ticket_ids, start=1):
        if ticket_id in known:
            continue
        try:
            async with ctx.session_factory() as session:
                try:
                    async with session.begin_nested():
                        await _write_one(session, ctx, payload, ticket_id, first=first)
                    _record(session, ctx, ticket_id, "done")
                    first = False
                except _Skip as skip:
                    _record(session, ctx, ticket_id, "skipped", skip.reason, skip.detail)
                except JobCancelled:
                    raise
                except Exception as exc:
                    logger.warning(
                        "itsm_linked_article_failed",
                        job_id=ctx.job_id,
                        ticket_id=ticket_id,
                        error=type(exc).__name__,
                    )
                    _record(session, ctx, ticket_id, "error", "error", type(exc).__name__)
                await session.commit()
            await ctx.progress(index, total)
        except JobCancelled:
            cancelled = True
            break
    async with ctx.session_factory() as session:
        result = await result_from_table(session, ctx.job_id)
    out = result.model_dump()
    if cancelled:
        out["cancelled"] = True
    return out


async def on_linked_articles_interrupted(ctx: JobContext, payload: LinkedArticlesPayload) -> None:
    """Store what was done (from the progress table) in the failed job's result."""
    import json

    async with ctx.session_factory() as session:
        result = await result_from_table(session, ctx.job_id)
        data = result.model_dump()
        data["interrupted"] = True
        await session.execute(
            update(TiqoraJob).where(TiqoraJob.id == ctx.job_id).values(result=json.dumps(data))
        )
        await session.commit()


LINKED_ARTICLES_JOB = JobHandler(
    kind=LINKED_ARTICLES_KIND,
    payload_model=LinkedArticlesPayload,
    run=run_linked_articles,
    title_key="jobs.kinds.core.itsm_linked_articles",
    timeout_seconds=1800,
    lane="default",
    max_attempts=2,
    requeue_if_interrupted=True,
    on_interrupted=on_linked_articles_interrupted,
    result_private=True,
)
