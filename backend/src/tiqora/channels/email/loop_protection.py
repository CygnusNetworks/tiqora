"""Loop protection — port of ``Kernel::System::PostMaster::LoopProtection::DB``.

Tracks per-day auto-response counts per recipient in ``ticket_loop_protection``
(``sent_to``, ``sent_date`` — composite primary key, so a second send to the
same address on the same day is a silent upsert-no-op in Znuny; we replicate
via INSERT ... ON CONFLICT DO NOTHING equivalent / catch IntegrityError).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.ticket import TicketLoopProtection


def loop_protection_date(now: datetime | None = None) -> str:
    """Znuny ``LoopProtectionCommon``: ``DateTimeObject->Format('%Y-%m-%d')``."""
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


async def check(
    session: AsyncSession,
    *,
    to: str,
    max_emails: int,
    max_emails_per_address: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True if it is still safe to send an auto-response to *to*."""
    if not to:
        return False
    limit = max_emails
    if max_emails_per_address:
        override = max_emails_per_address.get(to.lower())
        if override is not None:
            limit = int(override)
    if not limit:
        return True

    date_str = loop_protection_date(now)
    count = (
        await session.execute(
            select(func.count())
            .select_from(TicketLoopProtection)
            .where(TicketLoopProtection.sent_to == to, TicketLoopProtection.sent_date == date_str)
        )
    ).scalar_one()
    return int(count) < limit


async def record(session: AsyncSession, *, to: str, now: datetime | None = None) -> None:
    """Record one auto-response send and prune rows from earlier days.

    Znuny's ``ticket_loop_protection`` table (``schema.xml``) has **no**
    primary key or unique constraint on ``(sent_to, sent_date)`` — every send
    is a plain ``INSERT``, so the row count for a given day *is* the
    send-count ``Check()`` compares against ``PostmasterMaxEmails``. Do not
    "optimize" this into an upsert.
    """
    if not to:
        return
    date_str = loop_protection_date(now)
    session.add(TicketLoopProtection(sent_to=to, sent_date=date_str))
    await session.flush()
    await session.execute(
        text("DELETE FROM ticket_loop_protection WHERE sent_date != :ds"), {"ds": date_str}
    )
