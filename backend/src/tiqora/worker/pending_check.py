"""Feature-flagged port of Znuny's ``Maint::Ticket::PendingCheck``.

Two passes per tick, mirroring the upstream command:

1. *Pending auto*: tickets whose state type is listed in
   ``Ticket::PendingAutoStateType`` and whose pending time has passed move to
   the state configured in ``Ticket::StateAfterPending`` (keyed by state
   *name*); if the target state's type is ``closed`` the ticket is unlocked,
   exactly as upstream's ``LockSet(Notification => 0)``.
2. *Pending reminder*: tickets in a ``pending reminder`` state whose pending
   time has passed emit a ``NotificationPendingReminder`` outbox event, but
   only when the preceding ten minutes contained working time for the ticket's
   calendar (upstream's ``Delta(ForWorkingTime => 1)`` guard).

Documented divergence: upstream re-fires the reminder on *every* run for as
long as the ticket stays pending, so its cadence is whatever the daemon's
schedule happens to be. Tiqora instead de-duplicates per pending deadline via
``daemon.pending_check.reminder_interval_seconds`` (default two hours), which
keeps the reminder cadence independent of the tick interval.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_PENDING_CHECK_ENABLED,
    KEY_PENDING_CHECK_REMINDER_INTERVAL_SECONDS,
    get_setting_bool,
    get_setting_int,
)
from tiqora.domain.ticket_write_service import change_state, unlock_ticket
from tiqora.znuny.escalation import (
    VacationDays,
    VacationDaysOneTime,
    WorkingHours,
    _calendar_config,
    _tzinfo,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)
PENDING_TRANSITIONS = Counter("tiqora_pending_transitions_total", "Due pending-auto transitions")
PENDING_REMINDERS = Counter("tiqora_pending_reminders_total", "Pending reminder events emitted")
PENDING_ERRORS = Counter("tiqora_pending_check_errors_total", "Pending-check ticket errors")
_DEFAULT_REMINDER_INTERVAL_SECONDS = 7200
# Upstream's reminder gate window (Maint::Ticket::PendingCheck subtracts 10 min).
_REMINDER_LOOKBACK_SECONDS = 600


def reminder_due(*, last_sent_epoch: int | None, now_epoch: int, interval_seconds: int) -> bool:
    return last_sent_epoch is None or now_epoch - last_sent_epoch >= interval_seconds


def working_seconds_between(
    start_epoch: int,
    stop_epoch: int,
    hours: WorkingHours,
    vacations: VacationDays,
    vacations_once: VacationDaysOneTime,
    tz_name: str,
) -> int:
    """Working seconds in ``[start_epoch, stop_epoch)`` for one calendar.

    Port of ``DateTime::Delta(ForWorkingTime => 1)`` for the short window the
    reminder gate needs. Znuny's working hours have hour granularity, so the
    window is walked in whole minutes.
    """
    if not any(hours.values()):
        return 0
    tz = _tzinfo(tz_name)
    seconds = 0
    cursor = start_epoch - (start_epoch % 60)
    while cursor < stop_epoch:
        dt = datetime.fromtimestamp(cursor, tz=tz)
        day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[dt.weekday()]
        is_vacation = dt.day in vacations.get(dt.month, {}) or dt.day in vacations_once.get(
            dt.year, {}
        ).get(dt.month, {})
        if not is_vacation and dt.hour in hours.get(day, []):
            seconds += 60
        cursor += 60
    return seconds


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


async def _last_matching_reminder_epoch(
    session: AsyncSession, ticket_id: int, pending_epoch: int, state_name: str
) -> int | None:
    """Epoch of the last reminder for this exact pending deadline, if any.

    The send time is read back from the payload's ``sent_epoch`` rather than
    from the row's ``created`` column: ``created`` is a naive DB timestamp
    whose time zone depends on the database session, and interpreting it in
    the wrong zone would shift the cadence by hours.
    """
    rows = (
        await session.execute(
            text(
                "SELECT payload FROM tiqora_event_outbox WHERE ticket_id = :id"
                " AND event_type = 'NotificationPendingReminder' ORDER BY id DESC LIMIT 24"
            ),
            {"id": ticket_id},
        )
    ).fetchall()
    for (payload,) in rows:
        try:
            data = json.loads(payload or "{}")
            if (
                int(data.get("pending_epoch", -1)) == pending_epoch
                and data.get("state") == state_name
            ):
                return int(data["sent_epoch"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


async def _emit_reminder(
    session: AsyncSession,
    ticket_id: int,
    tn: str,
    pending_epoch: int,
    state_name: str,
    sent_epoch: int,
) -> None:
    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox (event_type, ticket_id, payload, created, processed)"
            " VALUES ('NotificationPendingReminder', :id, :payload, current_timestamp, 0)"
        ),
        {
            "id": ticket_id,
            "payload": json.dumps(
                {
                    "ticket_number": tn,
                    "pending_epoch": pending_epoch,
                    "state": state_name,
                    "sent_epoch": sent_epoch,
                }
            ),
        },
    )


async def run_pending_check_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    _ = settings or get_settings()
    factory = session_factory or get_session_factory()
    async with factory() as session:
        if not await get_setting_bool(session, KEY_PENDING_CHECK_ENABLED, False):
            return {"enabled": 0}
        reminder_interval = max(
            60,
            await get_setting_int(
                session,
                KEY_PENDING_CHECK_REMINDER_INTERVAL_SECONDS,
                _DEFAULT_REMINDER_INTERVAL_SECONDS,
            ),
        )
        sysconfig = SysConfig(session)
        auto_types = await sysconfig.get("Ticket::PendingAutoStateType", ["pending auto"])
        if not isinstance(auto_types, list) or not auto_types:
            auto_types = ["pending auto"]
        # Upstream hard-codes the reminder pass to the ``pending reminder``
        # state type (it does not read Ticket::PendingReminderStateType).
        type_params = {
            f"stype_{i}": str(v) for i, v in enumerate([*auto_types, "pending reminder"])
        }
        type_in = ", ".join(f":{key}" for key in type_params)
        ids = [
            int(r[0])
            for r in (
                await session.execute(
                    text(
                        "SELECT t.id FROM ticket t JOIN ticket_state s ON s.id=t.ticket_state_id"
                        " JOIN ticket_state_type st ON st.id=s.type_id"
                        " WHERE st.name IN (" + type_in + ")"
                        " AND t.until_time > 0 AND t.until_time <= :now"
                        " ORDER BY t.until_time"
                    ),
                    {**type_params, "now": int(time.time())},
                )
            ).fetchall()
        ]
        auto_type_names = {str(v).lower() for v in auto_types}
    totals = {"checked": len(ids), "transitioned": 0, "reminded": 0, "errors": 0}
    now_epoch = int(time.time())
    for ticket_id in ids:
        try:
            async with factory() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "SELECT t.tn, t.until_time, t.sla_id, t.queue_id, s.name, st.name"
                            " FROM ticket t JOIN ticket_state s ON s.id=t.ticket_state_id"
                            " JOIN ticket_state_type st ON st.id=s.type_id"
                            " WHERE t.id=:id FOR UPDATE"
                        ),
                        {"id": ticket_id},
                    )
                ).first()
                if row is None or int(row[1] or 0) > now_epoch or int(row[1] or 0) == 0:
                    continue
                tn, _until, sla_id, queue_id, state_name, state_type = row
                sysconfig = SysConfig(session)
                user_id = await sysconfig.postmaster_user_id()
                if str(state_type).lower() in auto_type_names:
                    mappings = await sysconfig.get("Ticket::StateAfterPending", {}) or {}
                    target_name = (
                        mappings.get(str(state_name)) if isinstance(mappings, dict) else None
                    )
                    if not target_name:
                        continue
                    target = (
                        await session.execute(
                            text(
                                "SELECT s.id, st.name FROM ticket_state s"
                                " JOIN ticket_state_type st ON st.id=s.type_id"
                                " WHERE s.name=:name AND s.valid_id=1 LIMIT 1"
                            ),
                            {"name": str(target_name)},
                        )
                    ).first()
                    if target is None:
                        continue
                    await change_state(
                        session,
                        ticket_id=ticket_id,
                        new_state_id=int(target[0]),
                        user_id=user_id,
                        sysconfig=sysconfig,
                    )
                    if str(target[1]).lower() == "closed":
                        await unlock_ticket(
                            session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig
                        )
                    totals["transitioned"] += 1
                    PENDING_TRANSITIONS.inc()
                elif str(state_type).lower() == "pending reminder":
                    calendar = await _calendar_name(
                        session, int(sla_id) if sla_id else None, int(queue_id)
                    )
                    hours, vacations, vacations_once, tz_name = await _calendar_config(
                        sysconfig, calendar
                    )
                    # Upstream skips the reminder unless the preceding ten
                    # minutes contained working time for this calendar.
                    if not working_seconds_between(
                        now_epoch - _REMINDER_LOOKBACK_SECONDS,
                        now_epoch,
                        hours,
                        vacations,
                        vacations_once,
                        tz_name,
                    ):
                        continue
                    pending_epoch = int(_until)
                    last_sent = await _last_matching_reminder_epoch(
                        session, ticket_id, pending_epoch, str(state_name)
                    )
                    if not reminder_due(
                        last_sent_epoch=last_sent,
                        now_epoch=now_epoch,
                        interval_seconds=reminder_interval,
                    ):
                        continue
                    await _emit_reminder(
                        session, ticket_id, str(tn), pending_epoch, str(state_name), now_epoch
                    )
                    totals["reminded"] += 1
                    PENDING_REMINDERS.inc()
        except Exception:
            logger.exception("pending_check_ticket_failed", ticket_id=ticket_id)
            totals["errors"] += 1
            PENDING_ERRORS.inc()
    logger.info("pending_check_tick", **totals)
    return totals


__all__ = ["reminder_due", "run_pending_check_tick", "working_seconds_between"]
