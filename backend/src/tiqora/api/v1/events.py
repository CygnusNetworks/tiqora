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
* ``ticket_new_in_queue`` messages carry a ``queue_id`` and are filtered
  per-connection to the agent's readable queues: the endpoint computes
  ``allowed_queue_ids`` **once** at connection time (the DB session is
  closed before the streaming generator runs) and drops notifications for
  queues the agent can't read. ``ticket_changed`` / ``presence_changed``
  pass through unfiltered as before.

v1 limitations (documented intentionally)
-----------------------------------------
* No notification-persistence table: unread state is live + session-only.
  Reconnecting drops any history the client hadn't already received.
* Permission-set changes mid-connection are not picked up until the client
  reconnects, since ``allowed_queue_ids`` is snapshotted once per SSE
  connection.
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

from tiqora.api.deps import CurrentUser, DbSession, get_redis
from tiqora.domain.queue_service import QueueService
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


def _should_forward(raw: str, allowed_queue_ids: set[int]) -> bool:
    """Whether one raw pub/sub payload should reach *this* connection.

    Only ``ticket_new_in_queue`` is filtered — it is dropped when its
    ``queue_id`` is not among the agent's readable queues. Every other
    message type (and any payload that isn't valid JSON) passes through, so
    a filter miss never silently swallows unrelated events. Extracted as a
    pure function so the per-queue gate is unit-testable without a live SSE
    connection.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return True
    if not isinstance(payload, dict) or payload.get("type") != "ticket_new_in_queue":
        return True
    return payload.get("queue_id") in allowed_queue_ids


async def _event_stream(
    request: Request, redis_client: redis.Redis, allowed_queue_ids: set[int]
) -> AsyncGenerator[bytes, None]:
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
            if not _should_forward(data, allowed_queue_ids):
                continue
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
    session: DbSession,
) -> StreamingResponse:
    """Server-sent event stream of ``tiqora:events`` pub/sub messages.

    Requires an authenticated agent (same session/API-key auth as the rest
    of ``/api/v1``). Each message forwarded is the raw JSON payload
    published to Redis — see :mod:`tiqora.events.pubsub` for the message
    shapes (``ticket_changed`` / ``presence_changed`` /
    ``ticket_new_in_queue``).

    ``ticket_new_in_queue`` notifications are filtered to the agent's
    readable queues. ``allowed_queue_ids`` is resolved **here**, before the
    ``StreamingResponse`` is returned, because the DB session dependency's
    context manager closes once this function returns — the streaming
    generator must not touch it. This snapshots the permission set for the
    life of the connection (see the module docstring's v1 limitations).
    """
    allowed_queue_ids = await QueueService(session).allowed_queue_ids(user.id, "ro")
    return StreamingResponse(
        _event_stream(request, redis_client, allowed_queue_ids),
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
