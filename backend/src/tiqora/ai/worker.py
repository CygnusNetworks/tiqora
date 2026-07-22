"""``tiqora-ai-worker`` — the AI subsystem's own process (plan §3.0/§3.4).

Deliberately **not** a loop inside ``tiqora.worker`` (the main takeover
worker): a hung/slow LLM call must never affect the poller, outbox drain, or
any other core daemon, and the AI worker needs to be independently
stoppable as a cost/incident kill switch.

Phase A shipped only the loop skeleton (Readiness-Gate check, tick-status
recording, sleep). Phase D adds the actual work: draining
``tiqora_event_outbox`` for auto-reply-relevant events and running the
queue-threshold auto-summary scan — see :mod:`tiqora.ai.auto_worker` for the
tick logic itself; this module stays the thin process/loop shell.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from tiqora.ai.auto_worker import run_auto_tick
from tiqora.ai.gate import is_tiqora_primary
from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_AI_WORKER_ENABLED,
    KEY_AI_WORKER_INTERVAL_SECONDS,
    get_setting_bool,
    get_setting_int,
)
from tiqora.logging_setup import configure_logging
from tiqora.worker.status import record_tick_status

logger = structlog.get_logger(__name__)

_HEARTBEAT_FILE = os.environ.get(
    "TIQORA_AI_WORKER_HEARTBEAT_FILE",
    "/tmp/tiqora-ai-worker.heartbeat",  # noqa: S108
)
_HEARTBEAT_INTERVAL_SECONDS = 15
_SERVICE_SLUG = "ai_worker"


def _write_heartbeat() -> None:
    with open(_HEARTBEAT_FILE, "w") as fh:
        fh.write(str(int(time.time())))


async def _heartbeat_loop(stop: asyncio.Event) -> None:
    logger.info(
        "ai_worker_heartbeat_loop_started",
        path=_HEARTBEAT_FILE,
        interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(_write_heartbeat)
        except OSError:
            logger.exception("ai_worker_heartbeat_write_error", path=_HEARTBEAT_FILE)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            continue
    logger.info("ai_worker_heartbeat_loop_stopped")


async def _ai_tick(factory: async_sessionmaker[Any]) -> dict[str, Any]:
    """One tick: honour ``daemon.ai_worker.enabled``, re-check the
    Readiness-Gate, and (gate open) run the auto-reply/auto-summary tick."""
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_AI_WORKER_ENABLED, False)
        if not enabled:
            return {"enabled": False}
        gate_open = await is_tiqora_primary(session)
    if not gate_open:
        logger.info("ai_worker_gate_closed")
        return {"enabled": True, "gate_open": False}
    result = await run_auto_tick(session_factory=factory)
    return {"enabled": True, "gate_open": True, **result}


async def _run_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    factory = get_session_factory()
    default_interval = settings.ai_worker_interval_seconds
    logger.info("ai_worker_tick_loop_started", interval_default_seconds=default_interval)

    while not stop.is_set():
        try:
            async with factory() as session:
                interval = await get_setting_int(
                    session, KEY_AI_WORKER_INTERVAL_SECONDS, default_interval
                )
            interval = max(5, interval)
            result = await _ai_tick(factory)
            await record_tick_status(_SERVICE_SLUG, ok=True, result=result, session_factory=factory)
        except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
            logger.exception("ai_worker_tick_error")
            await record_tick_status(
                _SERVICE_SLUG, ok=False, error=str(exc), session_factory=factory
            )
            interval = max(5, default_interval)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("ai_worker_tick_loop_stopped")


async def _run_all(stop: asyncio.Event) -> None:
    await asyncio.gather(_heartbeat_loop(stop), _run_loop(stop))


def run_ai_worker() -> None:
    """Start the AI worker process (Readiness-Gate tick + heartbeat only, Phase A)."""
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

    logger.info("tiqora_ai_worker_starting")
    try:
        loop.run_until_complete(_run_all(stop))
    finally:
        loop.close()
        logger.info("tiqora_ai_worker_stopped")


if __name__ == "__main__":
    run_ai_worker()
