"""Cache invalidation writer for ``tiqora_cache_invalidation``.

Direct DB writes by Tiqora are invisible to Znuny's in-process caches until
they expire. The Perl OPM addon TiqoraSync (Phase 3) polls this table from a
daemon cron task and clears the affected ticket caches. Future write services
call :func:`invalidate_ticket_cache` inside the same transaction as the write.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def invalidate_ticket_cache(session: AsyncSession, ticket_id: int) -> None:
    """Queue a cache-invalidation signal for *ticket_id*."""
    await session.execute(
        text(
            "INSERT INTO tiqora_cache_invalidation (ticket_id, created)"
            " VALUES (:tid, current_timestamp)"
        ),
        {"tid": ticket_id},
    )


__all__ = ["invalidate_ticket_cache"]
