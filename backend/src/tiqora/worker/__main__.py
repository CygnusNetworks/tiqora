"""Worker process: taskiq consumer + lightweight poller loop."""

from __future__ import annotations

import asyncio
import os
import signal
import time

import structlog

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging
from tiqora.worker.escalation import run_escalation_tick
from tiqora.worker.generic_agent import run_generic_agent_tick
from tiqora.worker.notifications import run_notifications_tick
from tiqora.worker.poller import poll_once
from tiqora.worker.postmaster import run_postmaster_tick

logger = structlog.get_logger(__name__)

#: Heartbeat file the worker touches every cycle. The container has no HTTP
#: port, so the Docker healthcheck reads this file's freshness to tell the
#: asyncio event loop is still alive (see docker-compose healthcheck).
_HEARTBEAT_FILE = os.environ.get("TIQORA_WORKER_HEARTBEAT_FILE", "/tmp/tiqora-worker.heartbeat")
_HEARTBEAT_INTERVAL_SECONDS = 15


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


async def _poller_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(5, settings.poller_interval_seconds)
    logger.info("poller_loop_started", interval_seconds=interval)
    while not stop.is_set():
        try:
            await poll_once(settings=settings)
        except Exception:  # noqa: BLE001 — keep loop alive
            logger.exception("poller_loop_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("poller_loop_stopped")


async def _postmaster_loop(stop: asyncio.Event) -> None:
    """Postmaster tick loop. A no-op unless daemon.postmaster.enabled=1 (see
    tiqora.domain.settings_store) — the tick itself checks the flag every
    cycle so it can be toggled at runtime without a worker restart."""
    settings = get_settings()
    interval = max(5, settings.postmaster_interval_seconds)
    logger.info("postmaster_loop_started", interval_seconds=interval)
    while not stop.is_set():
        try:
            await run_postmaster_tick(settings=settings)
        except Exception:  # noqa: BLE001 — keep loop alive
            logger.exception("postmaster_loop_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("postmaster_loop_stopped")


async def _escalation_loop(stop: asyncio.Event) -> None:
    """Escalation sweep tick loop. A no-op unless daemon.escalation.enabled=1 (see
    tiqora.domain.settings_store) — the tick itself checks the flag every cycle
    so it can be toggled at runtime without a worker restart."""
    settings = get_settings()
    interval = max(5, settings.escalation_interval_seconds)
    logger.info("escalation_loop_started", interval_seconds=interval)
    while not stop.is_set():
        try:
            await run_escalation_tick(settings=settings)
        except Exception:  # noqa: BLE001 — keep loop alive
            logger.exception("escalation_loop_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("escalation_loop_stopped")


async def _notifications_loop(stop: asyncio.Event) -> None:
    """Notification engine tick loop. A no-op unless daemon.notifications.enabled=1
    (see tiqora.domain.settings_store) — the tick itself checks the flag every
    cycle so it can be toggled at runtime without a worker restart."""
    settings = get_settings()
    interval = max(5, settings.notifications_interval_seconds)
    logger.info("notifications_loop_started", interval_seconds=interval)
    while not stop.is_set():
        try:
            await run_notifications_tick(settings=settings)
        except Exception:  # noqa: BLE001 — keep loop alive
            logger.exception("notifications_loop_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("notifications_loop_stopped")


async def _generic_agent_loop(stop: asyncio.Event) -> None:
    """GenericAgent tick loop. A no-op unless daemon.generic_agent.enabled=1
    (see tiqora.domain.settings_store) — the tick itself checks the flag every
    cycle so it can be toggled at runtime without a worker restart. Evaluated
    every minute (Znuny's own GenericAgent daemon task cron granularity)."""
    settings = get_settings()
    interval = max(5, settings.generic_agent_interval_seconds)
    logger.info("generic_agent_loop_started", interval_seconds=interval)
    while not stop.is_set():
        try:
            await run_generic_agent_tick(settings=settings)
        except Exception:  # noqa: BLE001 — keep loop alive
            logger.exception("generic_agent_loop_error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("generic_agent_loop_stopped")


async def _run_all_loops(stop: asyncio.Event) -> None:
    await asyncio.gather(
        _heartbeat_loop(stop),
        _poller_loop(stop),
        _postmaster_loop(stop),
        _escalation_loop(stop),
        _notifications_loop(stop),
        _generic_agent_loop(stop),
    )


def run_worker() -> None:
    """Start the background worker (poller + postmaster loops; taskiq CLI is optional)."""
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
