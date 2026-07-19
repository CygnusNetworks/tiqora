"""SSE realtime event stream + agent presence endpoints.

Design notes
------------
* The SSE stream (``GET /events/stream``) is a thin passthrough over the
  ``tiqora:events`` Redis pub/sub channel (see
  :mod:`tiqora.events.pubsub`) — it does not do any DB work itself, so it
  stays cheap to hold open per connected agent.
* Presence changes are *not* pushed as a full payload over SSE. A presence
  write only publishes a small ``{"type": "presence_changed", "ticket_id":
  ...}`` marker; SSE subscribers react by invalidating/refetching
  ``GET /tickets/{id}/presence`` instead of receiving presence state
  in-band. This keeps the pub/sub message tiny and avoids serializing
  presence twice (once for the DB/Redis write, once for the wire).
* Heartbeats: a ``: heartbeat\\n\\n`` SSE comment is emitted whenever no
  pub/sub message arrives within ``HEARTBEAT_INTERVAL_SECONDS`` — this
  keeps intermediate proxies/load balancers from timing out the idle
  connection and gives the generator a natural point to check for client
  disconnect.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated, Literal

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tiqora.api.deps import CurrentUser, get_redis
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL, publish_presence_changed

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["events"])

HEARTBEAT_INTERVAL_SECONDS = 25
PRESENCE_TTL_SECONDS = 30
PRESENCE_KEY_PREFIX = "tiqora:presence:"

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def _presence_key(ticket_id: int, user_id: int) -> str:
    return f"{PRESENCE_KEY_PREFIX}{ticket_id}:{user_id}"


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def _event_stream(request: Request, redis_client: redis.Redis) -> AsyncGenerator[bytes, None]:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(TIQORA_EVENTS_CHANNEL)
    try:
        while True:
            if await request.is_disconnected():
                break
            # redis-py's own timeout handling (not an outer asyncio.wait_for)
            # so a slow/absent message never leaves the shared connection's
            # read cancelled mid-frame — it just returns None on timeout.
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=HEARTBEAT_INTERVAL_SECONDS
            )
            if message is None:
                yield b": heartbeat\n\n"
                continue
            data = message.get("data")
            if data is None:
                continue
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            yield f"data: {data}\n\n".encode()
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(TIQORA_EVENTS_CHANNEL)
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        except Exception:  # noqa: BLE001 — best-effort cleanup on disconnect
            logger.warning("sse_cleanup_error")


@router.get("/events/stream")
async def stream_events(
    request: Request,
    user: CurrentUser,
    redis_client: RedisDep,
) -> StreamingResponse:
    """Server-sent event stream of ``tiqora:events`` pub/sub messages.

    Requires an authenticated agent (same session/API-key auth as the rest
    of ``/api/v1``). Each message forwarded is the raw JSON payload
    published to Redis — see :mod:`tiqora.events.pubsub` for the two
    message shapes (``ticket_changed`` / ``presence_changed``).
    """
    del user  # auth only — the stream itself is not scoped to this agent
    return StreamingResponse(
        _event_stream(request, redis_client),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


class PresenceIn(BaseModel):
    mode: Literal["viewing", "composing"]


class PresenceEntry(BaseModel):
    user_id: int
    name: str
    mode: str


@router.post("/tickets/{ticket_id}/presence", status_code=status.HTTP_204_NO_CONTENT)
async def set_presence(
    ticket_id: int,
    body: PresenceIn,
    user: CurrentUser,
    redis_client: RedisDep,
) -> None:
    """Record that ``user`` is viewing/composing on ``ticket_id`` (30s TTL)."""
    name = f"{user.first_name} {user.last_name}".strip() or user.login
    payload = json.dumps({"user_id": user.id, "name": name, "mode": body.mode})
    await redis_client.set(_presence_key(ticket_id, user.id), payload, ex=PRESENCE_TTL_SECONDS)
    await publish_presence_changed(redis_client, ticket_id)


@router.get("/tickets/{ticket_id}/presence", response_model=list[PresenceEntry])
async def get_presence(
    ticket_id: int,
    user: CurrentUser,
    redis_client: RedisDep,
) -> list[PresenceEntry]:
    """Current viewers/composers on ``ticket_id`` (expired entries drop out via TTL)."""
    del user
    entries: list[PresenceEntry] = []
    pattern = f"{PRESENCE_KEY_PREFIX}{ticket_id}:*"
    async for key in redis_client.scan_iter(match=pattern):
        raw = await redis_client.get(key)
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
            entries.append(PresenceEntry(**data))
        except (ValueError, TypeError):
            continue
    return entries
