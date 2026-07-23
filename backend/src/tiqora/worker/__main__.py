"""Worker process: lightweight asyncio loops (no external scheduler)."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import get_setting_int
from tiqora.logging_setup import configure_logging
from tiqora.worker.ai_audit_cleanup import run_ai_audit_cleanup_tick
from tiqora.worker.escalation import run_escalation_tick
from tiqora.worker.gdpr_erasure_purge import run_gdpr_erasure_purge_tick
from tiqora.worker.gdpr_retention import run_gdpr_retention_tick
from tiqora.worker.generic_agent import run_generic_agent_tick
from tiqora.worker.notifications import run_notifications_tick
from tiqora.worker.outbox_drain import drain_outbox
from tiqora.worker.poller import poll_once
from tiqora.worker.postmaster import run_postmaster_tick
from tiqora.worker.status import record_tick_status, seconds_until_daily

logger = structlog.get_logger(__name__)

#: Heartbeat file the worker touches every cycle. The container has no HTTP
#: port, so the Docker healthcheck reads this file's freshness to tell the
#: asyncio event loop is still alive (see docker-compose healthcheck).
_HEARTBEAT_FILE = os.environ.get("TIQORA_WORKER_HEARTBEAT_FILE", "/tmp/tiqora-worker.heartbeat")
_HEARTBEAT_INTERVAL_SECONDS = 15

Tick = Callable[[], Awaitable[dict[str, Any]]]


def _write_heartbeat() -> None:
    with open(_HEARTBEAT_FILE, "w") as fh:
        fh.write(str(int(time.time())))


async def _heartbeat_loop(stop: asyncio.Event) -> None:
    """Touch the heartbeat file on a fixed cadence so the container healthcheck
    can distinguish a live event loop from a hung/deadlocked one."""
    logger.info(
        "heartbeat_loop_started",
        path=_HEARTBEAT_FILE,
        interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(_write_heartbeat)
        except OSError:
            logger.exception("heartbeat_write_error", path=_HEARTBEAT_FILE)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            continue
    logger.info("heartbeat_loop_stopped")


async def _effective_interval(
    factory: async_sessionmaker[AsyncSession], interval_key: str | None, interval_default: int
) -> int:
    """DB override (``interval_key``) or the config default, clamped to >=5s.

    ``interval_key=None`` (the poller, which has no admin-editable override)
    skips the DB round-trip entirely.
    """
    if interval_key is None:
        return max(5, interval_default)
    async with factory() as session:
        value = await get_setting_int(session, interval_key, interval_default)
    return max(5, value)


async def _interval_loop(
    name: str,
    tick: Tick,
    interval_default: int,
    interval_key: str | None,
    stop: asyncio.Event,
) -> None:
    """Generic fixed-cadence tick loop, replacing the former per-service
    copy-paste loops. Records ``daemon.<name>.status.*`` after every tick
    (see ``tiqora.worker.status``) and survives any tick exception — one
    broken cycle must never take the whole worker process down.
    """
    factory = get_session_factory()
    logger.info(f"{name}_loop_started", interval_default_seconds=interval_default)
    interval = max(5, interval_default)
    while not stop.is_set():
        try:
            interval = await _effective_interval(factory, interval_key, interval_default)
            result = await tick()
            await record_tick_status(name, ok=True, result=result, session_factory=factory)
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            logger.exception(f"{name}_loop_error")
            await record_tick_status(name, ok=False, error=str(exc), session_factory=factory)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info(f"{name}_loop_stopped")


async def _daily_loop(name: str, tick: Tick, at_hhmm: str, stop: asyncio.Event) -> None:
    """Generic once-daily tick loop (UTC ``HH:MM``). Sleep is interruptible by
    ``stop`` and recomputed from scratch after every wake-up via
    ``seconds_until_daily`` — no drift bookkeeping needed."""
    factory = get_session_factory()
    logger.info(f"{name}_loop_started", at_utc=at_hhmm)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds_until_daily(at_hhmm))
            break  # stop was set while sleeping
        except TimeoutError:
            pass
        try:
            result = await tick()
            await record_tick_status(name, ok=True, result=result, session_factory=factory)
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            logger.exception(f"{name}_loop_error")
            await record_tick_status(name, ok=False, error=str(exc), session_factory=factory)
    logger.info(f"{name}_loop_stopped")


async def _run_all_loops(stop: asyncio.Event) -> None:
    settings = get_settings()

    async def poller_tick() -> dict[str, int]:
        return await poll_once(settings=settings)

    async def outbox_tick() -> dict[str, int]:
        return await drain_outbox(settings=settings)

    async def postmaster_tick() -> dict[str, int]:
        return await run_postmaster_tick(settings=settings)

    async def escalation_tick() -> dict[str, int]:
        return await run_escalation_tick(settings=settings)

    async def notifications_tick() -> dict[str, int]:
        return await run_notifications_tick(settings=settings)

    async def generic_agent_tick() -> dict[str, int]:
        return await run_generic_agent_tick(settings=settings)

    await asyncio.gather(
        _heartbeat_loop(stop),
        _interval_loop("poller", poller_tick, settings.poller_interval_seconds, None, stop),
        _interval_loop(
            "outbox",
            outbox_tick,
            settings.outbox_drain_interval_seconds,
            "daemon.outbox.interval_seconds",
            stop,
        ),
        _interval_loop(
            "postmaster",
            postmaster_tick,
            settings.postmaster_interval_seconds,
            "daemon.postmaster.interval_seconds",
            stop,
        ),
        _interval_loop(
            "escalation",
            escalation_tick,
            settings.escalation_interval_seconds,
            "daemon.escalation.interval_seconds",
            stop,
        ),
        _interval_loop(
            "notifications",
            notifications_tick,
            settings.notifications_interval_seconds,
            "daemon.notifications.interval_seconds",
            stop,
        ),
        _interval_loop(
            "generic_agent",
            generic_agent_tick,
            settings.generic_agent_interval_seconds,
            "daemon.generic_agent.interval_seconds",
            stop,
        ),
        _daily_loop("gdpr_retention", run_gdpr_retention_tick, "03:00", stop),
        _daily_loop("gdpr_erasure_purge", run_gdpr_erasure_purge_tick, "03:30", stop),
        _daily_loop("ai_audit_cleanup", run_ai_audit_cleanup_tick, "04:00", stop),
    )


def run_worker() -> None:
    """Start the background worker (poller, daemon takeover loops, heartbeat)."""
    settings = get_settings()
    configure_logging(settings)
    stop = asyncio.Event()

    def _handle_signal(*_args: object) -> None:
        stop.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())

    logger.info("tiqora_worker_starting", redis_url=settings.redis_url)
    try:
        loop.run_until_complete(_run_all_loops(stop))
    finally:
        loop.close()
        logger.info("tiqora_worker_stopped")


if __name__ == "__main__":
    run_worker()
