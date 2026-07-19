"""Escalation sweep — feature-flagged takeover of Znuny's escalation daemon task.

Gated by the ``daemon.escalation.enabled`` tiqora_settings key (default OFF —
see ``tiqora.domain.settings_store``). Ports two Znuny pieces:

- ``Kernel::System::Console::Command::Maint::Ticket::EscalationIndexRebuild``
  (behind ``Daemon::SchedulerCronTaskManager::Task###EscalationCheck``): batched
  recompute of the four ``ticket.escalation_*`` columns for every ticket whose
  state type is not ``merge``/``close``/``remove`` — the actual column math is
  ``tiqora.znuny.escalation.escalation_index_build`` (ported separately).
- ``TriggerEscalationStartEvents`` semantics: Znuny fires
  ``Escalation{ResponseTime,UpdateTime,SolutionTime}{Start,Stop}`` ticket
  events (and matching ``ticket_history`` rows — see
  ``scripts/database/initial_insert.xml``) whenever a column transitions
  between "unset" (0) and "set" (destination epoch), plus a one-shot
  ``Escalation*TimeNotifyBefore`` event/history row once the destination time
  is within the configured notify-before window (``daemon.escalation.
  notify_before_seconds``; Znuny's default is
  ``Ticket::Frontend::AgentTicketEscalationView`` un-notified list uses the SLA/
  queue *NotifyBefore* percentage — Tiqora simplifies this to a fixed window,
  documented as a divergence).

Idempotent by construction: Start/Stop fire only on a 0↔non-zero transition
(comparing the column values from before/after the recompute), and
NotifyBefore dedupes against an existing history row carrying the exact
destination-time value as its ``name`` (so a rerun before the destination time
changes writes nothing new).
"""

from __future__ import annotations

import json
import time

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_ESCALATION_BATCH_SIZE,
    KEY_ESCALATION_ENABLED,
    KEY_ESCALATION_NOTIFY_BEFORE_SECONDS,
    get_setting_bool,
    get_setting_int,
)
from tiqora.znuny.escalation import escalation_index_build
from tiqora.znuny.history import history_add
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

ESCALATION_TICKETS_SWEPT = Counter(
    "tiqora_escalation_tickets_swept_total", "Tickets whose escalation index was recomputed"
)
ESCALATION_EVENTS_FIRED = Counter(
    "tiqora_escalation_events_fired_total",
    "Escalation Start/Stop/NotifyBefore events fired",
    ["event"],
)
ESCALATION_ERRORS = Counter("tiqora_escalation_errors_total", "Per-ticket escalation sweep errors")

_DEFAULT_BATCH_SIZE = 500
_DEFAULT_NOTIFY_BEFORE_SECONDS = 24 * 3600

# (column, Start history/event type, Stop history/event type, NotifyBefore history/event type)
_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    (
        "escalation_response_time",
        "EscalationResponseTimeStart",
        "EscalationResponseTimeStop",
        "EscalationResponseTimeNotifyBefore",
    ),
    (
        "escalation_update_time",
        "EscalationUpdateTimeStart",
        "EscalationUpdateTimeStop",
        "EscalationUpdateTimeNotifyBefore",
    ),
    (
        "escalation_solution_time",
        "EscalationSolutionTimeStart",
        "EscalationSolutionTimeStop",
        "EscalationSolutionTimeNotifyBefore",
    ),
)


async def _emit_event(session: AsyncSession, ticket_id: int, event_type: str) -> None:
    """Write one row to tiqora_event_outbox (mirrors ticket_write_service._emit_event)."""
    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox"
            " (event_type, ticket_id, payload, created, processed)"
            " VALUES (:et, :tid, :pl, current_timestamp, :pr)"
        ),
        {"et": event_type, "tid": ticket_id, "pl": json.dumps({}), "pr": False},
    )


async def _sweepable_ticket_ids(session: AsyncSession, batch_size: int) -> list[int]:
    """Open (non merge/close/remove) tickets, oldest-recomputed first.

    Ordering by ``change_time`` (ascending) approximates Znuny's own
    RebuildEscalationIndexOnline default (unordered full-table scan) while
    guaranteeing every ticket is eventually revisited within a bounded number
    of ticks, not just the lowest-id ones.
    """
    rows = (
        await session.execute(
            text(
                "SELECT t.id FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE tst.name NOT IN ('merge', 'close', 'remove')"
                " ORDER BY t.change_time ASC, t.id ASC LIMIT :n"
            ),
            {"n": batch_size},
        )
    ).fetchall()
    return [int(r[0]) for r in rows]


async def _notify_before_seen(
    session: AsyncSession, ticket_id: int, history_type: str, marker: str
) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT h.id FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid AND ht.name = :htype AND h.name = :marker LIMIT 1"
            ),
            {"tid": ticket_id, "htype": history_type, "marker": marker},
        )
    ).first()
    return row is not None


async def sweep_ticket(
    session: AsyncSession,
    sysconfig: SysConfig,
    ticket_id: int,
    *,
    user_id: int,
    notify_before_seconds: int,
) -> dict[str, int]:
    """Recompute one ticket's escalation index and fire Start/Stop/NotifyBefore events."""
    fired = {"start": 0, "stop": 0, "notify_before": 0}

    before = (
        await session.execute(
            text(
                "SELECT escalation_response_time, escalation_update_time,"
                " escalation_solution_time FROM ticket WHERE id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if before is None:
        return fired

    await escalation_index_build(session, ticket_id, user_id, sysconfig)

    after = (
        await session.execute(
            text(
                "SELECT escalation_response_time, escalation_update_time,"
                " escalation_solution_time FROM ticket WHERE id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if after is None:
        return fired

    now_epoch = int(time.time())
    for idx, (_column, start_type, stop_type, notify_type) in enumerate(_COLUMNS):
        old_v = int(before[idx] or 0)
        new_v = int(after[idx] or 0)

        if new_v and not old_v:
            await history_add(
                session, ticket_id=ticket_id, history_type=start_type, name="", user_id=user_id
            )
            await _emit_event(session, ticket_id, start_type)
            ESCALATION_EVENTS_FIRED.labels(event=start_type).inc()
            fired["start"] += 1
        elif old_v and not new_v:
            await history_add(
                session, ticket_id=ticket_id, history_type=stop_type, name="", user_id=user_id
            )
            await _emit_event(session, ticket_id, stop_type)
            ESCALATION_EVENTS_FIRED.labels(event=stop_type).inc()
            fired["stop"] += 1

        if new_v and new_v > now_epoch and (new_v - now_epoch) <= notify_before_seconds:
            marker = str(new_v)
            if not await _notify_before_seen(session, ticket_id, notify_type, marker):
                await history_add(
                    session,
                    ticket_id=ticket_id,
                    history_type=notify_type,
                    name=marker,
                    user_id=user_id,
                )
                await _emit_event(session, ticket_id, notify_type)
                ESCALATION_EVENTS_FIRED.labels(event=notify_type).inc()
                fired["notify_before"] += 1

    return fired


async def run_escalation_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """One scheduler tick: check the feature flag, then sweep a batch of tickets."""
    _ = settings or get_settings()  # reserved for future config-driven tuning
    factory = session_factory or get_session_factory()

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_ESCALATION_ENABLED, False)
        if not enabled:
            logger.debug("escalation_sweep_disabled")
            return {"enabled": 0}
        batch_size = await get_setting_int(session, KEY_ESCALATION_BATCH_SIZE, _DEFAULT_BATCH_SIZE)
        notify_before = await get_setting_int(
            session, KEY_ESCALATION_NOTIFY_BEFORE_SECONDS, _DEFAULT_NOTIFY_BEFORE_SECONDS
        )
        sysconfig = SysConfig(session)
        user_id = await sysconfig.postmaster_user_id()
        ticket_ids = await _sweepable_ticket_ids(session, batch_size)

    totals = {"swept": 0, "start": 0, "stop": 0, "notify_before": 0, "errors": 0}
    for ticket_id in ticket_ids:
        try:
            async with factory() as session, session.begin():
                sysconfig = SysConfig(session)
                fired = await sweep_ticket(
                    session,
                    sysconfig,
                    ticket_id,
                    user_id=user_id,
                    notify_before_seconds=notify_before,
                )
            totals["swept"] += 1
            ESCALATION_TICKETS_SWEPT.inc()
            for key in ("start", "stop", "notify_before"):
                totals[key] += fired[key]
        except Exception:  # noqa: BLE001 — one broken ticket must not stop the sweep
            logger.exception("escalation_sweep_ticket_failed", ticket_id=ticket_id)
            totals["errors"] += 1
            ESCALATION_ERRORS.inc()

    logger.info("escalation_tick", **totals)
    return totals


__all__ = ["run_escalation_tick", "sweep_ticket"]
