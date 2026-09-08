"""Feature-flagged port of Znuny's Maint::Ticket::UnlockTimeout."""

from __future__ import annotations

import time

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_UNLOCK_TIMEOUT_ENABLED, get_setting_bool
from tiqora.domain.ticket_write_service import unlock_ticket
from tiqora.znuny.escalation import (
    VacationDays,
    VacationDaysOneTime,
    WorkingHours,
    _calendar_config,
    destination_time_epoch,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)
UNLOCK_TIMEOUT_TICKETS = Counter(
    "tiqora_unlock_timeout_tickets_total", "Tickets unlocked after their working-time timeout"
)
UNLOCK_TIMEOUT_ERRORS = Counter(
    "tiqora_unlock_timeout_errors_total", "Unlock-timeout ticket processing errors"
)


def working_timeout_due(
    start_epoch: int,
    timeout_minutes: int,
    now_epoch: int,
    working_hours: WorkingHours,
    vacation_days: VacationDays,
    vacation_days_once: VacationDaysOneTime,
    tz_name: str,
) -> bool:
    return (
        destination_time_epoch(
            start_epoch,
            timeout_minutes,
            working_hours,
            vacation_days,
            vacation_days_once,
            tz_name,
        )
        <= now_epoch
    )


async def _calendar_name(session: AsyncSession, sla_id: int | None, queue_id: int) -> str | None:
    if sla_id:
        row = (
            await session.execute(
                text("SELECT calendar_name FROM sla WHERE id = :id"), {"id": sla_id}
            )
        ).first()
        if row is not None and row[0]:
            return str(row[0])
    row = (
        await session.execute(
            text("SELECT calendar_name FROM queue WHERE id = :id"), {"id": queue_id}
        )
    ).first()
    return str(row[0]) if row is not None and row[0] else None


async def run_unlock_timeout_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    _ = settings or get_settings()
    factory = session_factory or get_session_factory()
    async with factory() as session:
        if not await get_setting_bool(session, KEY_UNLOCK_TIMEOUT_ENABLED, False):
            return {"enabled": 0}
        sysconfig = SysConfig(session)
        state_types = await sysconfig.get("Ticket::UnlockStateType", ["new", "open"])
        if not isinstance(state_types, list) or not state_types:
            state_types = ["new", "open"]
        viewable_locks = await sysconfig.get("Ticket::ViewableLocks", ["'unlock'", "'tmp_lock'"])
        if not isinstance(viewable_locks, list) or not viewable_locks:
            viewable_locks = ["'unlock'", "'tmp_lock'"]
        viewable_locks = [str(v).strip("'") for v in viewable_locks]
        state_params = {f"state_{i}": str(v) for i, v in enumerate(state_types)}
        lock_params = {f"lock_{i}": str(v) for i, v in enumerate(viewable_locks)}
        state_in = ", ".join(f":{key}" for key in state_params)
        lock_in = ", ".join(f":{key}" for key in lock_params)
        eligibility = (
            "q.unlock_timeout <> 0 AND st.name IN (" + state_in + ")"
            " AND lt.name NOT IN (" + lock_in + ")"
        )
        params = {**state_params, **lock_params}
        rows = (
            await session.execute(
                text(
                    "SELECT t.id FROM ticket t JOIN queue q ON q.id = t.queue_id"
                    " JOIN ticket_state s ON s.id = t.ticket_state_id"
                    " JOIN ticket_state_type st ON st.id = s.type_id"
                    " JOIN ticket_lock_type lt ON lt.id = t.ticket_lock_id"
                    " WHERE " + eligibility
                ),
                params,
            )
        ).fetchall()
    totals = {"checked": len(rows), "unlocked": 0, "errors": 0}
    now_epoch = int(time.time())
    for (ticket_id,) in rows:
        try:
            async with factory() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "SELECT t.timeout, q.unlock_timeout, t.sla_id, t.queue_id"
                            " FROM ticket t JOIN queue q ON q.id = t.queue_id"
                            " JOIN ticket_state s ON s.id = t.ticket_state_id"
                            " JOIN ticket_state_type st ON st.id = s.type_id"
                            " JOIN ticket_lock_type lt ON lt.id = t.ticket_lock_id"
                            " WHERE t.id = :id AND " + eligibility + " FOR UPDATE"
                        ),
                        {"id": int(ticket_id), **params},
                    )
                ).first()
                if row is None:
                    continue
                timeout_epoch, minutes, sla_id, queue_id = row
                calendar = await _calendar_name(
                    session, int(sla_id) if sla_id else None, int(queue_id)
                )
                sysconfig = SysConfig(session)
                hours, vacations, vacations_once, tz_name = await _calendar_config(
                    sysconfig, calendar
                )
                if not working_timeout_due(
                    int(timeout_epoch or 0),
                    int(minutes),
                    now_epoch,
                    hours,
                    vacations,
                    vacations_once,
                    tz_name,
                ):
                    continue
                user_id = await sysconfig.postmaster_user_id()
                await unlock_ticket(
                    session, ticket_id=int(ticket_id), user_id=user_id, sysconfig=sysconfig
                )
            totals["unlocked"] += 1
            UNLOCK_TIMEOUT_TICKETS.inc()
        except Exception:
            logger.exception("unlock_timeout_ticket_failed", ticket_id=ticket_id)
            totals["errors"] += 1
            UNLOCK_TIMEOUT_ERRORS.inc()
    logger.info("unlock_timeout_tick", **totals)
    return totals


__all__ = ["run_unlock_timeout_tick", "working_timeout_due"]
