"""Persist the AI→human handoff flag on ``tiqora_ai_ticket_state``.

Separate from Znuny SLA ``ticket.escalation_*``: those columns are rebuilt
from queue minutes after every article/state change, and queues with
``update_time = 0`` (common in production) would wipe a fake timestamp. This flag is
the list-UI marker for "the model asked a human to take over".
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiTicketState

logger = structlog.get_logger(__name__)


async def mark_ai_escalated(session: AsyncSession, ticket_id: int) -> None:
    """Set ``ai_escalated_at`` if it is not already set. Does not commit.

    Creates the per-ticket state row when missing, but unlike
    :func:`tiqora.ai.context.get_or_create_state` this never commits
    mid-transaction — the caller owns the session.

    Called from inside the AI agent run's own transaction. Runs in a
    SAVEPOINT (not a plain try/except) so that a missing
    ``tiqora_ai_ticket_state`` table (Znuny-only fixtures/deployments — the
    table is created lazily by Tiqora's own migrations) only undoes this
    flag write, never the caller's outer transaction.
    """
    try:
        async with session.begin_nested():
            state = await session.get(TiqoraAiTicketState, ticket_id)
            if state is None:
                state = TiqoraAiTicketState(ticket_id=ticket_id)
                session.add(state)
                await session.flush()
            if state.ai_escalated_at is None:
                state.ai_escalated_at = datetime.now(UTC).replace(tzinfo=None)
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state write failed (table missing?) — skipping AI handoff mark",
            exc_info=True,
        )


async def clear_ai_escalated(session: AsyncSession, ticket_id: int) -> None:
    """Clear the handoff flag. No-op when the row is missing. Does not commit.

    Called from the core ticket-write path (agent reply / state change), so
    — same reasoning as :func:`mark_ai_escalated` — a missing table is
    isolated to a SAVEPOINT rather than risking the caller's transaction.
    """
    try:
        async with session.begin_nested():
            await session.execute(
                update(TiqoraAiTicketState)
                .where(TiqoraAiTicketState.ticket_id == ticket_id)
                .values(ai_escalated_at=None)
            )
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state write failed (table missing?) — skipping AI handoff clear",
            exc_info=True,
        )


async def _pause_row_exists(session: AsyncSession, ticket_id: int) -> bool:
    return (
        await session.execute(
            select(TiqoraAiTicketState.ticket_id).where(TiqoraAiTicketState.ticket_id == ticket_id)
        )
    ).first() is not None


async def _apply_pause(session: AsyncSession, ticket_id: int, user_id: int) -> int:
    """Set the pause on an existing, not-yet-paused row; returns rows changed."""
    result = await session.execute(
        update(TiqoraAiTicketState)
        .where(
            TiqoraAiTicketState.ticket_id == ticket_id,
            TiqoraAiTicketState.ai_paused_at.is_(None),
        )
        .values(ai_paused_at=datetime.now(UTC).replace(tzinfo=None), ai_paused_by=user_id)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def set_ai_paused(session: AsyncSession, ticket_id: int, user_id: int) -> None:
    """Pause all automatic AI actions on a ticket. Does not commit.

    User-initiated, so errors propagate (a swallowed failure would report a
    pause that never happened). Race-safe against the auto worker creating the
    state row concurrently: update first, insert only when no row exists, and
    if that insert loses the race, update again. An already paused ticket keeps
    its original ``ai_paused_at``/``ai_paused_by``. Unlike the escalation flag
    this is never cleared by an agent reply or a state change, only by
    :func:`clear_ai_paused`.
    """
    if await _apply_pause(session, ticket_id, user_id):
        return
    if await _pause_row_exists(session, ticket_id):
        return  # already paused
    try:
        async with session.begin_nested():
            session.add(
                TiqoraAiTicketState(
                    ticket_id=ticket_id,
                    ai_paused_at=datetime.now(UTC).replace(tzinfo=None),
                    ai_paused_by=user_id,
                )
            )
            await session.flush()
    except IntegrityError:
        # The row appeared between our check and the insert.
        await _apply_pause(session, ticket_id, user_id)


async def clear_ai_paused(session: AsyncSession, ticket_id: int) -> None:
    """Lift the per-ticket AI pause. No-op when the row is missing. Does not
    commit. User-initiated, so errors propagate."""
    await session.execute(
        update(TiqoraAiTicketState)
        .where(TiqoraAiTicketState.ticket_id == ticket_id)
        .values(ai_paused_at=None, ai_paused_by=None)
        .execution_options(synchronize_session=False)
    )


async def ai_escalated_ticket_ids(session: AsyncSession, ticket_ids: list[int]) -> set[int]:
    """Ids (of ``ticket_ids``) that currently carry the handoff flag.

    The ``tiqora_ai_ticket_state`` table may not exist in Znuny-only
    fixtures — treat a missing-table error as "no flags" rather than
    failing the ticket list.
    """
    if not ticket_ids:
        return set()
    try:
        rows = await session.execute(
            select(TiqoraAiTicketState.ticket_id).where(
                TiqoraAiTicketState.ticket_id.in_(ticket_ids),
                TiqoraAiTicketState.ai_escalated_at.is_not(None),
            )
        )
        return set(rows.scalars().all())
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state query failed (table missing?) — treating as no AI handoffs",
            exc_info=True,
        )
        await session.rollback()
        return set()


__all__ = [
    "ai_escalated_ticket_ids",
    "clear_ai_escalated",
    "clear_ai_paused",
    "mark_ai_escalated",
    "set_ai_paused",
]
