"""Outbox drain task: reads tiqora_event_outbox and re-indexes affected tickets.

Runs every minute via taskiq scheduler. Marks rows as processed after
successful Meilisearch indexing, so a crash between index and mark will
cause double-processing (idempotent: re-index is harmless).
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.search import SearchIndexService
from tiqora.events.pubsub import get_pubsub_redis, publish_ticket_event
from tiqora.worker.webhooks import dispatch_webhooks

logger = structlog.get_logger(__name__)

# How many outbox rows to process per run
_BATCH_SIZE = 500


async def drain_outbox(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """Drain unprocessed outbox rows, re-index affected tickets, mark done.

    Returns {"processed": N, "ticket_ids": M}.
    """
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, event_type, ticket_id, payload FROM tiqora_event_outbox"
                    " WHERE processed = 0 ORDER BY id ASC LIMIT :n"
                ),
                {"n": _BATCH_SIZE},
            )
        ).fetchall()

    if not rows:
        return {"processed": 0, "ticket_ids": 0}

    row_ids = [int(r[0]) for r in rows]
    ticket_ids = sorted({int(r[2]) for r in rows})
    webhook_rows = [(str(r[1]), int(r[2]), r[3]) for r in rows]

    # Re-index in Meilisearch
    async with factory() as session:
        svc = SearchIndexService(session, cfg)
        try:
            await svc.index_tickets(ticket_ids)
        finally:
            await svc.close()

    # Fan out to webhook subscribers — best-effort, never blocks the drain.
    try:
        await dispatch_webhooks(webhook_rows, settings=cfg, session_factory=factory)
    except Exception:  # noqa: BLE001 — webhook delivery must not fail the drain
        logger.exception("webhook_dispatch_error")

    # Notify SSE subscribers (frontend cache invalidation) — best-effort,
    # fire-and-forget, never blocks the drain. One message per distinct
    # (ticket_id, event_type) pair already grouped by the outbox rows.
    try:
        pubsub_client = get_pubsub_redis(cfg)
        distinct_events = sorted({(int(r[2]), str(r[1])) for r in rows})
        for ticket_id, event_type in distinct_events:
            await publish_ticket_event(pubsub_client, ticket_id, event_type)
    except Exception:  # noqa: BLE001 — pub/sub notification must not fail the drain
        logger.exception("pubsub_publish_error")

    # Mark as processed
    in_clause = ",".join(str(i) for i in row_ids)
    async with factory() as session, session.begin():
        await session.execute(
            text(f"UPDATE tiqora_event_outbox SET processed = 1 WHERE id IN ({in_clause})")
        )

    logger.info("outbox_drain", processed=len(row_ids), ticket_ids=len(ticket_ids))
    return {"processed": len(row_ids), "ticket_ids": len(ticket_ids)}
