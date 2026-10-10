"""Scheduled plugin tasks: schedule value object and due-time calculation."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.extensions.registry import ExtensionPoint, Registry
from tiqora.jobs.service import utc_now
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active
from tiqora.plugins.store import (
    MAX_ERROR_LEN,
    ensure_hook,
    record_hook_failure,
    record_hook_run_start,
    record_hook_success,
)
from tiqora.util.cron import CronExpr

logger = structlog.get_logger(__name__)

#: tasks of one tick that run at the same time
MAX_PARALLEL_TASKS: Final = 4
#: consecutive failures that open a task's circuit breaker (same as for event subscribers)
TRIP_AFTER: Final = 5
#: default wall time of a tick after which no further task is started (R-HEAD-OF-LINE)
MIN_RUN_BUDGET_SECONDS: Final = 30

MIN_INTERVAL_SECONDS = 30
_VALIDATION_REFERENCE = datetime(2026, 1, 1)
_DAILY_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


@dataclass(frozen=True, slots=True)
class Schedule:
    every_seconds: int | None = None
    daily_at: str | None = None  # "HH:MM" UTC
    cron: str | None = None

    def validate(self) -> None:
        set_count = sum(v is not None for v in (self.every_seconds, self.daily_at, self.cron))
        if set_count != 1:
            raise ValueError(
                "Ungültiger Zeitplan: genau eine Angabe aus every_seconds, "
                "daily_at oder cron ist nötig"
            )
        if self.every_seconds is not None and self.every_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(
                f"Ungültiger Zeitplan: das Intervall muss mindestens "
                f"{MIN_INTERVAL_SECONDS} Sekunden betragen"
            )
        if self.daily_at is not None and not _DAILY_RE.fullmatch(self.daily_at):
            raise ValueError(
                f"Ungültiger Zeitplan: „{self.daily_at}“ ist keine Uhrzeit im Format HH:MM"
            )
        if self.cron is not None:
            try:
                CronExpr.parse(self.cron).next_after(_VALIDATION_REFERENCE)
            except ValueError as exc:
                if "kein" in str(exc):
                    raise ValueError(
                        f"Ungültiger Zeitplan „{self.cron}“: kein gültiger Termin"
                    ) from exc
                raise

    def describe(self) -> str:
        if self.every_seconds is not None:
            n = self.every_seconds
            if n == 60:
                return "jede Minute"
            if n == 3600:
                return "jede Stunde"
            if n % 3600 == 0:
                return f"alle {n // 3600} Stunden"
            if n % 60 == 0:
                return f"alle {n // 60} Minuten"
            return f"alle {n} Sekunden"
        if self.daily_at is not None:
            return f"täglich um {self.daily_at} UTC"
        return f"nach Zeitplan „{self.cron}“ (UTC)"


def next_due(schedule: Schedule, *, last_run_at: datetime | None, created_at: datetime) -> datetime:
    """Next run time (naive UTC) of a validated schedule."""
    if schedule.every_seconds is not None:
        if last_run_at is None:
            return created_at
        return last_run_at + timedelta(seconds=schedule.every_seconds)
    base = last_run_at or created_at
    if schedule.daily_at is not None:
        hh, mm = schedule.daily_at.split(":")
        return CronExpr.parse(f"{int(mm)} {int(hh)} * * *").next_after(base)
    if schedule.cron is None:
        raise ValueError("Ungültiger Zeitplan: keine Angabe gesetzt")
    return CronExpr.parse(schedule.cron).next_after(base)


@dataclass(frozen=True, slots=True)
class TaskContext:
    plugin: PluginContext
    scheduled_for: datetime
    last_run_at: datetime | None


TaskRun = Callable[[TaskContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    schedule: Schedule
    run: TaskRun
    timeout_seconds: int
    context: PluginContext | None = None


SCHEDULED_TASKS: Final[ExtensionPoint[ScheduledTask]] = ExtensionPoint(
    key="core.worker.scheduled_tasks",
    item_key=lambda t: t.key,
    description="Periodic tasks run by the worker.",
    sdk_stable=True,
)


def hook_key(task: ScheduledTask) -> str:
    return f"task:{task.key}"


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LEN]


async def _call_task(task: ScheduledTask, ctx: TaskContext) -> str | None:
    """Run one task under its own timeout; returns the error text or None."""
    timeout = asyncio.timeout(task.timeout_seconds)
    try:
        async with timeout:
            await task.run(ctx)
    except asyncio.CancelledError:
        raise  # the worker is shutting down; not the plugin's fault
    except TimeoutError as exc:
        if timeout.expired():
            return f"Zeitlimit von {task.timeout_seconds} Sekunden überschritten"
        return _describe(exc)
    except BaseException as exc:  # noqa: BLE001 - SystemExit & co. from plugin code must not end the worker
        return _describe(exc)
    return None


@dataclass(slots=True)
class _Outcome:
    ran: bool = False
    failed: bool = False
    tripped: bool = False


async def _run_task(
    task: ScheduledTask,
    hook: TiqoraPluginHook,
    *,
    factory: async_sessionmaker[AsyncSession],
    sem: asyncio.Semaphore,
    now: Callable[[], datetime],
    wall_clock: Callable[[], float],
    started: float,
    budget: float,
    stop: asyncio.Event | None,
) -> _Outcome:
    """Run one due task; never raises for plugin or DB errors."""
    outcome = _Outcome()
    try:
        async with sem:
            # a task that waited for a slot behind slow ones is left for the next tick
            if (stop is not None and stop.is_set()) or wall_clock() - started >= budget:
                return outcome
            scheduled_for = next_due(
                task.schedule, last_run_at=hook.last_run_at, created_at=hook.created_at
            )
            last_run_at = hook.last_run_at
            async with factory() as session:
                await record_hook_run_start(session, hook, now=now())
                await session.commit()
            outcome.ran = True
            ctx = TaskContext(
                plugin=task.context or minimal_context(task.plugin_id),
                scheduled_for=scheduled_for,
                last_run_at=last_run_at,
            )
            error = await _call_task(task, ctx)
            async with factory() as session:
                if error is None:
                    await record_hook_success(session, hook, cursor=None, now=now())
                else:
                    outcome.failed = True
                    outcome.tripped = await record_hook_failure(
                        session, hook, error=error, now=now(), trip_after=TRIP_AFTER
                    )
                await session.commit()
            if error is not None:
                logger.warning(
                    "plugin_task_failed",
                    task=task.key,
                    error=error,
                    consecutive_failures=hook.consecutive_failures,
                )
                if outcome.tripped:
                    logger.error("plugin_task_tripped", task=task.key, failures=TRIP_AFTER)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - bookkeeping errors stay with this task
        logger.exception("plugin_task_bookkeeping_failed", task=task.key)
    return outcome


async def run_plugin_tasks_tick(
    *,
    registry: Registry,
    session_factory: async_sessionmaker[AsyncSession],
    now: Callable[[], datetime] = utc_now,
    wall_clock: Callable[[], float] = time.monotonic,
    stop: asyncio.Event | None = None,
    budget_seconds: float = MIN_RUN_BUDGET_SECONDS,
) -> dict[str, int]:
    """Start every due task of an active plugin; returns ``{"tasks", "ran", "failed", "tripped"}``.

    Tasks come from the live registry on every tick (a late plugin boot swaps its contents in
    place). ``last_run_at`` is committed before a run, so a crash never hot-loops, and a
    schedule that was missed during downtime runs once (the next due time counts from the
    start of that run, never from the missed slots). At most :data:`MAX_PARALLEL_TASKS` run at
    once; once ``budget_seconds`` of wall time have passed (or ``stop`` is set) no further task
    is started, the rest stays due for the next tick. A failure or timeout is booked on the
    task's hook row only; after :data:`TRIP_AFTER` in a row the task stops until an admin
    retries it. One worker replica is assumed.
    """
    tasks = [t for t in registry.items(SCHEDULED_TASKS) if is_active(t.plugin_id)]
    result = {"tasks": len(tasks), "ran": 0, "failed": 0, "tripped": 0}
    if not tasks:
        return result
    started = wall_clock()
    async with session_factory() as session:
        stamp = now()
        hooks = {
            hook_key(t): await ensure_hook(
                session,
                hook_key=hook_key(t),
                plugin_id=t.plugin_id,
                kind="task",
                initial_cursor=None,
                now=stamp,
            )
            for t in tasks
        }
        await session.commit()

    due: list[tuple[ScheduledTask, TiqoraPluginHook]] = []
    for task in tasks:
        hook = hooks[hook_key(task)]
        if hook.tripped_at is not None:
            continue
        when = next_due(task.schedule, last_run_at=hook.last_run_at, created_at=hook.created_at)
        if when <= stamp:
            due.append((task, hook))
    sem = asyncio.Semaphore(MAX_PARALLEL_TASKS)
    outcomes = await asyncio.gather(
        *(
            _run_task(
                t,
                h,
                factory=session_factory,
                sem=sem,
                now=now,
                wall_clock=wall_clock,
                started=started,
                budget=budget_seconds,
                stop=stop,
            )
            for t, h in due
        ),
        return_exceptions=True,
    )
    for (task, _hook), outcome in zip(due, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            logger.error("plugin_task_crashed", task=task.key, error=_describe(outcome))
            continue
        result["ran"] += int(outcome.ran)
        result["failed"] += int(outcome.failed)
        result["tripped"] += int(outcome.tripped)
    return result
