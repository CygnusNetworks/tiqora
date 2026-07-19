"""Shared write-path helpers for the admin CRUD API.

Mirrors the invariant bookkeeping already used by
``domain/ticket_write_service.py`` / ``domain/queue_service.py``: every
Znuny row carries ``create_time``/``create_by``/``change_time``/``change_by``,
and "deletion" is a soft ``valid_id`` flip rather than a hard ``DELETE``
(Znuny never hard-deletes master-data rows referenced by tickets).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.cache_invalidation import invalidate_ticket_cache


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def invalidate_cache_for_queue(session: AsyncSession, queue_id: int) -> None:
    """Queue cache invalidation for every open ticket in *queue_id*.

    Config changes to a queue (escalation timers, salutation/signature,
    validity) are ticket-relevant for every ticket currently sitting in it.
    """
    result = await session.execute(
        text("SELECT id FROM ticket WHERE queue_id = :qid"), {"qid": queue_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)


async def invalidate_cache_for_state(session: AsyncSession, state_id: int) -> None:
    result = await session.execute(
        text("SELECT id FROM ticket WHERE ticket_state_id = :sid"), {"sid": state_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)


async def invalidate_cache_for_priority(session: AsyncSession, priority_id: int) -> None:
    result = await session.execute(
        text("SELECT id FROM ticket WHERE ticket_priority_id = :pid"), {"pid": priority_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)
