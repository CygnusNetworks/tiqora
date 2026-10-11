"""Ticket event follower: delivers ``ticket_history`` rows to plugin event subscribers.

One tick (worker loop ``plugin_events``):

1. Subscribers are read from the live registry every tick (a late boot retry swaps its contents
   in place); only subscribers of plugins loaded ACTIVE in this process take part.
2. Each subscriber has a hook row ``event:<key>`` with a cursor (last handled
   ``ticket_history.id``). New rows and NULL cursors (plugin switched off and on) start at
   ``MAX(ticket_history.id)``: no replay. Tripped hooks are skipped.
3. A process-level *frontier* is the highest gap-safe id ever returned by the
   :class:`GapTracker` (R-DISPATCH-FRONTIER). Only ids above it are held back (a missing id may
   still be committed late); ids at or below it are settled, holes there are final and never
   held again. Each tick reads up to :data:`EVENT_BATCH` rows above the frontier to move it.
4. Every subscriber reads its own range (cursor, frontier], at most :data:`EVENT_BATCH` rows, so
   a lagging (or just reset) subscriber never holds back the others. Subscribers run
   concurrently. Rows a subscriber does not want only move its cursor; a matching
   event runs the handler under its own timeout. Delivery is *at least once*: the cursor is
   committed after every handled matching event (R-ATLEASTONCE), so a crash re-delivers at
   most the event that was running. ``event.id`` is the idempotency key.
5. A handler error or timeout stops that subscriber for this tick (cursor stays on the failed
   event); after :data:`TRIP_AFTER` consecutive failures its circuit breaker opens until an
   admin presses "Erneut versuchen". Nothing a plugin does stops the tick or other plugins.

A subscriber's run stops starting new handler calls after ``max(interval x 2, 30 s)`` of wall
time (R-HEAD-OF-LINE) or once the worker is stopping; the cursor carries on next tick.
One worker replica is assumed (as for ``report_facts``).
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.id_step import autoincrement_step
from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.plugins.context import minimal_context
from tiqora.plugins.events import (
    EVENT_SUBSCRIBERS,
    EventContext,
    EventSubscriber,
    GapTracker,
    HistoryRow,
    TicketEvent,
    events_from_history,
)
from tiqora.plugins.state import is_active, really_cancelled
from tiqora.plugins.store import (
    MAX_ERROR_LEN,
    advance_hook_cursor,
    ensure_hook,
    record_hook_failure,
    record_hook_success,
)

logger = structlog.get_logger(__name__)

#: consecutive failures that open a subscriber's circuit breaker
TRIP_AFTER: Final = 5
#: history rows read per tick
EVENT_BATCH: Final = 500
#: lower bound of a subscriber's wall-time budget per tick (R-HEAD-OF-LINE)
MIN_RUN_BUDGET_SECONDS: Final = 30

_HISTORY_SQL = text(
    "SELECT th.id, tht.name, th.ticket_id, th.article_id, th.name, th.create_by, th.create_time"
    " FROM ticket_history th JOIN ticket_history_type tht ON tht.id = th.history_type_id"
    " WHERE th.id > :after ORDER BY th.id LIMIT :n"
)
_HISTORY_RANGE_SQL = text(
    "SELECT th.id, tht.name, th.ticket_id, th.article_id, th.name, th.create_by, th.create_time"
    " FROM ticket_history th JOIN ticket_history_type tht ON tht.id = th.history_type_id"
    " WHERE th.id > :after AND th.id <= :upto ORDER BY th.id LIMIT :n"
)

#: the process's gap tracker (created lazily on the first tick without an explicit one)
_TRACKER: GapTracker | None = None
#: frontier per tracker: the highest horizon it ever returned (never moves back)
_FRONTIERS: weakref.WeakKeyDictionary[GapTracker, int] = weakref.WeakKeyDictionary()


def hook_key(subscriber: EventSubscriber) -> str:
    return f"event:{subscriber.key}"


def run_budget_seconds(settings: Settings) -> float:
    return float(max(settings.plugin_events_interval_seconds * 2, MIN_RUN_BUDGET_SECONDS))


async def _process_tracker(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> GapTracker:
    global _TRACKER
    if _TRACKER is None:
        # step AND offset (R-GAP-OFFSET-AMEND): step > 1 without the offset reports a gap
        step, offset = await autoincrement_step(factory)
        _TRACKER = GapTracker(
            hold_seconds=settings.plugin_events_gap_hold_seconds, step=step, offset=offset
        )
    return _TRACKER


async def _max_history_id(session: AsyncSession) -> int:
    value = (await session.execute(text("SELECT MAX(id) FROM ticket_history"))).scalar()
    return int(value or 0)


async def _read_history(
    factory: async_sessionmaker[AsyncSession], after: int, limit: int, upto: int | None = None
) -> list[HistoryRow]:
    async with factory() as session:
        if upto is None:
            result = await session.execute(_HISTORY_SQL, {"after": after, "n": limit})
        else:
            result = await session.execute(
                _HISTORY_RANGE_SQL, {"after": after, "upto": upto, "n": limit}
            )
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


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LEN]


def _cancelled_by_plugin(exc: asyncio.CancelledError) -> str:
    """Error text for a ``CancelledError`` that plugin code raised on its own."""
    return _describe(exc) if str(exc) else "CancelledError: von der Erweiterung selbst ausgelöst"


@dataclass(slots=True)
class _Outcome:
    delivered: int = 0
    failed: bool = False
    tripped: bool = False


@dataclass(frozen=True, slots=True)
class _Window:
    """A subscriber's rows in (cursor, frontier], with their events."""

    rows: Sequence[HistoryRow]
    events: dict[int, TicketEvent]


def _window(rows: Sequence[HistoryRow]) -> _Window:
    return _Window(rows=rows, events={e.id: e for e in events_from_history(rows)})


@dataclass(frozen=True, slots=True)
class _Run:
    factory: async_sessionmaker[AsyncSession]
    frontier: int
    budget: float
    now: Callable[[], datetime]
    wall_clock: Callable[[], float]
    stop: asyncio.Event | None


async def _save_cursor(run: _Run, hook: TiqoraPluginHook, cursor: int) -> None:
    async with run.factory() as session:
        await advance_hook_cursor(session, hook, cursor=cursor)
        await session.commit()


async def _call_handler(
    sub: EventSubscriber,
    ctx: EventContext,
    event: TicketEvent,
    stop: asyncio.Event | None = None,
) -> str | None:
    """Run one handler under its own time budget; returns the error text or None."""
    timeout = asyncio.timeout(sub.timeout_seconds)
    try:
        async with timeout:
            await sub.handler(ctx, event)
    except asyncio.CancelledError as exc:
        if really_cancelled(stop):
            raise  # the worker is shutting down; not the plugin's fault
        return _cancelled_by_plugin(exc)  # raised by plugin code itself (B-I1)
    except TimeoutError as exc:
        if timeout.expired():
            return f"Zeitlimit von {sub.timeout_seconds} Sekunden überschritten"
        return _describe(exc)
    except BaseException as exc:  # noqa: BLE001 - SystemExit & co. from plugin code must not end the worker
        return _describe(exc)
    return None


async def _run_subscriber(
    run: _Run, sub: EventSubscriber, hook: TiqoraPluginHook, window: _Window
) -> _Outcome:
    """Deliver this subscriber's events in id order; never raises for plugin or DB errors."""
    outcome = _Outcome()
    started = run.wall_clock()
    assert hook.cursor is not None  # live hooks only (ensure_hook resolved NULL cursors)
    cursor = hook.cursor
    pending: int | None = None  # last row passed over without a handler call, not yet stored
    plugin = sub.context or minimal_context(sub.plugin_id)
    try:
        for row in window.rows:
            if row.id <= cursor:
                continue
            if row.id > run.frontier:
                break
            event = window.events.get(row.id)
            if event is None or not sub.matches(event.event):
                pending = row.id
                continue
            if (run.stop is not None and run.stop.is_set()) or (
                run.wall_clock() - started >= run.budget
            ):
                break  # carries on next tick; not a failure
            ctx = EventContext(plugin=plugin, attempt=hook.consecutive_failures + 1)
            error = await _call_handler(sub, ctx, event, run.stop)
            if error is None:
                async with run.factory() as session:
                    await record_hook_success(session, hook, cursor=row.id, now=run.now())
                    await session.commit()
                outcome.delivered += 1
                cursor = row.id
                pending = None
                continue
            async with run.factory() as session:
                tripped = await record_hook_failure(
                    session,
                    hook,
                    error=error,
                    now=run.now(),
                    trip_after=TRIP_AFTER,
                    cursor=pending,
                )
                await session.commit()
            pending = None
            outcome.failed = True
            logger.warning(
                "plugin_subscriber_failed",
                subscriber=sub.key,
                event_id=row.id,
                event_name=event.event,
                error=error,
                consecutive_failures=hook.consecutive_failures,
            )
            if tripped:
                outcome.tripped = True
                logger.error("plugin_subscriber_tripped", subscriber=sub.key, failures=TRIP_AFTER)
            break
        if pending is not None:
            await _save_cursor(run, hook, pending)
    except asyncio.CancelledError:
        if really_cancelled(run.stop):
            raise
        logger.exception("plugin_subscriber_bookkeeping_failed", subscriber=sub.key)
    except Exception:  # noqa: BLE001 - bookkeeping errors stay with this subscriber
        logger.exception("plugin_subscriber_bookkeeping_failed", subscriber=sub.key)
    return outcome


async def run_plugin_events_tick(
    *,
    registry: Registry,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    tracker: GapTracker | None = None,
    now: Callable[[], datetime] = utc_now,
    wall_clock: Callable[[], float] = time.monotonic,
    stop: asyncio.Event | None = None,
) -> dict[str, int]:
    """One delivery round; returns ``{"subscribers", "events", "failed", "tripped", "horizon"}``.

    ``stop`` (the worker's stop event): once set, no new handler call starts.
    """
    subs = [s for s in registry.items(EVENT_SUBSCRIBERS) if is_active(s.plugin_id)]
    result = {"subscribers": len(subs), "events": 0, "failed": 0, "tripped": 0, "horizon": 0}
    if not subs:
        return result
    if tracker is None:
        tracker = await _process_tracker(settings, session_factory)

    async with session_factory() as session:
        start = await _max_history_id(session)
        hooks = {
            hook_key(s): await ensure_hook(
                session,
                hook_key=hook_key(s),
                plugin_id=s.plugin_id,
                kind="event",
                initial_cursor=start,
                now=now(),
            )
            for s in subs
        }
        await session.commit()

    live = [
        (s, hooks[hook_key(s)])
        for s in subs
        if hooks[hook_key(s)].tripped_at is None and hooks[hook_key(s)].cursor is not None
    ]
    if not live:
        return result
    cursors = [int(h.cursor) for _s, h in live if h.cursor is not None]
    previous = _FRONTIERS.get(tracker)
    if previous is None:
        # Cold start: everything up to the highest stored cursor was delivered past a horizon
        # of an earlier run, so it is settled. Cursors equal to ``start`` may be hooks created
        # right now at the end of the history and prove nothing; only ids above the result go
        # through the hold.
        old = [int(h.cursor) for h in hooks.values() if h.cursor is not None and h.cursor < start]
        previous = max(old) if old else min(cursors)
    leading = await _read_history(session_factory, previous, EVENT_BATCH)
    frontier = max(previous, tracker.horizon(previous, [r.id for r in leading], wall_clock()))
    _FRONTIERS[tracker] = frontier
    result["horizon"] = frontier

    # each subscriber reads its own range; rows above the old frontier come from ``leading``
    lagging: dict[int, _Window] = {}
    windows: list[_Window] = []
    for cursor in cursors:
        if cursor >= previous:
            windows.append(_window([r for r in leading if cursor < r.id <= frontier]))
            continue
        if cursor not in lagging:
            rows = await _read_history(session_factory, cursor, EVENT_BATCH, upto=frontier)
            lagging[cursor] = _window(rows)
        windows.append(lagging[cursor])

    run = _Run(
        factory=session_factory,
        frontier=frontier,
        budget=run_budget_seconds(settings),
        now=now,
        wall_clock=wall_clock,
        stop=stop,
    )
    outcomes = await asyncio.gather(
        *(_run_subscriber(run, s, h, w) for (s, h), w in zip(live, windows, strict=True)),
        return_exceptions=True,
    )
    for (sub, _hook), outcome in zip(live, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, asyncio.CancelledError) and really_cancelled(stop):
                raise outcome
            logger.error("plugin_subscriber_crashed", subscriber=sub.key, error=_describe(outcome))
            continue
        result["events"] += outcome.delivered
        result["failed"] += int(outcome.failed)
        result["tripped"] += int(outcome.tripped)
    return result
