"""Shared per-ticket loading helpers (plan §3.4 step 4-5).

Extracted from :mod:`tiqora.ai.runtime` so :mod:`tiqora.ai.summary` (Phase C)
does not duplicate the ticket/article loading and AI-origin labeling
convention: an AI-authored article is never physically removed from the
ticket, only labeled as "(AI, previous own action)" wherever it is rendered
into an LLM prompt (plan §3.4 step 5).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiTicketState


@dataclass(frozen=True, slots=True)
class TicketSnapshot:
    ticket_id: int
    queue_id: int
    customer_id: str | None
    title: str


@dataclass(frozen=True, slots=True)
class ArticleSnapshot:
    id: int
    sender_type: str
    is_visible_for_customer: bool
    subject: str | None
    body: str | None
    from_address: str | None
    is_ai_origin: bool


class TicketNotFoundError(Exception):
    pass


async def ticket_snapshot(session: AsyncSession, ticket_id: int) -> TicketSnapshot:
    row = (
        (
            await session.execute(
                text("SELECT id, queue_id, customer_id, title FROM ticket WHERE id = :tid LIMIT 1"),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise TicketNotFoundError(f"Ticket {ticket_id} not found")
    return TicketSnapshot(
        ticket_id=int(row["id"]),
        queue_id=int(row["queue_id"]),
        customer_id=row["customer_id"],
        title=row["title"] or "",
    )


async def load_articles(session: AsyncSession, ticket_id: int) -> list[ArticleSnapshot]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, st.name AS sender_type, a.is_visible_for_customer,"
                    " m.a_subject, m.a_body, m.a_from,"
                    " (o.article_id IS NOT NULL) AS is_ai_origin"
                    " FROM article a"
                    " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                    " LEFT JOIN article_data_mime m ON m.article_id = a.id"
                    " LEFT JOIN tiqora_ai_article_origin o ON o.article_id = a.id"
                    " WHERE a.ticket_id = :tid ORDER BY a.id"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        ArticleSnapshot(
            id=int(r["id"]),
            sender_type=str(r["sender_type"]),
            is_visible_for_customer=bool(r["is_visible_for_customer"]),
            subject=r["a_subject"],
            body=r["a_body"],
            from_address=r["a_from"],
            is_ai_origin=bool(r["is_ai_origin"]),
        )
        for r in rows
    ]


async def get_or_create_state(session: AsyncSession, ticket_id: int) -> TiqoraAiTicketState:
    state = await session.get(TiqoraAiTicketState, ticket_id)
    if state is None:
        state = TiqoraAiTicketState(ticket_id=ticket_id)
        session.add(state)
        await session.commit()
        await session.refresh(state)
    return state


async def latest_customer_article_id(session: AsyncSession, ticket_id: int) -> int | None:
    row = (
        await session.execute(
            text(
                "SELECT a.id FROM article a"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid AND st.name = 'customer'"
                " ORDER BY a.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return int(row[0]) if row else None


__all__ = [
    "ArticleSnapshot",
    "TicketNotFoundError",
    "TicketSnapshot",
    "get_or_create_state",
    "latest_customer_article_id",
    "load_articles",
    "ticket_snapshot",
]
