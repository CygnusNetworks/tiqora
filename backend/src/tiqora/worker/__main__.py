"""Worker process: taskiq consumer + lightweight poller loop."""

from __future__ import annotations

import asyncio
import signal

import structlog

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging
from tiqora.worker.poller import poll_once

logger = structlog.get_logger(__name__)


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


def run_worker() -> None:
    """Start the background worker (poller loop; taskiq CLI is optional)."""
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
        loop.run_until_complete(_poller_loop(stop))
    finally:
        loop.close()
        logger.info("tiqora_worker_stopped")


if __name__ == "__main__":
    run_worker()
