"""The AI worker's web-chat assistant loop.

Visitor lines are announced on a Redis list (``BOT_QUEUE_KEY``, see
:mod:`tiqora.webchat.notify`). This loop pops the session ids, and every
``BOT_RESCAN_SECONDS`` it also scans the database for ``bot`` chats with an unanswered visitor
line (a lost push, a Redis outage, a claim left behind by a restarted worker). Each turn is
:func:`tiqora.webchat.bot.run_chat_bot_turn` in its own task, at most
``chat_bot_concurrency`` at a time.

Everything is gated by ``daemon.ai_worker.enabled`` and the web chat master switch
``channel.webchat.enabled``: while either is off the loop idles quietly (no Redis, no database
scan) and does not refresh ``BOT_ALIVE_KEY``, so the API reports the assistant as
``worker_down`` and the visitor goes straight to the team.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import channel_enabled
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraChatSession
from tiqora.domain.settings_store import KEY_AI_WORKER_ENABLED, get_setting_bool
from tiqora.jobs.service import utc_now
from tiqora.webchat.bot import (
    BOT_ALIVE_KEY,
    BOT_CLAIM_STALE_SECONDS,
    make_claim_owner,
    run_chat_bot_turn,
)
from tiqora.webchat.notify import BOT_QUEUE_KEY

logger = structlog.get_logger(__name__)

BOT_ALIVE_REFRESH_SECONDS: Final = 10
BOT_ALIVE_TTL_SECONDS: Final = 30
BOT_RESCAN_SECONDS: Final = 15
BOT_BLPOP_TIMEOUT: Final = 5
BOT_BLPOP_GRACE_SECONDS: Final = 2.0  # a half-open connection must not stall the rescan
BOT_RETRY_DELAY_SECONDS: Final = 5
BOT_ENABLED_CHECK_SECONDS: Final = 5
BOT_REDIS_ERROR_SLEEP_SECONDS: Final = 1.0
BOT_DISABLED_SLEEP_SECONDS: Final = 2.0
BOT_STOP_GRACE_SECONDS: Final = 5.0
# Identical failures are logged once, then at most this often (the ai-worker retries every few
# seconds; an unreachable Redis or a missing table must not flood the log).
BOT_WARN_REPEAT_SECONDS: Final = 600.0
# redis-py's default ``socket_timeout`` (5 s) equals the BLPOP timeout, so every idle poll used to
# end in a ``TimeoutError``; the socket must outlive the server-side wait.
BOT_REDIS_SOCKET_TIMEOUT: Final = BOT_BLPOP_TIMEOUT + BOT_BLPOP_GRACE_SECONDS + 1.0


def make_bot_redis(settings: Settings) -> Any:
    """A dedicated Redis client for the loop (BLPOP blocks, so it must not share the default
    socket timeout of the pub/sub singleton)."""
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=BOT_REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=BOT_REDIS_SOCKET_TIMEOUT,
        health_check_interval=30,
    )


async def rescan_pending_sessions(
    factory: async_sessionmaker[AsyncSession], *, now: datetime, limit: int = 100
) -> list[str]:
    """Ids of ``bot`` chats with an unanswered visitor line and no fresh claim."""
    stale = now - timedelta(seconds=BOT_CLAIM_STALE_SECONDS)
    async with factory() as db:
        rows = await db.execute(
            select(TiqoraChatSession.id)
            .where(
                TiqoraChatSession.status == "bot",
                TiqoraChatSession.last_visitor_seq > TiqoraChatSession.bot_answered_seq,
                (TiqoraChatSession.bot_claimed_at.is_(None))
                | (TiqoraChatSession.bot_claimed_at < stale),
            )
            .order_by(TiqoraChatSession.created)
            .limit(limit)
        )
        return [str(sid) for sid in rows.scalars().all()]


def _decode_pop(popped: Any) -> str | None:
    """``BLPOP`` answers ``(key, value)`` (bytes or str) or ``None`` on timeout."""
    if not popped:
        return None
    value = popped[1]
    return value.decode() if isinstance(value, bytes) else str(value)


class _BotLoop:
    def __init__(
        self,
        stop: asyncio.Event,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        redis_client: Any,
        clock: Callable[[], float],
    ) -> None:
        self.stop = stop
        self.settings = settings
        self.factory = factory
        self.redis = redis_client
        self.clock = clock
        self.todo: asyncio.Queue[str] = asyncio.Queue()
        self.known: set[str] = set()  # queued, not yet running; a duplicate offer is dropped
        self.active: set[str] = set()  # running right now
        # Offered while its turn was running (the turn's own requeue for a follow-up line, see
        # bot._release_claim): run once more after the turn, never drop it.
        self.rerun: set[str] = set()
        self.retry_at: dict[str, float] = {}
        self.running: set[asyncio.Task[None]] = set()
        self.sem = asyncio.Semaphore(settings.chat_bot_concurrency)
        self._enabled = False
        self._enabled_checked = -math.inf
        self._warned: dict[tuple[str, str], float] = {}

    def warn(self, event: str, exc: BaseException) -> None:
        """Log a swallowed failure with its exception, once per distinct error per 10 minutes."""
        signature = (event, f"{type(exc).__name__}:{exc}")
        now = self.clock()
        last = self._warned.get(signature)
        if last is not None and now - last < BOT_WARN_REPEAT_SECONDS:
            return
        self._warned[signature] = now
        logger.warning(event, error=f"{type(exc).__name__}: {exc}", exc_info=exc)

    # -- gate ------------------------------------------------------------------------------
    async def enabled(self) -> bool:
        now = self.clock()
        if now - self._enabled_checked >= BOT_ENABLED_CHECK_SECONDS:
            try:
                async with self.factory() as db:
                    self._enabled = await get_setting_bool(
                        db, KEY_AI_WORKER_ENABLED, False
                    ) and await channel_enabled(db, "webchat")
                self._enabled_checked = now
            except Exception as exc:  # noqa: BLE001 - keep the last known value on a database blip
                self.warn("webchat_bot_enabled_check_failed", exc)
        return self._enabled

    # -- producers -------------------------------------------------------------------------
    def offer(self, session_id: str) -> None:
        if session_id in self.active:
            self.rerun.add(session_id)
            return
        if session_id in self.known or session_id in self.retry_at:
            return
        self.known.add(session_id)
        self.todo.put_nowait(session_id)

    async def alive_loop(self) -> None:
        while True:
            try:
                if await self.enabled():
                    await self.redis.set(BOT_ALIVE_KEY, "1", ex=BOT_ALIVE_TTL_SECONDS)
            except Exception as exc:  # noqa: BLE001 - the key expires; API then says worker_down
                self.warn("webchat_bot_alive_refresh_failed", exc)
            await asyncio.sleep(BOT_ALIVE_REFRESH_SECONDS)

    async def feeder(self) -> None:
        last_rescan = -math.inf
        while True:
            if not await self.enabled():
                await asyncio.sleep(BOT_DISABLED_SLEEP_SECONDS)
                continue
            now = self.clock()
            for session_id, due in list(self.retry_at.items()):
                if due <= now:
                    del self.retry_at[session_id]
                    self.offer(session_id)
            if now - last_rescan >= BOT_RESCAN_SECONDS:
                last_rescan = now
                try:
                    for session_id in await rescan_pending_sessions(self.factory, now=utc_now()):
                        self.offer(session_id)
                except Exception as exc:  # noqa: BLE001 - try again at the next rescan
                    self.warn("webchat_bot_rescan_failed", exc)
            try:
                popped_id = _decode_pop(
                    await asyncio.wait_for(
                        self.redis.blpop(BOT_QUEUE_KEY, timeout=BOT_BLPOP_TIMEOUT),
                        timeout=BOT_BLPOP_TIMEOUT + BOT_BLPOP_GRACE_SECONDS,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - Redis down: the database rescan carries on
                self.warn("webchat_bot_queue_pop_failed", exc)
                last_rescan = -math.inf
                await asyncio.sleep(BOT_REDIS_ERROR_SLEEP_SECONDS)
                continue
            if popped_id:
                self.offer(popped_id)

    # -- consumer --------------------------------------------------------------------------
    async def dispatcher(self) -> None:
        while True:
            session_id = await self.todo.get()
            await self.sem.acquire()
            self.known.discard(session_id)
            self.active.add(session_id)
            task = asyncio.create_task(self._turn(session_id))
            self.running.add(task)
            task.add_done_callback(self.running.discard)

    async def _turn(self, session_id: str) -> None:
        outcome = "retry"
        try:
            outcome = await run_chat_bot_turn(
                self.factory,
                self.settings,
                self.redis,
                session_id=session_id,
                owner=make_claim_owner(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad turn must not stop the loop
            logger.exception("webchat_bot_turn_crashed", session_id=session_id)
        finally:
            self.active.discard(session_id)
            self.sem.release()
        rerun = session_id in self.rerun
        self.rerun.discard(session_id)
        if outcome == "retry":
            self.retry_at[session_id] = self.clock() + BOT_RETRY_DELAY_SECONDS
        elif rerun:
            self.offer(session_id)  # claim_bot_turn answers "skipped" if nothing is pending


async def run_chat_bot_loop(
    stop: asyncio.Event,
    *,
    settings: Settings | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Run the assistant until *stop* is set; waits up to 5 s for running turns."""
    cfg = settings or get_settings()
    own_redis = redis_client is None
    loop = _BotLoop(
        stop,
        cfg,
        factory or get_session_factory(),
        make_bot_redis(cfg) if own_redis else redis_client,
        clock,
    )
    logger.info("webchat_bot_loop_started", concurrency=cfg.chat_bot_concurrency)
    background = [
        asyncio.create_task(loop.alive_loop()),
        asyncio.create_task(loop.feeder()),
        asyncio.create_task(loop.dispatcher()),
    ]
    try:
        await stop.wait()
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if loop.running:
            _, pending = await asyncio.wait(set(loop.running), timeout=BOT_STOP_GRACE_SECONDS)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        with contextlib.suppress(Exception):
            # Single ai-worker assumed: with replicas this would flash worker_down for <=10 s.
            await loop.redis.delete(BOT_ALIVE_KEY)
        if own_redis:
            with contextlib.suppress(Exception):
                await loop.redis.aclose()
        logger.info("webchat_bot_loop_stopped")
