"""Redis pub/sub helper for realtime SSE event notifications.

Shared by the outbox drain, the Znuny-write poller, the presence endpoints,
and the SSE stream endpoint — keeps Redis client construction and the
message shape in one place so all publishers/subscribers agree on the
channel name and payload format.

Messages published on :data:`TIQORA_EVENTS_CHANNEL` are JSON objects of one
of two shapes:

* ``{"type": "ticket_changed", "ticket_id": <int>, "event": "<event_type>"}``
  — a ticket was created/updated, either via Tiqora's own outbox (event_type
  is the outbox ``event_type`` column, e.g. ``TicketCreate``) or via the
  Znuny-write poller (event_type is the literal string ``"poller"`` since
  the poller only knows *that* a ticket changed, not the precise Znuny
  event).
* ``{"type": "presence_changed", "ticket_id": <int>}`` — an agent's
  viewing/composing presence on a ticket changed. Deliberately does not
  carry the presence payload itself: clients are expected to react by
  re-fetching ``GET /api/v1/tickets/{id}/presence`` (poll-via-invalidation),
  not by receiving full presence state over SSE.

Publishing is always best-effort: this module never raises out of its
publish functions, so callers (outbox drain, poller, presence writes) don't
need their own try/except to stay safe. A failed publish only means
frontend caches will invalidate a bit later via their normal
polling/refetch fallbacks, not that the underlying write failed.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis
import structlog

from tiqora.config import Settings, get_settings

logger = structlog.get_logger(__name__)

TIQORA_EVENTS_CHANNEL = "tiqora:events"

_client: redis.Redis | None = None


def get_pubsub_redis(settings: Settings | None = None) -> redis.Redis:
    """Lazily construct (and cache) a Redis client for background workers.

    The outbox drain and the poller run outside of a FastAPI request scope
    and so don't have access to ``app.state.redis`` / the ``get_redis``
    dependency — this gives them an equivalent, cached the same way
    (module-level singleton, built from ``Settings.redis_url``).
    """
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = redis.from_url(cfg.redis_url, decode_responses=True)
    return _client


async def publish_ticket_event(redis_client: redis.Redis, ticket_id: int, event_type: str) -> None:
    """Publish a ``ticket_changed`` notification. Best-effort — never raises."""
    payload: dict[str, Any] = {
        "type": "ticket_changed",
        "ticket_id": ticket_id,
        "event": event_type,
    }
    await _publish(redis_client, payload)


async def publish_presence_changed(redis_client: redis.Redis, ticket_id: int) -> None:
    """Publish a ``presence_changed`` notification. Best-effort — never raises."""
    payload: dict[str, Any] = {"type": "presence_changed", "ticket_id": ticket_id}
    await _publish(redis_client, payload)


async def _publish(redis_client: redis.Redis, payload: dict[str, Any]) -> None:
    try:
        await redis_client.publish(TIQORA_EVENTS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 — pub/sub notification must never fail the caller
        logger.warning("pubsub_publish_failed", payload=payload)
