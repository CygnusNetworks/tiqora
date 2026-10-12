"""Visitor event stream (C7): replay from the message log, then live events over SSE.

``GET /api/chat/v1/sessions/{sid}/stream`` (endpoint in :mod:`tiqora.webchat.public_api`)
answers ``text/event-stream`` with these events:

- ``id: <seq>`` + ``event: message`` + ``ChatMessageOut`` JSON — persisted lines only carry ids;
- ``event: session`` ``{"status", "agent_name"}`` — on connect and whenever either changes;
- ``event: typing`` ``{"who": "agent" | "bot"}`` — forwarded from the hub without a DB read
  (the hub item carries ``who``; anything else counts as ``agent``);
- ``event: end`` ``{"reason"}`` — the session is ``ended``/``abandoned``/purged; the stream then
  closes and the client stops reconnecting (R-SSE-CAPS e: also right after the replay when
  the session was already over at connect);
- ``: hb`` — heartbeat ``STREAM_HEARTBEAT_SECONDS`` after the last bytes sent (then a re-read).

The loop subscribes to the hub FIRST and only then reads ``seq > cursor`` from the DB, so a
wake published between the read and the wait is already queued. Every read opens its own
short DB session and closes it before anything is yielded — the long wait is on the hub
queue only, never on a DB connection. A wake that was published before its transaction
committed (the read finds nothing yet) is caught by the re-read after the next heartbeat.

A hub ``close`` item (per-session eviction, the 30 min lifetime, hub shutdown) ends the
stream WITHOUT ``end``: the client reconnects with ``Last-Event-ID`` and replays. The hub
subscription — and with it the per-IP stream slot — is released in ``finally`` and again by
the response when the body was never iterated (client gone before the first chunk).

On connect and at least every heartbeat the stream refreshes the visitor's online marker
(``tiqora:chat:seen:<sid>``, 90 s) and ``visitor_last_seen`` (R-LASTSEEN; that UPDATE is
throttled to once per minute by ``touch_visitor_last_seen``).

Imports are one-way: this module uses the hub; the hub never imports this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any, Final

import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Receive, Scope, Send

from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import VISITOR_SEEN_PREFIX
from tiqora.webchat.config_cache import public_config_cache
from tiqora.webchat.hub import CLOSE_ITEM, ChatHub
from tiqora.webchat.serialize import fallback_config, message_out, session_agent_name
from tiqora.webchat.sessions import touch_visitor_last_seen
from tiqora.webchat.store import get_chat_session, load_messages_since

logger = structlog.get_logger(__name__)

#: Same value as the agent stream (``api/v1/events.py``); own constant, no import from tiqora.api.
STREAM_HEARTBEAT_SECONDS: Final = 25
#: ``stream_connect`` limit per session (C8).
STREAM_CONNECTS_PER_MINUTE: Final = 30
#: TTL of the visitor online marker ``tiqora:chat:seen:<sid>``.
VISITOR_SEEN_TTL_SECONDS: Final = 90
HEARTBEAT_CHUNK: Final = b": hb\n\n"

_FINAL_STATUSES: Final = frozenset({"ended", "abandoned"})
_PAGE: Final = 200
_LAST_EVENT_ID_MAX_DIGITS: Final = 18


def sse_event(*, event: str, data: dict[str, Any], event_id: int | None = None) -> bytes:
    """One SSE frame; ``json.dumps`` escapes newlines, so ``data`` is always a single line."""
    head = f"id: {int(event_id)}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n".encode()


TYPING_CHUNK: Final = sse_event(event="typing", data={"who": "agent"})
BOT_TYPING_CHUNK: Final = sse_event(event="typing", data={"who": "bot"})


def typing_chunk(item: dict[str, Any]) -> bytes:
    """The SSE frame of a hub typing item: ``bot`` only when the item says so."""
    return BOT_TYPING_CHUNK if item.get("who") == "bot" else TYPING_CHUNK


def parse_last_event_id(raw: str | None) -> int | None:
    """The ``Last-Event-ID`` header as an int ≥ 0, else ``None`` (the caller uses ``after``)."""
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > _LAST_EVENT_ID_MAX_DIGITS:
        return None
    if not (value.isascii() and value.isdigit()):
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    chunks: list[bytes]
    cursor: int
    status: str | None
    agent_name: str | None
    end_reason: str | None


async def _read(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    cursor: int,
    *,
    touch: bool,
) -> _Snapshot:
    """Everything new since *cursor*, serialized inside one short DB session."""
    async with session_factory() as db:
        chat = await get_chat_session(db, session_id)
        if chat is None or chat.purged_at is not None:
            reason = (chat.end_reason if chat is not None else None) or "ended"
            return _Snapshot([], cursor, None, None, reason)
        chunks: list[bytes] = []
        while True:
            rows = await load_messages_since(db, session_id, cursor, _PAGE)
            for row in rows:
                out = message_out(row)
                chunks.append(
                    sse_event(event="message", data=out.model_dump(mode="json"), event_id=out.seq)
                )
                cursor = out.seq
            if len(rows) < _PAGE:
                break
        status = str(chat.status)
        if status in _FINAL_STATUSES:
            return _Snapshot(chunks, cursor, status, None, chat.end_reason or "ended")
        cached = await public_config_cache.by_id(db, int(chat.config_id))
        values = (cached if cached is not None else fallback_config(chat)).values
        agent_name = await session_agent_name(db, chat, values)
        if touch:
            await touch_visitor_last_seen(db, chat, utc_now())
        await db.commit()
    return _Snapshot(chunks, cursor, status, agent_name, None)


async def _mark_seen(redis_client: Any, session_id: str) -> None:
    try:
        await redis_client.set(
            f"{VISITOR_SEEN_PREFIX}{session_id}", "1", ex=VISITOR_SEEN_TTL_SECONDS
        )
    except Exception:  # noqa: BLE001 - a marker failure must not end the stream
        logger.warning("webchat_visitor_seen_mark_failed")


async def visitor_event_stream(
    request: Request,
    *,
    hub: ChatHub,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    session_id: str,
    after_seq: int,
    heartbeat_seconds: float = STREAM_HEARTBEAT_SECONDS,
    queue: asyncio.Queue[dict[str, Any]] | None = None,
    ip_hash: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[bytes, None]:
    """SSE chunks for one visitor session (see the module docstring).

    The endpoint subscribes before it answers (so a full hub is a 503, not a broken 200) and
    hands over *queue*; without one the generator subscribes itself with *ip_hash*.

    ``: hb`` + re-read are due *heartbeat_seconds* after the last bytes SENT (whatever woke
    the loop in between, so no silent gap ever exceeds one heartbeat) or after the last
    refresh of the online markers, whichever comes first (``clock`` is injectable for tests).
    """
    if queue is None:
        queue = await hub.subscribe(session_id, ip_hash=ip_hash)
    try:
        cursor = after_seq
        sent: tuple[str | None, str | None] | None = None
        last_alive = float("-inf")
        last_sent = clock()
        while True:
            if await request.is_disconnected():
                return
            now = clock()
            alive_due = now - last_alive >= heartbeat_seconds
            if alive_due:
                last_alive = now
                await _mark_seen(redis_client, session_id)
            try:
                snap = await _read(session_factory, session_id, cursor, touch=alive_due)
            except Exception:  # noqa: BLE001 - the client reconnects with Last-Event-ID
                logger.warning("webchat_stream_read_failed", exc_info=True)
                return
            for chunk in snap.chunks:
                yield chunk
                last_sent = clock()
            cursor = snap.cursor
            if snap.end_reason is not None:
                yield sse_event(event="end", data={"reason": snap.end_reason})
                return
            if sent != (snap.status, snap.agent_name):
                sent = (snap.status, snap.agent_name)
                yield sse_event(
                    event="session", data={"status": snap.status, "agent_name": snap.agent_name}
                )
                last_sent = clock()
            # Wait for a wake (re-read), typing (forward) or close, bounded by both deadlines.
            while True:
                if await request.is_disconnected():
                    return
                now = clock()
                if now - last_sent >= heartbeat_seconds or now - last_alive >= heartbeat_seconds:
                    # One wake-up per deadline: heartbeat, then a re-read that refreshes the
                    # markers and catches a wake that was published before its commit.
                    yield HEARTBEAT_CHUNK
                    last_sent = clock()
                    break
                timeout = min(last_sent, last_alive) + heartbeat_seconds - now
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    continue
                kind = item.get("type")
                if kind == CLOSE_ITEM["type"]:
                    return
                if kind == "typing":
                    yield typing_chunk(item)
                    last_sent = clock()
                    continue
                if kind != "wake":
                    continue
                # Collapse a burst of queued wakes into ONE re-read (typing/close still count).
                typing: dict[str, Any] | None = None
                while not queue.empty():
                    extra = queue.get_nowait()
                    if extra.get("type") == CLOSE_ITEM["type"]:
                        return
                    if extra.get("type") == "typing":
                        typing = extra
                if typing is not None:
                    yield typing_chunk(typing)
                    last_sent = clock()
                break
    finally:
        hub.unsubscribe(session_id, queue)


class _ReleasingStreamingResponse(StreamingResponse):
    """Releases the hub subscription however the response ends — also when the body was
    never iterated (the generator's own ``finally`` would then never run)."""

    def __init__(
        self,
        content: AsyncGenerator[bytes, None],
        *,
        release: Callable[[], None],
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._release = release
        self._stream = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()
            with contextlib.suppress(Exception):
                await self._stream.aclose()


def visitor_stream_response(
    stream: AsyncGenerator[bytes, None], *, release: Callable[[], None]
) -> StreamingResponse:
    return _ReleasingStreamingResponse(
        stream,
        release=release,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
