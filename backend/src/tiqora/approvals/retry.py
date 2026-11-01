"""A vote's transaction, run again when the database picked it as a deadlock victim.

Everything a vote causes (vote row, audit trail, history lines, mail and hook jobs) is written in
its transaction, so a rollback undoes all of it and a new attempt cannot send or run anything
twice. What happens outside the database (the push to the inbox) comes after the commit, in the
caller.

Not ``dashboard.store._retry_races``: that one also retries lock wait timeouts, which at 50 s each
would outlast the proxy's 90 s, and integrity errors. Only a deadlock is worth a quick new attempt.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Final

import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import ApprovalError
from tiqora.reporting.state import is_deadlock

logger = structlog.get_logger(__name__)

_ATTEMPTS: Final = 4


async def commit_with_retry[T](session: AsyncSession, attempt: Callable[[], Awaitable[T]]) -> T:
    """Runs *attempt* and commits; after a deadlock rolls back, waits and runs all of it again.

    *attempt* must start a fresh transaction's work, with the request lock as its first read. Out
    of attempts: 503 ``approval_busy``, retryable. Every other error passes through unchanged.
    """
    for n in range(1, _ATTEMPTS + 1):
        try:
            result = await attempt()
            await session.commit()
        except DBAPIError as exc:
            await session.rollback()
            if not is_deadlock(exc):
                raise
            logger.info("approval_vote_deadlock", attempt=n)
            if n == _ATTEMPTS:
                raise ApprovalError("approval_busy", 503) from exc
            await asyncio.sleep(random.uniform(0.02, 0.1) * n)  # jitter: no lock-step retry
        else:
            return result
    raise AssertionError("unreachable")
