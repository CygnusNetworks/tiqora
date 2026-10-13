"""The one place that ends sessions of agents and customers (wave 4, SCIM).

Admin routes, the SCIM write layer and the deprovisioning paths all call these two
functions so a disabled account loses every live session the same way: Znuny
``sessions`` rows first (own try + rollback), then Redis. Neither half may fail the
account change that already committed, so errors are logged and swallowed.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.domain.customer_auth import revoke_customer_redis_sessions
from tiqora.znuny.sessions import delete_agent_sessions, delete_customer_sessions

logger = structlog.get_logger(__name__)


async def end_agent_sessions(
    session: AsyncSession, redis: Redis, settings: Settings, user_id: int, *, reason: str
) -> int:
    """End every session of agent *user_id*; return the number of Redis tokens removed.

    Commits the Znuny row deletion. A rollback after a failure expires loaded ORM
    attributes - callers that keep using the user object refresh it afterwards.
    """
    try:
        await delete_agent_sessions(session, user_id)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — the account change already committed
        await session.rollback()
        logger.warning("sessions.znuny_revoke_failed", user_id=user_id, error=str(exc))
    try:
        revoked = await SessionStore(redis, settings).revoke_user_sessions(user_id)
    except Exception as exc:  # noqa: BLE001 — the account change already committed
        logger.warning("sessions.redis_revoke_failed", user_id=user_id, error=str(exc))
        return 0
    logger.info("sessions.ended", kind="agent", user_id=user_id, revoked=revoked, reason=reason)
    return revoked


async def end_customer_sessions(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    logins: Iterable[str],
    *,
    reason: str,
) -> int:
    """End every session of the customers *logins*: Znuny rows, portal sessions and GI tokens."""
    wanted = list(logins)
    if not wanted:
        return 0
    try:
        for login in wanted:
            await delete_customer_sessions(session, login)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — the account change already committed
        await session.rollback()
        logger.warning("sessions.znuny_revoke_failed", customers=len(wanted), error=str(exc))
    try:
        revoked = await revoke_customer_redis_sessions(redis, settings, wanted)
    except Exception as exc:  # noqa: BLE001 — the account change already committed
        logger.warning("sessions.redis_revoke_failed", customers=len(wanted), error=str(exc))
        return 0
    logger.info(
        "sessions.ended", kind="customer", customers=len(wanted), revoked=revoked, reason=reason
    )
    return revoked
