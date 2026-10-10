"""Per-process fan-out of chat wake-ups: one ``PSUBSCRIBE tiqora:chat:*`` for all SSE streams.

Streams (visitor and agent) subscribe by session id and receive small dict items on a bounded
queue. Caps (R-SSE-CAPS): per IP hash ``MAX_PER_IP`` concurrent streams, per session
``max_per_session`` (the OLDEST stream of that session is evicted with a close item instead of
refusing the new one), a process total (``chat_max_streams``, clamped to the file-descriptor
limit by :func:`effective_max_streams`) and a maximum stream lifetime.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import resource
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from fastapi import Request

from tiqora.api.deps import AppSettings, RedisDep

logger = structlog.get_logger(__name__)

CHAT_CHANNEL_PREFIX: Final = "tiqora:chat:"
QUEUE_SIZE: Final = 50
MAX_PER_IP: Final = 6
SWEEP_INTERVAL: Final = 1.0
MAX_STREAM_SECONDS: Final = 30 * 60
_FD_RESERVE: Final = 256
_MIN_STREAMS: Final = 10
CLOSE_ITEM: Final = {"type": "close"}


def chat_channel(session_id: str) -> str:
    return f"{CHAT_CHANNEL_PREFIX}{session_id}"


async def _publish(client: Any, session_id: str, payload: dict[str, Any]) -> None:
    try:
        await client.publish(chat_channel(session_id), json.dumps(payload))
    except Exception:  # noqa: BLE001 - best effort; streams re-read the DB on every heartbeat
        logger.warning("chat_publish_failed", payload_type=payload.get("type"))


async def publish_chat_wake(client: Any, session_id: str) -> None:
    await _publish(client, session_id, {"type": "wake"})


async def publish_chat_typing(client: Any, session_id: str) -> None:
    await _publish(client, session_id, {"type": "typing", "who": "agent"})


def effective_max_streams(configured: int, soft_nofile: int | None = None) -> int:
    """Clamp the configured cap to ``RLIMIT_NOFILE soft - 256`` (never below 10)."""
    if soft_nofile is None:
        soft_nofile = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if soft_nofile == resource.RLIM_INFINITY or soft_nofile < 0:
        return configured
    return max(_MIN_STREAMS, min(configured, soft_nofile - _FD_RESERVE))


class StreamLimitReached(Exception):
    """``reason`` is ``"total"`` or ``"ip"``; the API answers 503 + Retry-After."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"stream limit reached: {reason}")
        self.reason = reason


def _queue_items(queue: asyncio.Queue[dict[str, Any]]) -> Any:
    # asyncio.Queue keeps its items in a deque; coalescing needs to look inside it.
    return queue._queue  # type: ignore[attr-defined]


def _force_close(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Replace everything queued by the close item (the client reconnects and replays)."""
    _queue_items(queue).clear()
    queue.put_nowait(dict(CLOSE_ITEM))


@dataclass(eq=False)
class _Stream:
    session_id: str
    ip_hash: str | None
    queue: asyncio.Queue[dict[str, Any]]
    expires_at: float = field(default=0.0)


class ChatHub:
    def __init__(
        self,
        client: Any,
        *,
        max_streams: int,
        max_per_session: int = 3,
        max_per_ip: int = MAX_PER_IP,
        max_lifetime: float = MAX_STREAM_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._max_streams = max_streams
        self._max_per_session = max_per_session
        self._max_per_ip = max_per_ip
        self._max_lifetime = max_lifetime
        self._clock = clock
        self._streams: dict[str, list[_Stream]] = {}
        self._ip_counts: dict[str, int] = {}
        self._total = 0
        self._pubsub: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._sweeper: asyncio.Task[None] | None = None

    @property
    def active_streams(self) -> int:
        return self._total

    # -- registry --------------------------------------------------------------------------
    def _remove(self, stream: _Stream) -> bool:
        streams = self._streams.get(stream.session_id)
        if not streams or stream not in streams:
            return False
        streams.remove(stream)
        if not streams:
            del self._streams[stream.session_id]
        self._total -= 1
        if stream.ip_hash is not None:
            left = self._ip_counts.get(stream.ip_hash, 1) - 1
            if left > 0:
                self._ip_counts[stream.ip_hash] = left
            else:
                self._ip_counts.pop(stream.ip_hash, None)
        return True

    def _offer(self, stream: _Stream, item: dict[str, Any]) -> None:
        """Queue ``item`` without ever dropping a ``wake``/``close``.

        Typing items are coalesced (only the newest stays) and are what gets dropped when the
        queue is full; if only wakes are left the stream is closed instead, so a visitor
        reconnects and replays rather than silently missing a message.
        """
        queue = stream.queue
        items = _queue_items(queue)
        if item.get("type") == "typing":
            for old in [i for i in items if i.get("type") == "typing"]:
                items.remove(old)
        elif queue.full():
            for old in items:
                if old.get("type") == "typing":
                    items.remove(old)
                    break
        if queue.full():
            if item.get("type") == "typing":
                return
            self._remove(stream)
            _force_close(queue)
            return
        queue.put_nowait(item)

    def _close_stream(self, stream: _Stream) -> None:
        self._remove(stream)
        _force_close(stream.queue)

    async def subscribe(
        self, session_id: str, *, ip_hash: str | None = None
    ) -> asyncio.Queue[dict[str, Any]]:
        if self._closed:
            raise StreamLimitReached("closed")
        existing = self._streams.get(session_id, [])
        victim = existing[0] if len(existing) >= self._max_per_session else None
        total = self._total - (1 if victim else 0)
        if total >= self._max_streams:
            raise StreamLimitReached("total")
        if ip_hash is not None:
            ip_count = self._ip_counts.get(ip_hash, 0)
            if victim is not None and victim.ip_hash == ip_hash:
                ip_count -= 1
            if ip_count >= self._max_per_ip:
                raise StreamLimitReached("ip")
        if victim is not None:
            self._close_stream(victim)
        await self._ensure_reader()
        stream = _Stream(
            session_id=session_id,
            ip_hash=ip_hash,
            queue=asyncio.Queue(maxsize=QUEUE_SIZE),
            expires_at=self._clock() + self._max_lifetime,
        )
        self._streams.setdefault(session_id, []).append(stream)
        self._total += 1
        if ip_hash is not None:
            self._ip_counts[ip_hash] = self._ip_counts.get(ip_hash, 0) + 1
        return stream.queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        for stream in list(self._streams.get(session_id, [])):
            if stream.queue is queue:
                self._remove(stream)

    # -- reader ----------------------------------------------------------------------------
    async def _open_pubsub(self) -> Any:
        pubsub = self._client.pubsub()
        await pubsub.psubscribe(f"{CHAT_CHANNEL_PREFIX}*")
        return pubsub

    async def _ensure_reader(self) -> None:
        if self._reader is not None and not self._reader.done():
            return
        async with self._start_lock:
            if self._reader is not None and not self._reader.done():
                return
            try:
                self._pubsub = await self._open_pubsub()
            except Exception:  # noqa: BLE001 - the reader loop retries
                logger.warning("chat_hub_subscribe_failed", exc_info=True)
                self._pubsub = None
            self._reader = asyncio.create_task(self._run(), name="chat-hub-reader")
            if self._sweeper is None or self._sweeper.done():
                self._sweeper = asyncio.create_task(self._sweep_loop(), name="chat-hub-sweeper")

    def _expire_streams(self) -> None:
        now = self._clock()
        for streams in list(self._streams.values()):
            for stream in list(streams):
                if stream.expires_at <= now:
                    self._close_stream(stream)

    async def _sweep_loop(self) -> None:
        """Lifetime enforcement on its own timer, independent of Redis traffic or outages."""
        while not self._closed:
            self._expire_streams()
            await asyncio.sleep(SWEEP_INTERVAL)

    def _route(self, message: dict[str, Any]) -> None:
        if message.get("type") != "pmessage":
            return
        channel = message.get("channel")
        if isinstance(channel, bytes):
            channel = channel.decode()
        if not isinstance(channel, str) or not channel.startswith(CHAT_CHANNEL_PREFIX):
            return
        streams = self._streams.get(channel[len(CHAT_CHANNEL_PREFIX) :])
        if not streams:
            return
        try:
            payload = json.loads(message.get("data") or "")
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        for stream in list(streams):
            self._offer(stream, dict(payload))

    async def _run(self) -> None:
        while not self._closed:
            try:
                if self._pubsub is None:
                    self._pubsub = await self._open_pubsub()
                while not self._closed:
                    message = await self._pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is not None:
                        self._route(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - restart after a pause
                logger.warning("chat_hub_reader_failed", exc_info=True)
                await self._drop_pubsub()
                await asyncio.sleep(1.0)

    async def _drop_pubsub(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def aclose(self) -> None:
        self._closed = True
        for task in (self._reader, self._sweeper):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reader = self._sweeper = None
        await self._drop_pubsub()
        for streams in list(self._streams.values()):
            for stream in list(streams):
                self._close_stream(stream)


async def get_chat_hub(request: Request, redis_client: RedisDep, settings: AppSettings) -> ChatHub:
    """The process-wide hub, created lazily on ``app.state`` (tests override this dependency)."""
    hub = getattr(request.app.state, "chat_hub", None)
    if hub is None:
        hub = ChatHub(
            redis_client,
            max_streams=effective_max_streams(settings.chat_max_streams),
        )
        request.app.state.chat_hub = hub
    return hub
