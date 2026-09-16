"""Shared ``tiqora_event_outbox`` reading helpers for the AI workers.

Extracted from :mod:`tiqora.ai.auto_worker` when :mod:`tiqora.ai.triage_worker`
became a second consumer. Each worker keeps its **own** watermark setting —
this module only knows how to read a batch and classify an article, never
where a given consumer has got to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OUTBOX_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: int
    event_type: str
    ticket_id: int
    payload: dict[str, Any]


async def next_outbox_batch(
    session: AsyncSession, after_id: int, batch_size: int
) -> list[OutboxEvent]:
    rows = (
        await session.execute(
            text(
                "SELECT id, event_type, ticket_id, payload FROM tiqora_event_outbox"
                " WHERE id > :after ORDER BY id ASC LIMIT :n"
            ),
            {"after": after_id, "n": batch_size},
        )
    ).fetchall()
    out: list[OutboxEvent] = []
    for row in rows:
        payload: dict[str, Any] = {}
        if row[3]:
            try:
                payload = json.loads(row[3])
            except (TypeError, ValueError):
                payload = {}
        out.append(
            OutboxEvent(
                id=int(row[0]), event_type=str(row[1]), ticket_id=int(row[2]), payload=payload
            )
        )
    return out


async def article_sender_type(session: AsyncSession, article_id: int) -> str | None:
    row = (
        await session.execute(
            text(
                "SELECT st.name FROM article a"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " WHERE a.id = :aid LIMIT 1"
            ),
            {"aid": article_id},
        )
    ).first()
    return str(row[0]) if row else None


async def max_outbox_id(session: AsyncSession) -> int:
    """Highest existing outbox id, for seeding a brand-new consumer's
    watermark so it starts at "now" instead of replaying all history."""
    return int(
        (
            await session.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_event_outbox"))
        ).scalar_one()
    )


__all__ = [
    "OUTBOX_BATCH_SIZE",
    "OutboxEvent",
    "article_sender_type",
    "max_outbox_id",
    "next_outbox_batch",
]
