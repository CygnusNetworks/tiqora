"""Cache invalidation writer for ``tiqora_cache_invalidation``.

Direct DB writes by Tiqora are invisible to Znuny's in-process caches until
they expire. The Perl OPM addon TiqoraSync polls this table from a daemon
cron task and either clears per-ticket Ticket cache entries or runs
``Cache->CleanUp(Type => $CacheType)`` for master-data signals.

Write services call :func:`invalidate_ticket_cache` / :func:`invalidate_cache_type`
inside the same transaction as the write.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def invalidate_ticket_cache(session: AsyncSession, ticket_id: int) -> None:
    """Queue a ticket-level cache-invalidation signal for *ticket_id*."""
    await session.execute(
        text(
            "INSERT INTO tiqora_cache_invalidation (ticket_id, cache_type, created)"
            " VALUES (:tid, NULL, current_timestamp)"
        ),
        {"tid": ticket_id},
    )


async def invalidate_cache_type(session: AsyncSession, cache_type: str) -> None:
    """Queue a Znuny CacheType cleanup signal (``ticket_id`` NULL).

    *cache_type* must be a real Znuny ``CacheType`` string as used by
    ``Kernel::System::*`` modules (e.g. ``'Queue'``, ``'DynamicField'``).
    Empty / whitespace-only values are ignored.
    """
    cleaned = (cache_type or "").strip()
    if not cleaned:
        return
    await session.execute(
        text(
            "INSERT INTO tiqora_cache_invalidation (ticket_id, cache_type, created)"
            " VALUES (NULL, :ctype, current_timestamp)"
        ),
        {"ctype": cleaned},
    )


__all__ = ["invalidate_ticket_cache", "invalidate_cache_type"]
