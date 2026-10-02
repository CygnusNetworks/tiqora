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

from tiqora.ai.models import TiqoraAiTicketState, TiqoraAiTriage

logger = structlog.get_logger(__name__)


async def mark_ai_escalated(
    session: AsyncSession, ticket_id: int, *, reason: str | None = None
) -> None:
    """Set ``ai_escalated_at`` (and ``reason``) if it is not already set, and
    end any autopilot release: a handoff hands the ticket to the team, so N
    remaining automatic replies must not resume after it. Does not commit.

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
                state.ai_escalated_reason = reason
            _clear_grant_fields(state)
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state write failed (table missing?) — skipping AI handoff mark",
            exc_info=True,
        )


def _clear_grant_fields(state: TiqoraAiTicketState) -> None:
    state.ai_grant_remaining = None
    state.ai_grant_total = None
    state.ai_grant_by = None
    state.ai_grant_at = None


_NO_GRANT = {
    "ai_grant_remaining": None,
    "ai_grant_total": None,
    "ai_grant_by": None,
    "ai_grant_at": None,
}


async def clear_ai_grant(session: AsyncSession, ticket_id: int) -> None:
    """End an autopilot release (stop, ticket closed). No-op without a row.
    Does not commit. Also called from the core state-change path, so — like
    :func:`clear_ai_escalated` — a missing table only undoes this write."""
    try:
        async with session.begin_nested():
            await session.execute(
                update(TiqoraAiTicketState)
                .where(TiqoraAiTicketState.ticket_id == ticket_id)
                .values(**_NO_GRANT)
                .execution_options(synchronize_session=False)
            )
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state write failed (table missing?) — skipping grant clear",
            exc_info=True,
        )


async def set_ai_grant(session: AsyncSession, ticket_id: int, user_id: int, runs: int) -> None:
    """Grant ``runs`` automatic replies (replacing any earlier release).
    Creates the state row when missing, race-safe like :func:`set_ai_paused`.
    Does not commit."""
    values = {
        "ai_grant_remaining": runs,
        "ai_grant_total": runs,
        "ai_grant_by": user_id,
        "ai_grant_at": datetime.now(UTC).replace(tzinfo=None),
    }
    stmt = (
        update(TiqoraAiTicketState)
        .where(TiqoraAiTicketState.ticket_id == ticket_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if (await session.execute(stmt)).rowcount:  # type: ignore[attr-defined]
        return
    try:
        async with session.begin_nested():
            session.add(TiqoraAiTicketState(ticket_id=ticket_id, **values))
            await session.flush()
    except IntegrityError:
        # The row appeared between our update and the insert.
        await session.execute(stmt)


async def consume_ai_grant(session: AsyncSession, ticket_id: int) -> None:
    """Use one reply of a running release (never below 0). Does not commit."""
    await session.execute(
        update(TiqoraAiTicketState)
        .where(
            TiqoraAiTicketState.ticket_id == ticket_id,
            TiqoraAiTicketState.ai_grant_remaining > 0,
        )
        .values(ai_grant_remaining=TiqoraAiTicketState.ai_grant_remaining - 1)
        .execution_options(synchronize_session=False)
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
                .values(ai_escalated_at=None, ai_escalated_reason=None)
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


async def set_ai_paused(session: AsyncSession, ticket_id: int, user_id: int) -> bool:
    """Pause all automatic AI actions on a ticket. Does not commit.

    Returns True only when THIS call changed the state (so two concurrent
    requests write one audit note, not two). User-initiated, so errors
    propagate (a swallowed failure would report a pause that never happened).
    Race-safe against the auto worker creating the state row concurrently:
    update first, insert only when no row exists, and if that insert loses the
    race, update again. An already paused ticket keeps its original
    ``ai_paused_at``/``ai_paused_by``. Unlike the escalation flag this is never
    cleared by an agent reply or a state change, only by :func:`clear_ai_paused`.

    Also drops the ticket's parked triage reply (``reply_deferred_article_id``):
    it is an article that arrived before the pause and must not be answered
    after a later unpause (see ``auto_worker._replay_deferred_replies``).
    """
    changed = await _apply_pause(session, ticket_id, user_id) > 0
    if not changed and not await _pause_row_exists(session, ticket_id):
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
            changed = True
        except IntegrityError:
            # The row appeared between our check and the insert.
            changed = await _apply_pause(session, ticket_id, user_id) > 0
    if changed:
        await session.execute(
            update(TiqoraAiTriage)
            .where(TiqoraAiTriage.ticket_id == ticket_id)
            .values(reply_deferred_article_id=None)
            .execution_options(synchronize_session=False)
        )
    return changed


async def clear_ai_paused(session: AsyncSession, ticket_id: int) -> bool:
    """Lift the per-ticket AI pause. Returns True only when this call lifted
    it (False: row missing or not paused). Does not commit. User-initiated,
    so errors propagate."""
    result = await session.execute(
        update(TiqoraAiTicketState)
        .where(
            TiqoraAiTicketState.ticket_id == ticket_id,
            TiqoraAiTicketState.ai_paused_at.is_not(None),
        )
        .values(ai_paused_at=None, ai_paused_by=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0) > 0  # type: ignore[attr-defined]


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


async def ai_paused_ticket_ids(session: AsyncSession, ticket_ids: list[int]) -> set[int]:
    """Ids (of ``ticket_ids``) whose AI autopilot an agent stopped — the list
    shows them, so a forgotten stop does not go unnoticed."""
    if not ticket_ids:
        return set()
    try:
        rows = await session.execute(
            select(TiqoraAiTicketState.ticket_id).where(
                TiqoraAiTicketState.ticket_id.in_(ticket_ids),
                TiqoraAiTicketState.ai_paused_at.is_not(None),
            )
        )
        return set(rows.scalars().all())
    except DBAPIError:
        logger.debug(
            "tiqora_ai_ticket_state query failed (table missing?) — treating as no pauses",
            exc_info=True,
        )
        await session.rollback()
        return set()


__all__ = [
    "ai_escalated_ticket_ids",
    "ai_paused_ticket_ids",
    "clear_ai_escalated",
    "clear_ai_grant",
    "clear_ai_paused",
    "consume_ai_grant",
    "mark_ai_escalated",
    "set_ai_grant",
    "set_ai_paused",
]
