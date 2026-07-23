"""Shared per-ticket loading helpers (plan §3.4 step 4-5).

Extracted from :mod:`tiqora.ai.runtime` so :mod:`tiqora.ai.summary` (Phase C)
does not duplicate the ticket/article loading and AI-origin labeling
convention: an AI-authored article is never physically removed from the
ticket, only labeled as "(AI, previous own action)" wherever it is rendered
into an LLM prompt (plan §3.4 step 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiTicketState
from tiqora.domain.ticket_service import _is_body_part_attachment, _is_inline_attachment
from tiqora.storage.backend import AttachmentMeta


@dataclass(frozen=True, slots=True)
class TicketSnapshot:
    ticket_id: int
    queue_id: int
    customer_id: str | None
    title: str
    ticket_number: str = ""
    state_name: str = ""
    state_type: str = ""
    queue_name: str = ""
    customer_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentSnapshot:
    """Metadata for one ``article_data_mime_attachment`` row. Content is not
    loaded here — call :func:`load_attachment_content` lazily, only for
    attachments actually used (document extraction / vision pre-pass)."""

    id: int
    filename: str | None
    content_type: str | None
    size: int


@dataclass(frozen=True, slots=True)
class ArticleSnapshot:
    id: int
    sender_type: str
    is_visible_for_customer: bool
    subject: str | None
    body: str | None
    from_address: str | None
    is_ai_origin: bool
    attachments: tuple[AttachmentSnapshot, ...] = ()


class TicketNotFoundError(Exception):
    pass


async def ticket_snapshot(session: AsyncSession, ticket_id: int) -> TicketSnapshot:
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.queue_id, t.customer_id, t.customer_user_id, t.title, t.tn,"
                    " ts.name AS state_name, tst.name AS state_type, q.name AS queue_name"
                    " FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " JOIN queue q ON q.id = t.queue_id"
                    " WHERE t.id = :tid LIMIT 1"
                ),
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
        customer_user_id=row["customer_user_id"],
        title=row["title"] or "",
        ticket_number=row["tn"] or "",
        state_name=row["state_name"] or "",
        state_type=row["state_type"] or "",
        queue_name=row["queue_name"] or "",
    )


def render_ticket_header(ticket: TicketSnapshot) -> str:
    """Structured metadata header rendered ahead of the article text — same
    format for both the agent runtime (:mod:`tiqora.ai.runtime`) and the
    summary service (:mod:`tiqora.ai.summary`), so the model never sees two
    divergent conventions. Never passed through :meth:`PiiMapper.mask` — the
    ticket number/queue/state are not PII, and CustomerID/CustomerUser are
    deliberately shown unmasked (they are needed verbatim for tool calls and
    are already exempted via ``PiiMapper(never_mask=...)``)."""
    customer_id = ticket.customer_id or "not set"
    customer_user_id = ticket.customer_user_id or "not set"
    return "\n".join(
        (
            f"Ticket #{ticket.ticket_id} (number {ticket.ticket_number}): {ticket.title}",
            f"Queue: {ticket.queue_name} | State: {ticket.state_name} ({ticket.state_type})",
            f"CustomerID: {customer_id} | CustomerUser: {customer_user_id}",
        )
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
    article_ids = [int(r["id"]) for r in rows]
    attachments_by_article = await _load_attachment_metadata(session, article_ids)

    return [
        ArticleSnapshot(
            id=int(r["id"]),
            sender_type=str(r["sender_type"]),
            is_visible_for_customer=bool(r["is_visible_for_customer"]),
            subject=r["a_subject"],
            body=r["a_body"],
            from_address=r["a_from"],
            is_ai_origin=bool(r["is_ai_origin"]),
            attachments=tuple(attachments_by_article.get(int(r["id"]), [])),
        )
        for r in rows
    ]


async def _load_attachment_metadata(
    session: AsyncSession, article_ids: list[int]
) -> dict[int, list[AttachmentSnapshot]]:
    """Loads attachment metadata, dropping two canonical Znuny marker
    categories (same predicates as ``tiqora.domain.ticket_service``'s ticket
    zoom — single source of truth, see its module for the exact rules):

    - Body-part pseudo-attachments (the mail's own MIME alternatives) —
      these duplicate the article body text and must never be fed back into
      an LLM context as if they were separate information.
    - Inline parts (``content_id`` set or ``disposition=inline``, e.g.
      signature logos referenced via ``cid:``) — skipped for both document
      extraction and the vision pre-pass.

    A real image attachment (no ``content_id``, ``disposition=attachment``)
    still goes through the vision pass normally even if named "logo.png".
    """
    if not article_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT article_id, id, filename, content_type, content_size,"
                    " content_id, content_alternative, disposition"
                    " FROM article_data_mime_attachment WHERE article_id IN :aids ORDER BY id"
                ).bindparams(bindparam("aids", expanding=True)),
                {"aids": article_ids},
            )
        )
        .mappings()
        .all()
    )
    by_article: dict[int, list[AttachmentSnapshot]] = {}
    for r in rows:
        meta = AttachmentMeta(
            id=int(r["id"]),
            article_id=int(r["article_id"]),
            filename=r["filename"],
            content_type=r["content_type"],
            content_size=r["content_size"],
            content_id=r["content_id"],
            content_alternative=r["content_alternative"],
            disposition=r["disposition"],
        )
        if _is_body_part_attachment(meta) or _is_inline_attachment(meta):
            continue
        try:
            size = int(r["content_size"]) if r["content_size"] else 0
        except ValueError:
            size = 0
        by_article.setdefault(int(r["article_id"]), []).append(
            AttachmentSnapshot(
                id=meta.id, filename=meta.filename, content_type=meta.content_type, size=size
            )
        )
    return by_article


async def load_attachment_content(session: AsyncSession, attachment_id: int) -> bytes | None:
    """Lazily load one attachment's binary content (not fetched by
    :func:`load_articles` — most attachments are never used by an AI run)."""
    row = (
        await session.execute(
            text("SELECT content FROM article_data_mime_attachment WHERE id = :aid"),
            {"aid": attachment_id},
        )
    ).first()
    if row is None or row[0] is None:
        return None
    content = row[0]
    return bytes(content) if not isinstance(content, bytes) else content


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


async def article_from_address(session: AsyncSession, article_id: int) -> str | None:
    """The raw ``article_data_mime.a_from`` header for one article (may be
    ``"Name <mail@x>"`` — callers that need a bare address should parse it,
    e.g. via :mod:`email.utils.parseaddr`, same as the sender-blocklist
    matcher in :mod:`tiqora.ai.senders`)."""
    row = (
        await session.execute(
            text("SELECT a_from FROM article_data_mime WHERE article_id = :aid LIMIT 1"),
            {"aid": article_id},
        )
    ).first()
    return str(row[0]) if row and row[0] is not None else None


async def customer_user_name(
    session: AsyncSession, login: str | None
) -> tuple[str | None, str | None]:
    """Best-effort ``(first_name, last_name)`` lookup for a customer_user.

    ``login`` is ``ticket.customer_user_id`` as stored on the ticket row —
    Znuny's ticket table records the customer_user's *login*, not its
    numeric ``customer_user.id`` PK. Used only to seed PII name-masking
    candidates (see :func:`collect_known_names`); never customer-facing.
    """
    if not login:
        return None, None
    row = (
        await session.execute(
            text("SELECT first_name, last_name FROM customer_user WHERE login = :login LIMIT 1"),
            {"login": login},
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


_NAME_SPLIT_RE = re.compile(r"[\s,]+")


def display_name_tokens(from_header: str | None) -> list[str]:
    """Name candidates parsed out of a raw ``From:`` header's display-name
    part (e.g. ``"Anna-Lena Meyer"`` and its individual tokens from
    ``"Anna-Lena Meyer <a.meyer@example.com>"``). Mirrors the address-only
    extraction in :mod:`tiqora.ai.senders`, which only needs the bare
    address and discards the display name entirely."""
    if not from_header:
        return []
    display_name = parseaddr(from_header)[0].strip()
    if not display_name:
        return []
    return [display_name, *(p for p in _NAME_SPLIT_RE.split(display_name) if p)]


async def collect_known_names(
    session: AsyncSession, ticket: TicketSnapshot, articles: list[ArticleSnapshot]
) -> list[str]:
    """PII name-masking candidates (plan §3.7 gap): the ticket's
    customer_user first/last name plus display-name tokens parsed from every
    article's ``From:`` header. Purely additive/defensive — any piece that
    can't be resolved simply yields fewer candidates, never an error, so a
    ticket with no customer_user match or no From headers behaves exactly
    like today (no name masking)."""
    first, last = await customer_user_name(session, ticket.customer_user_id)
    names = [n for n in (first, last) if n]
    if first and last:
        names.append(f"{first} {last}")
    for article in articles:
        names.extend(display_name_tokens(article.from_address))
    return names


__all__ = [
    "ArticleSnapshot",
    "AttachmentSnapshot",
    "TicketNotFoundError",
    "TicketSnapshot",
    "article_from_address",
    "collect_known_names",
    "customer_user_name",
    "display_name_tokens",
    "get_or_create_state",
    "latest_customer_article_id",
    "load_articles",
    "load_attachment_content",
    "render_ticket_header",
    "ticket_snapshot",
]
