"""SCIM provisioning log: writes and failures only, field names only, purged after retention.

``record_event`` runs in its own session and transaction so a request that rolled back (or
failed authentication) is still logged; it never raises. ``record_event_in`` adds the same
row to the caller's transaction (admin token operations).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Literal

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraScimEvent
from tiqora.domain.settings_store import KEY_SCIM_EVENTS_PURGE_ENABLED, get_setting_bool
from tiqora.identity.types import utc_now

logger = structlog.get_logger(__name__)

ScimOperation = Literal[
    "create",
    "adopt",
    "revive",
    "replace",
    "patch",
    "deactivate",
    "reactivate",
    "delete",
    "conflict",
    "rejected",
    "auth_failed",
    "rate_limited",
    "token_create",
    "token_revoke",
    "group_create",
    "group_replace",
    "group_patch",
    "group_delete",
]

DETAIL_MAX: Final = 500
SCIM_ID_MAX: Final = 36  # CHAR(36); a client-supplied path id may be longer
LIST_LIMIT_MAX: Final = 200


def _build(
    *,
    provider_id: int | None,
    token_id: int | None,
    method: str,
    resource: str,
    operation: ScimOperation,
    status: int,
    scim_id: str | None = None,
    user_kind: str | None = None,
    user_id: int | None = None,
    detail: str | None = None,
    actor_user_id: int | None = None,
) -> TiqoraScimEvent:
    return TiqoraScimEvent(
        provider_id=provider_id,
        token_id=token_id,
        created=utc_now(),
        method=method,
        resource=resource,
        operation=operation,
        scim_id=scim_id[:SCIM_ID_MAX] if scim_id is not None else None,
        user_kind=user_kind,
        user_id=user_id,
        status=status,
        detail=detail[:DETAIL_MAX] if detail is not None else None,
        actor_user_id=actor_user_id,
    )


async def record_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_id: int | None,
    token_id: int | None,
    method: str,
    resource: str,
    operation: ScimOperation,
    status: int,
    scim_id: str | None = None,
    user_kind: str | None = None,
    user_id: int | None = None,
    detail: str | None = None,
    actor_user_id: int | None = None,
) -> None:
    """Write one event in its own transaction; a DB failure never reaches the caller.

    The row is built outside the ``try``: a wrong call (programming error) raises loudly.
    """
    row = _build(
        provider_id=provider_id,
        token_id=token_id,
        method=method,
        resource=resource,
        operation=operation,
        status=status,
        scim_id=scim_id,
        user_kind=user_kind,
        user_id=user_id,
        detail=detail,
        actor_user_id=actor_user_id,
    )
    try:
        async with session_factory() as session:
            session.add(row)
            await session.commit()
    except Exception:  # noqa: BLE001 — auditing must not break provisioning
        logger.warning(
            "scim_event_not_recorded",
            provider_id=provider_id,
            operation=operation,
            exc_info=True,
        )


async def record_event_in(
    session: AsyncSession,
    *,
    provider_id: int | None,
    token_id: int | None,
    method: str,
    resource: str,
    operation: ScimOperation,
    status: int,
    scim_id: str | None = None,
    user_kind: str | None = None,
    user_id: int | None = None,
    detail: str | None = None,
    actor_user_id: int | None = None,
) -> None:
    """Add the event to the caller's transaction (flush, no commit)."""
    session.add(
        _build(
            provider_id=provider_id,
            token_id=token_id,
            method=method,
            resource=resource,
            operation=operation,
            status=status,
            scim_id=scim_id,
            user_kind=user_kind,
            user_id=user_id,
            detail=detail,
            actor_user_id=actor_user_id,
        )
    )
    await session.flush()


async def list_events(
    session: AsyncSession,
    *,
    provider_id: int,
    before_id: int | None,
    limit: int,
    failures_only: bool,
) -> list[TiqoraScimEvent]:
    """Newest first; ``before_id`` pages; ``failures_only`` keeps ``status >= 400``."""
    stmt = select(TiqoraScimEvent).where(TiqoraScimEvent.provider_id == provider_id)
    if before_id is not None:
        stmt = stmt.where(TiqoraScimEvent.id < before_id)
    if failures_only:
        stmt = stmt.where(TiqoraScimEvent.status >= 400)
    stmt = stmt.order_by(TiqoraScimEvent.id.desc()).limit(max(1, min(limit, LIST_LIMIT_MAX)))
    return list((await session.scalars(stmt)).all())


async def purge_events(session: AsyncSession, *, older_than: datetime) -> int:
    result = await session.execute(
        delete(TiqoraScimEvent).where(TiqoraScimEvent.created < older_than)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def run_scim_events_purge_tick(settings: Settings | None = None) -> dict[str, int]:
    """Daily retention purge; ``{"skipped": 1}`` when switched off (default ON)."""
    settings = settings or get_settings()
    async with get_session_factory()() as session:
        if not await get_setting_bool(session, KEY_SCIM_EVENTS_PURGE_ENABLED, True):
            return {"skipped": 1}
        cutoff = utc_now() - timedelta(days=settings.scim_event_retention_days)
        deleted = await purge_events(session, older_than=cutoff)
        await session.commit()
    logger.info("scim_events_purge_tick", deleted=deleted)
    return {"deleted": deleted}


__all__ = [
    "ScimOperation",
    "list_events",
    "purge_events",
    "record_event",
    "record_event_in",
    "run_scim_events_purge_tick",
]
