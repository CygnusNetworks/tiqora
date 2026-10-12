"""Ticket event follower: fires the GenericInterface invokers for ``ticket_history`` rows (G12).

One tick (worker loop ``gi_requester``, called every interval; the tick reads the takeover
flag itself):

1. Flag off: the cursor follows ``MAX(ticket_history.id)`` and the ``last_enabled_tick`` marker
   is deleted. Nothing is ever replayed after the flag goes on. A NULL cursor, a missing or
   stale marker (see :func:`stale_marker_seconds`, at least 180 s) on an enabled tick resets the
   cursor to the head too; the Daemons API does the same when it enables the service. So a
   worker that was down, or a flag flipped between two ticks, can never replay old history.
2. Enabled: read up to :data:`EVENT_BATCH` rows above the cursor, cut them at the gap-safe
   horizon (:class:`~tiqora.plugins.events.GapTracker`), map them to events, and match the
   invokers (:func:`tiqora.gi.events.match_invokers`).
3. In **one** transaction: enqueue every match and move the cursor to the horizon. A failure
   rolls both back, the next tick reads the same rows again, so every match is enqueued
   exactly once.

Conditions see the ticket at tick time. A tick stops starting new events after
:data:`TICK_BUDGET_SECONDS` of wall time and moves the cursor only past what it finished.
One worker replica is assumed (as for the plugin event follower).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import structlog
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.id_step import autoincrement_step
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import (
    KEY_GI_REQUESTER_CURSOR,
    KEY_GI_REQUESTER_ENABLED,
    KEY_GI_REQUESTER_INTERVAL_SECONDS,
    KEY_GI_REQUESTER_LAST_ENABLED_TICK,
    get_setting,
    get_setting_bool,
    get_setting_int,
)
from tiqora.gi.conditions import ConditionWarning
from tiqora.gi.deep_get import ticket_deep_get
from tiqora.gi.events import invoker_can_fire, match_invokers
from tiqora.gi.jobs import GiInvokePayload, enqueue_invocation
from tiqora.gi.loader import GiWebservice, GiWebservices
from tiqora.gi.takeover import znuny_gi_handler_active
from tiqora.jobs.service import utc_now
from tiqora.plugins.events import GapTracker, HistoryRow, events_from_history
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

__all__ = ["EVENT_BATCH", "TICK_BUDGET_SECONDS", "make_gap_tracker", "run_gi_requester_tick"]

#: history rows read per tick
EVENT_BATCH: Final = 500
#: wall time after which a tick starts no further event
TICK_BUDGET_SECONDS: Final = 30.0
#: a marker older than max(floor, budget + tick timeout + this many intervals) means the
#: follower did not run in between (a long tick or a normal worker restart must not count)
STALE_MARKER_INTERVALS: Final = 3
STALE_MARKER_FLOOR_SECONDS: Final = 180.0
#: upper bound for what one event may add to a tick beyond the budget (conditions, deep_get)
TICK_OVERRUN_SECONDS: Final = 30.0


def stale_marker_seconds(interval: int) -> float:
    """Age after which an enabled tick no longer trusts the cursor."""
    return max(
        STALE_MARKER_FLOOR_SECONDS,
        TICK_BUDGET_SECONDS + TICK_OVERRUN_SECONDS + STALE_MARKER_INTERVALS * interval,
    )


# Copy of tiqora.plugins.event_dispatch._HISTORY_SQL (a private name there).
_HISTORY_SQL = text(
    "SELECT th.id, tht.name, th.ticket_id, th.article_id, th.name, th.create_by, th.create_time"
    " FROM ticket_history th JOIN ticket_history_type tht ON tht.id = th.history_type_id"
    " WHERE th.id > :after ORDER BY th.id LIMIT :n"
)


async def make_gap_tracker(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> GapTracker:
    """The process-level tracker (create once, pass to every tick)."""
    step, offset = await autoincrement_step(session_factory)
    return GapTracker(hold_seconds=settings.gi_requester_gap_hold_seconds, step=step, offset=offset)


async def _max_history_id(session: AsyncSession) -> int:
    value = (await session.execute(text("SELECT MAX(id) FROM ticket_history"))).scalar()
    return int(value or 0)


async def _write_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert without committing (``set_setting`` commits, which would break the atomic step)."""
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is None:
        session.add(TiqoraSettings(key=key, value=value))
    elif existing.value != value:
        existing.value = value
    await session.flush()


async def _write_cursor(session: AsyncSession, cursor: int) -> None:
    await _write_setting(session, KEY_GI_REQUESTER_CURSOR, str(cursor))


def _parse_int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _marker_is_fresh(raw: str | None, *, max_age_seconds: float) -> bool:
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return False
    age = (utc_now() - stamp.replace(tzinfo=None)).total_seconds()
    return 0 <= age <= max_age_seconds


async def _prepare(session: AsyncSession, settings: Settings) -> tuple[dict[str, int] | None, int]:
    """Flag and gap handling (step 1). ``(result, cursor)``: *result* is set when the tick is
    over, else *cursor* is where the history is read from."""
    if not await get_setting_bool(session, KEY_GI_REQUESTER_ENABLED, False):
        head = await _max_history_id(session)
        await _write_cursor(session, head)
        await session.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key == KEY_GI_REQUESTER_LAST_ENABLED_TICK)
        )
        await session.commit()
        return {"enabled": 0}, head
    interval = max(
        5,
        await get_setting_int(
            session, KEY_GI_REQUESTER_INTERVAL_SECONDS, settings.gi_requester_interval_seconds
        ),
    )
    cursor = _parse_int(await get_setting(session, KEY_GI_REQUESTER_CURSOR))
    fresh = _marker_is_fresh(
        await get_setting(session, KEY_GI_REQUESTER_LAST_ENABLED_TICK),
        max_age_seconds=stale_marker_seconds(interval),
    )
    await _write_setting(session, KEY_GI_REQUESTER_LAST_ENABLED_TICK, utc_now().isoformat())
    if cursor is None or not fresh:
        head = await _max_history_id(session)
        await _write_cursor(session, head)
        await session.commit()
        skipped = max(0, head - cursor) if cursor is not None else 0
        log = logger.warning if skipped else logger.info
        log(
            "gi_requester_cursor_reset",
            cursor=head,
            had_cursor=cursor is not None,
            skipped=skipped,
        )
        return {"enabled": 1, "events": 0, "enqueued": 0, "cursor": head}, head
    await session.commit()
    return None, cursor


async def _read_history(session: AsyncSession, after: int) -> list[HistoryRow]:
    result = await session.execute(_HISTORY_SQL, {"after": after, "n": EVENT_BATCH})
    return [
        HistoryRow(
            id=int(r[0]),
            history_type=str(r[1]),
            ticket_id=int(r[2]),
            article_id=int(r[3]) if r[3] is not None else None,
            name=str(r[4] or ""),
            create_by=int(r[5]),
            create_time=r[6],
        )
        for r in result.all()
    ]


@dataclass(slots=True)
class _Matched:
    payloads: list[GiInvokePayload]
    events: int
    cursor: int


async def _match_rows(
    session: AsyncSession,
    rows: list[HistoryRow],
    *,
    cursor: int,
    horizon: int,
    ws_list: list[GiWebservice],
    started: float,
) -> _Matched:
    """Match the events of *rows* (all <= horizon) in id order; stop when the budget is spent."""
    sysconfig = SysConfig(session)
    cache: dict[tuple[int, int | None], dict[str, Any] | None] = {}

    async def deep_get(ticket_id: int, article_id: int | None) -> dict[str, Any] | None:
        key = (ticket_id, article_id)
        if key not in cache:
            cache[key] = await ticket_deep_get(
                session,
                ticket_id,
                article_id=article_id,
                all_attachments=False,
                sysconfig=sysconfig,
            )
        return cache[key]

    events = {e.id: e for e in events_from_history(rows)}
    payloads: list[GiInvokePayload] = []
    seen: set[tuple[str, str]] = set()
    done = cursor
    handled = 0
    for row in rows:
        event = events.get(row.id)
        if event is not None and ws_list:
            if time.monotonic() - started >= TICK_BUDGET_SECONDS:
                return _Matched(payloads, handled, done)
            handled += 1
            for ws in ws_list:  # one at a time: a broken webservice must not stop the others
                warnings: list[ConditionWarning] = []
                try:
                    payloads += await match_invokers([ws], event, deep_get, warnings=warnings)
                except SQLAlchemyError:
                    raise
                except Exception:  # noqa: BLE001 - one broken webservice, not the whole tick
                    logger.exception(
                        "gi_requester_match_failed", webservice_id=ws.id, event_id=event.id
                    )
                for w in warnings:
                    if (w.invoker, w.message) not in seen:
                        seen.add((w.invoker, w.message))
                        logger.warning(
                            "gi_requester_condition_warning",
                            webservice_id=ws.id,
                            invoker=w.invoker,
                            gi_event=w.event,
                            message=w.message,
                        )
        done = row.id
    return _Matched(payloads, handled, horizon)


async def run_gi_requester_tick(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    webservices: GiWebservices,
    tracker: GapTracker | None = None,
    now: float | None = None,
) -> dict[str, int]:
    """One follower round; returns ``{"enabled": 0}`` when the flag is off, else
    ``{"enabled": 1, "events", "enqueued", "cursor"}``.

    *tracker* must be the same object on every tick (it remembers the holes it holds back);
    without one a tick uses a fresh tracker that holds no gap. *now* is the monotonic clock for
    the tracker.
    """
    started = time.monotonic()
    async with session_factory() as session:
        result, cursor = await _prepare(session, settings)
    if result is not None:
        return result
    if tracker is None:
        step, offset = await autoincrement_step(session_factory)
        tracker = GapTracker(hold_seconds=0, step=step, offset=offset)
    clock = time.monotonic() if now is None else now

    async with session_factory() as read:
        sysconfig = SysConfig(read)
        await webservices.refresh(read, sysconfig)
        ws_list = [
            ws
            for ws in webservices.all()
            if any(invoker_can_fire(i) and i.events for i in ws.requester.invokers.values())
        ]
        if await znuny_gi_handler_active(read):
            logger.warning("gi_requester_znuny_handler_active")
        rows = await _read_history(read, cursor)
        horizon = tracker.horizon(cursor, [r.id for r in rows], clock)
        due = [r for r in rows if r.id <= horizon]
        if horizon == cursor:
            return {"enabled": 1, "events": 0, "enqueued": 0, "cursor": cursor}
        matched = await _match_rows(
            read, due, cursor=cursor, horizon=horizon, ws_list=ws_list, started=started
        )
    if matched.cursor == cursor and not matched.payloads:
        return {"enabled": 1, "events": matched.events, "enqueued": 0, "cursor": cursor}

    async with session_factory() as write:
        for payload in matched.payloads:
            await enqueue_invocation(write, payload=payload, created_by=None)
        await _write_cursor(write, matched.cursor)
        # refreshed at the end too, so a long tick does not age the marker
        await _write_setting(write, KEY_GI_REQUESTER_LAST_ENABLED_TICK, utc_now().isoformat())
        await write.commit()
    return {
        "enabled": 1,
        "events": matched.events,
        "enqueued": len(matched.payloads),
        "cursor": matched.cursor,
    }
