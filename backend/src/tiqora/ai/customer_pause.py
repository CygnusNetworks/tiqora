"""Per-customer stop of the automatic AI actions.

The per-ticket pause (``ai_paused_at``) only covers the ticket an agent is
looking at. Some customers should get no automation at all (a dispute, a VIP,
a person who answers every AI mail with an angry one): a row in
``tiqora_ai_customer_pause`` stops auto-reply, triage and auto-summary for
every ticket of that customer user or customer company, either until lifted
or for N days. Manual Assist is unaffected, like with the ticket pause.

The workers ask :func:`active_pause_for_ticket` next to their ``ai_paused``
checks. A pause that has run out is simply ignored (no sweeper needed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import (
    CUSTOMER_PAUSE_SCOPE_COMPANY,
    CUSTOMER_PAUSE_SCOPE_USER,
    TiqoraAiCustomerPause,
)

#: Skip reason recorded by the workers (``auto_skip_reason`` is a String(40)).
SKIP_REASON = "customer_ai_paused"
VALID_SCOPES = frozenset({CUSTOMER_PAUSE_SCOPE_USER, CUSTOMER_PAUSE_SCOPE_COMPANY})
MAX_PAUSE_DAYS = 3650


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _active_clause(now: datetime):  # type: ignore[no-untyped-def]
    return or_(
        TiqoraAiCustomerPause.paused_until.is_(None),
        TiqoraAiCustomerPause.paused_until > now,
    )


async def active_pause_for_customer(
    session: AsyncSession, *, customer_user_id: str | None, customer_id: str | None
) -> TiqoraAiCustomerPause | None:
    """The pause that applies to a customer user / company pair, if any.

    A pause on the customer user wins over one on the company."""
    keys = []
    if customer_user_id:
        keys.append(
            and_(
                TiqoraAiCustomerPause.scope == CUSTOMER_PAUSE_SCOPE_USER,
                TiqoraAiCustomerPause.customer_key == customer_user_id,
            )
        )
    if customer_id:
        keys.append(
            and_(
                TiqoraAiCustomerPause.scope == CUSTOMER_PAUSE_SCOPE_COMPANY,
                TiqoraAiCustomerPause.customer_key == customer_id,
            )
        )
    if not keys:
        return None
    rows = (
        (
            await session.execute(
                select(TiqoraAiCustomerPause).where(or_(*keys), _active_clause(_utcnow()))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.scope == CUSTOMER_PAUSE_SCOPE_USER:
            return row
    return rows[0] if rows else None


async def active_pause_for_ticket(
    session: AsyncSession, ticket_id: int
) -> TiqoraAiCustomerPause | None:
    row = (
        await session.execute(
            text("SELECT customer_id, customer_user_id FROM ticket WHERE id = :tid"),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return None
    return await active_pause_for_customer(
        session,
        customer_user_id=str(row[1]) if row[1] else None,
        customer_id=str(row[0]) if row[0] else None,
    )


async def get_pause(
    session: AsyncSession, scope: str, customer_key: str
) -> TiqoraAiCustomerPause | None:
    """The active pause for exactly this customer user / company (not the
    combined lookup used by the workers)."""
    return (
        await session.execute(
            select(TiqoraAiCustomerPause).where(
                TiqoraAiCustomerPause.scope == scope,
                TiqoraAiCustomerPause.customer_key == customer_key,
                _active_clause(_utcnow()),
            )
        )
    ).scalar_one_or_none()


async def set_pause(
    session: AsyncSession,
    *,
    scope: str,
    customer_key: str,
    days: int | None,
    reason: str | None,
    user_id: int,
) -> TiqoraAiCustomerPause:
    """Create or replace the pause. ``days=None`` = until an agent lifts it.
    The caller commits."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope!r}")
    if days is not None and not 1 <= days <= MAX_PAUSE_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_PAUSE_DAYS}")
    now = _utcnow()
    until = now + timedelta(days=days) if days is not None else None
    row = (
        await session.execute(
            select(TiqoraAiCustomerPause).where(
                TiqoraAiCustomerPause.scope == scope,
                TiqoraAiCustomerPause.customer_key == customer_key,
            )
        )
    ).scalar_one_or_none()
    cleaned = (reason or "").strip()[:255] or None
    if row is None:
        row = TiqoraAiCustomerPause(scope=scope, customer_key=customer_key, created_at=now)
        session.add(row)
    row.paused_until = until
    row.reason = cleaned
    row.created_by = user_id
    row.created_at = now
    await session.flush()
    return row


async def clear_pause(session: AsyncSession, *, scope: str, customer_key: str) -> bool:
    """Delete the pause (also an expired one). True if a row existed. The
    caller commits."""
    result = await session.execute(
        delete(TiqoraAiCustomerPause).where(
            TiqoraAiCustomerPause.scope == scope,
            TiqoraAiCustomerPause.customer_key == customer_key,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


__all__ = [
    "MAX_PAUSE_DAYS",
    "SKIP_REASON",
    "VALID_SCOPES",
    "active_pause_for_customer",
    "active_pause_for_ticket",
    "clear_pause",
    "get_pause",
    "set_pause",
]
