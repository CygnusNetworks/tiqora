"""Process registry and coordinated restarts after a runtime plugin install (D10).

Every api/worker/mcp/ai-worker process registers a ``tiqora_process`` row and heartbeats.
A restart request (``tiqora_restart``) lists the roles that were alive; the processes
restart role by role in :data:`RESTART_ORDER` (api last, it serves the progress UI), one
process of a role at a time. :func:`decide` is pure; the DB helpers commit per call.

Only ``tiqora_*`` tables are touched and they are never joined with each other.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraProcess, TiqoraRestart
from tiqora.domain.schemas import UtcDateTime
from tiqora.plugins import state

log = structlog.get_logger(__name__)

ProcessRole = Literal["api", "worker", "mcp", "ai-worker"]
RESTART_ORDER: Final[tuple[ProcessRole, ...]] = ("worker", "ai-worker", "mcp", "api")
#: EX_TEMPFAIL: also restarted under ``restart: on-failure``
RESTART_EXIT_CODE: Final = 75
HEARTBEAT_SECONDS: Final = 5
#: a row with an older heartbeat counts as gone
LIVE_WITHIN_SECONDS: Final = 30
#: rows without a heartbeat for this long are deleted
DEAD_AFTER: Final = timedelta(hours=1)

ROLE_LABEL: Final[dict[str, str]] = {
    "api": "Weboberfläche und Schnittstelle",
    "worker": "Hintergrunddienst",
    "mcp": "KI-Schnittstelle (MCP)",
    "ai-worker": "KI-Hintergrunddienst",
}

#: job kinds of the ``plugins`` lane (install, uninstall, fetch, repository refresh)
PLUGIN_JOB_KINDS: Final[tuple[str, ...]] = (
    "core.plugin_install",
    "core.plugin_uninstall",
    "core.plugin_fetch",
    "core.plugin_repository_refresh",
)
BUSY_MESSAGE: Final = "Eine Erweiterung wird gerade installiert oder entfernt"

#: set when this process decided to exit for a coordinated restart
_REQUESTED = False


def restart_requested() -> bool:
    """True once this process left because of a coordinated restart (exit code 75)."""
    return _REQUESTED


def restart_roles() -> tuple[ProcessRole, ...]:
    """Roles that host plugins, in restart order."""
    hosts = set(state.PLUGIN_ROLES)
    return tuple(r for r in RESTART_ORDER if r in hosts)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class ProcessRow:
    instance_id: str
    role: str
    boot_generation: int
    heartbeat_at: datetime
    exiting_at: datetime | None


@dataclass(frozen=True, slots=True)
class RestartRow:
    generation: int
    requested_at: datetime
    roles: tuple[str, ...]
    finished_at: datetime | None
    failed_message: str | None


@dataclass(frozen=True, slots=True)
class Decision:
    action: Literal["stay", "exit", "finish", "fail"]
    message: str | None = None


_STAY: Final = Decision("stay")


def _live(p: ProcessRow, now: datetime) -> bool:
    return (now - p.heartbeat_at).total_seconds() <= LIVE_WITHIN_SECONDS


def _role_view(
    role: str, target: RestartRow, processes: Sequence[ProcessRow], now: datetime
) -> tuple[bool, bool]:
    """``(done, started)`` of one role for a restart target.

    Done: a live process of the new generation and none of the old one (a process that
    already set ``exiting_at`` has committed to leave and no longer counts). ``started``:
    a process of the role left or a new one is live. Timeouts are :func:`_timed_out`.
    """
    mine = [p for p in processes if p.role == role]
    live = [p for p in mine if _live(p, now) and p.exiting_at is None]
    new = [p for p in live if p.boot_generation >= target.generation]
    old = [p for p in live if p.boot_generation < target.generation]
    done = bool(new) and not old
    exits = [
        p.exiting_at
        for p in mine
        if p.exiting_at is not None and p.exiting_at >= target.requested_at
    ]
    started = bool(exits) or bool(new)
    return done, started


def _timed_out(
    role: str, target: RestartRow, processes: Sequence[ProcessRow], now: datetime, timeout: int
) -> bool:
    mine = [p for p in processes if p.role == role]
    live = [p for p in mine if _live(p, now) and p.exiting_at is None]
    if any(p.boot_generation >= target.generation for p in live):
        return False
    exits = [
        p.exiting_at
        for p in mine
        if p.exiting_at is not None and p.exiting_at >= target.requested_at
    ]
    if exits and (now - max(exits)).total_seconds() > timeout:
        return True
    return not live and (now - target.requested_at).total_seconds() > timeout


def _fail(role: str) -> Decision:
    return Decision(
        "fail",
        f"{ROLE_LABEL.get(role, role)} ist nach dem Neustart nicht zurückgekommen. "
        "Bitte prüfen Sie die Container; die übrigen Dienste laufen weiter.",
    )


def decide(
    me: ProcessRow,
    target: RestartRow | None,
    processes: Sequence[ProcessRow],
    *,
    now: datetime,
    timeout_seconds: int,
    enabled: bool,
    busy: bool = False,
) -> Decision:
    """What ``me`` does next. Pure: no clock, no database."""
    if not enabled or target is None or target.finished_at or target.failed_message:
        return _STAY
    if me.role not in target.roles:
        return _STAY
    roles = target.roles
    if me.boot_generation >= target.generation:
        # a restarted api process closes the sequence once every role is back
        if me.role != "api":
            return _STAY
        for role in roles:
            if _role_view(role, target, processes, now)[0]:
                continue
            if _timed_out(role, target, processes, now, timeout_seconds):
                return _fail(role)
            return _STAY
        return Decision("finish")
    for role in roles[: roles.index(me.role)]:
        if _role_view(role, target, processes, now)[0]:
            continue
        if _timed_out(role, target, processes, now, timeout_seconds):
            return _fail(role)
        return _STAY
    if busy:
        # a plugin install/uninstall runs in this process; the clock of the later roles
        # only starts when it exits, so waiting costs nothing
        return _STAY
    same = [p for p in processes if p.role == me.role and p.instance_id != me.instance_id]
    window = LIVE_WITHIN_SECONDS + timeout_seconds
    exited = [
        p
        for p in same
        if p.exiting_at is not None
        and p.exiting_at >= target.requested_at
        and (now - p.exiting_at).total_seconds() <= window
    ]
    back = [
        p
        for p in same
        if _live(p, now) and p.exiting_at is None and p.boot_generation >= target.generation
    ]
    if len(exited) > len(back):
        return _STAY
    waiting = sorted(
        [me.instance_id]
        + [
            p.instance_id
            for p in same
            if _live(p, now) and p.exiting_at is None and p.boot_generation < target.generation
        ]
    )
    if waiting[0] != me.instance_id:
        return _STAY
    return Decision("exit")


# ---------------------------------------------------------------- database helpers


def _row(p: TiqoraProcess) -> ProcessRow:
    return ProcessRow(p.instance_id, p.role, p.boot_generation, p.heartbeat_at, p.exiting_at)


def _restart_row(r: TiqoraRestart) -> RestartRow:
    roles = tuple(x for x in r.roles.split(",") if x)
    return RestartRow(r.generation, r.requested_at, roles, r.finished_at, r.failed_message)


async def _insert_process(
    session: AsyncSession,
    *,
    instance_id: str,
    role: str,
    hostname: str,
    pid: int,
    now: datetime,
) -> None:
    generation = (await session.execute(select(func.max(TiqoraRestart.generation)))).scalar()
    session.add(
        TiqoraProcess(
            instance_id=instance_id,
            role=role,
            hostname=hostname,
            pid=pid,
            boot_generation=int(generation or 0),
            started_at=now,
            heartbeat_at=now,
            exiting_at=None,
        )
    )


async def register_process(
    factory: async_sessionmaker[AsyncSession],
    role: ProcessRole,
    *,
    now: datetime,
    hostname: str | None = None,
    pid: int | None = None,
) -> str:
    """Insert this process; ``boot_generation`` is the newest restart generation (0 = none)."""
    host = hostname or socket.gethostname()
    process_id = pid if pid is not None else os.getpid()
    instance_id = f"{role}:{host}:{process_id}:{uuid.uuid4().hex[:8]}"
    async with factory() as session:
        await _insert_process(
            session, instance_id=instance_id, role=role, hostname=host, pid=process_id, now=now
        )
        await session.commit()
    return instance_id


async def heartbeat(
    factory: async_sessionmaker[AsyncSession], instance_id: str, *, now: datetime
) -> bool:
    """Refresh the heartbeat; False when the row is gone (purged during a long stall)."""
    async with factory() as session:
        result = await session.execute(
            update(TiqoraProcess)
            .where(TiqoraProcess.instance_id == instance_id)
            .values(heartbeat_at=now)
        )
        await session.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]


async def unregister_process(factory: async_sessionmaker[AsyncSession], instance_id: str) -> None:
    """Remove the row at clean shutdown. Best effort: errors are logged, not raised."""
    try:
        async with factory() as session:
            await session.execute(
                delete(TiqoraProcess).where(TiqoraProcess.instance_id == instance_id)
            )
            await session.commit()
    except Exception as exc:
        log.warning("plugin_process_unregister_failed", instance_id=instance_id, error=str(exc))


async def purge_dead_processes(session: AsyncSession, *, older_than: datetime) -> int:
    """Delete rows without a heartbeat since ``older_than``; the caller commits."""
    result = await session.execute(
        delete(TiqoraProcess).where(TiqoraProcess.heartbeat_at < older_than)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def request_restart(
    session: AsyncSession, *, by: int | None, reason: str, now: datetime
) -> int:
    """Record a restart request and return its generation. Flushes only.

    ``roles`` is the snapshot of the plugin-hosting roles with a live process now. A
    newer generation supersedes an unfinished older one (it is finished without a
    message), so two quick installs restart once.
    """
    rows = (await session.execute(select(TiqoraProcess))).scalars().all()
    cutoff = now - timedelta(seconds=LIVE_WITHIN_SECONDS)
    alive = {r.role for r in rows if r.heartbeat_at >= cutoff and r.exiting_at is None}
    roles = [r for r in restart_roles() if r in alive]
    await session.execute(
        update(TiqoraRestart)
        .where(TiqoraRestart.finished_at.is_(None), TiqoraRestart.failed_message.is_(None))
        .values(finished_at=now)
    )
    row = TiqoraRestart(
        requested_at=now,
        requested_by=by,
        reason=reason[:200],
        roles=",".join(roles),
        finished_at=None,
        failed_message=None,
    )
    session.add(row)
    await session.flush()
    return int(row.generation)


async def plugin_job_active(session: AsyncSession) -> bool:
    """True while any plugins-lane job is queued or running (restart endpoint -> 409)."""
    n = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraJob)
            .where(
                TiqoraJob.kind.in_(PLUGIN_JOB_KINDS), TiqoraJob.status.in_(("queued", "running"))
            )
        )
    ).scalar()
    return bool(n)


async def _busy_here(session: AsyncSession) -> bool:
    """A plugins-lane job is running in this process's job runner (worker id host:pid)."""
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    n = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraJob)
            .where(
                TiqoraJob.kind.in_(PLUGIN_JOB_KINDS),
                TiqoraJob.status == "running",
                TiqoraJob.worker_id == worker_id,
            )
        )
    ).scalar()
    return bool(n)


async def _latest_restart(session: AsyncSession) -> TiqoraRestart | None:
    return (
        (await session.execute(select(TiqoraRestart).order_by(TiqoraRestart.generation.desc())))
        .scalars()
        .first()
    )


async def restart_watch_loop(
    factory: async_sessionmaker[AsyncSession],
    role: ProcessRole,
    instance_id: str,
    *,
    settings: Settings,
    on_exit: Callable[[], None],
    stop: asyncio.Event | None = None,
    sleep: Callable[[float], Awaitable[object]] | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    """Heartbeat and follow restart requests until this process leaves.

    Any database error is logged and the loop carries on: it never kills the process.
    """
    global _REQUESTED
    hostname = socket.gethostname()
    pid = os.getpid()
    while stop is None or not stop.is_set():
        try:
            now = clock()
            if not await heartbeat(factory, instance_id, now=now):
                async with factory() as session:
                    await _insert_process(
                        session,
                        instance_id=instance_id,
                        role=role,
                        hostname=hostname,
                        pid=pid,
                        now=now,
                    )
                    await session.commit()
            if await _step(factory, role, instance_id, settings, now):
                _REQUESTED = True
                log.info("plugin_restart_exit", role=role, instance_id=instance_id)
                on_exit()
                return
        except Exception as exc:
            log.warning("plugin_restart_loop_error", role=role, error=str(exc))
        if sleep is not None:
            await sleep(HEARTBEAT_SECONDS)
        elif stop is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), HEARTBEAT_SECONDS)
        else:
            await asyncio.sleep(HEARTBEAT_SECONDS)


async def _step(
    factory: async_sessionmaker[AsyncSession],
    role: ProcessRole,
    instance_id: str,
    settings: Settings,
    now: datetime,
) -> bool:
    """One tick; True when this process must exit now."""
    async with factory() as session:
        latest = await _latest_restart(session)
        rows = (await session.execute(select(TiqoraProcess))).scalars().all()
        processes = [_row(r) for r in rows]
        me = next((p for p in processes if p.instance_id == instance_id), None)
        if role == "api" and await purge_dead_processes(session, older_than=now - DEAD_AFTER):
            await session.commit()
        if me is None or latest is None:
            return False
        target = _restart_row(latest)
        busy = await _busy_here(session) if settings.plugins_coordinated_restart else False
        decision = decide(
            me,
            target,
            processes,
            now=now,
            timeout_seconds=settings.plugins_restart_timeout_seconds,
            enabled=settings.plugins_coordinated_restart,
            busy=busy,
        )
        if decision.action == "stay":
            return False
        if decision.action == "exit":
            await session.execute(
                update(TiqoraProcess)
                .where(TiqoraProcess.instance_id == instance_id)
                .values(exiting_at=now)
            )
            await session.commit()
            return True
        open_ = (TiqoraRestart.finished_at.is_(None), TiqoraRestart.failed_message.is_(None))
        if decision.action == "fail":
            await session.execute(
                update(TiqoraRestart)
                .where(TiqoraRestart.generation == target.generation, *open_)
                .values(failed_message=decision.message)
            )
            log.warning(
                "plugin_restart_failed", generation=target.generation, message=decision.message
            )
        else:
            await session.execute(
                update(TiqoraRestart)
                .where(TiqoraRestart.generation == target.generation, *open_)
                .values(finished_at=now)
            )
            log.info("plugin_restart_finished", generation=target.generation)
        await session.commit()
    return False


# ---------------------------------------------------------------- status for the admin UI


class RestartRoleOut(BaseModel):
    role: Literal["api", "worker", "mcp", "ai-worker"]
    old: int
    new: int
    state: Literal["waiting", "restarting", "done", "timeout"]


class RestartStatusOut(BaseModel):
    generation: int
    reason: str
    requested_at: UtcDateTime
    finished_at: UtcDateTime | None
    failed_message: str | None
    coordinated: bool
    roles: list[RestartRoleOut]


async def restart_status(
    session: AsyncSession,
    *,
    now: datetime,
    coordinated: bool = True,
    timeout_seconds: int = 180,
) -> RestartStatusOut | None:
    """State of the latest restart request, or None when there never was one."""
    latest = await _latest_restart(session)
    if latest is None:
        return None
    target = _restart_row(latest)
    rows = (await session.execute(select(TiqoraProcess))).scalars().all()
    processes = [_row(r) for r in rows]
    out: list[RestartRoleOut] = []
    for role in target.roles:
        if role not in RESTART_ORDER:
            continue
        mine = [p for p in processes if p.role == role and _live(p, now) and p.exiting_at is None]
        old = sum(1 for p in mine if p.boot_generation < target.generation)
        new = len(mine) - old
        done, started = _role_view(role, target, processes, now)
        if done:
            role_state: Literal["waiting", "restarting", "done", "timeout"] = "done"
        elif _timed_out(role, target, processes, now, timeout_seconds):
            role_state = "timeout"
        elif started:
            role_state = "restarting"
        else:
            role_state = "waiting"
        out.append(RestartRoleOut(role=role, old=old, new=new, state=role_state))
    return RestartStatusOut(
        generation=target.generation,
        reason=latest.reason,
        requested_at=latest.requested_at,
        finished_at=latest.finished_at,
        failed_message=latest.failed_message,
        coordinated=coordinated,
        roles=out,
    )
