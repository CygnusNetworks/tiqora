"""DraftService (plan §3.1/§3.4): ``tiqora_ai_draft`` CRUD + lifecycle.

A draft is **never** an article until a human accepts it (or, for the
auto-path, until the runtime's autonomy mapping sends it — which always
goes through the normal article-write path, never through this module).

Supersede rule (plan §3.1): at most one ``open`` draft per
``(ticket_id, based_on_article_id, kind)``. MariaDB has no partial/filtered
unique index, so this is enforced here, inside the same transaction as the
new draft's insert — never as a DB constraint.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import (
    DRAFT_STATUS_ACCEPTED,
    DRAFT_STATUS_DISCARDED,
    DRAFT_STATUS_OPEN,
    DRAFT_STATUS_SUPERSEDED,
    TiqoraAiDraft,
)


class DraftNotFound(Exception):
    pass


class DraftStateError(Exception):
    """Raised when an operation is attempted on a draft in the wrong state."""


async def create_draft(
    session: AsyncSession,
    *,
    ticket_id: int,
    queue_id: int,
    kind: str,
    body: str,
    actor_user_id: int,
    subject: str | None = None,
    based_on_article_id: int | None = None,
    tool_trace_json: str | None = None,
    created_by_user_id: int | None = None,
    source: str = "auto",
) -> TiqoraAiDraft:
    """Create a draft, superseding any existing ``open`` draft with the same
    ``(ticket_id, based_on_article_id, kind)`` key in the same transaction."""
    existing = (
        (
            await session.execute(
                select(TiqoraAiDraft).where(
                    TiqoraAiDraft.ticket_id == ticket_id,
                    TiqoraAiDraft.based_on_article_id == based_on_article_id,
                    TiqoraAiDraft.kind == kind,
                    TiqoraAiDraft.status == DRAFT_STATUS_OPEN,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        row.status = DRAFT_STATUS_SUPERSEDED

    draft = TiqoraAiDraft(
        ticket_id=ticket_id,
        queue_id=queue_id,
        kind=kind,
        subject=subject,
        body=body,
        based_on_article_id=based_on_article_id,
        tool_trace_json=tool_trace_json,
        status=DRAFT_STATUS_OPEN,
        created_by_user_id=created_by_user_id,
        source=source,
        create_by=actor_user_id,
        change_by=actor_user_id,
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return draft


async def get_draft(session: AsyncSession, draft_id: int) -> TiqoraAiDraft | None:
    return await session.get(TiqoraAiDraft, draft_id)


async def list_for_ticket(
    session: AsyncSession, ticket_id: int, *, limit: int = 20
) -> list[TiqoraAiDraft]:
    """Open + recent drafts for a ticket, newest first."""
    rows = (
        (
            await session.execute(
                select(TiqoraAiDraft)
                .where(TiqoraAiDraft.ticket_id == ticket_id)
                .order_by(TiqoraAiDraft.create_time.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def discard_draft(
    session: AsyncSession, row: TiqoraAiDraft, *, actor_user_id: int
) -> TiqoraAiDraft:
    if row.status != DRAFT_STATUS_OPEN:
        raise DraftStateError(f"Draft {row.id} is not open (status={row.status})")
    row.status = DRAFT_STATUS_DISCARDED
    row.change_by = actor_user_id
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_draft(session: AsyncSession, row: TiqoraAiDraft) -> None:
    """Hard-delete a draft row, regardless of status — admin cleanup only,
    never called from the normal draft lifecycle (create/discard/accept
    above), which never removes rows."""
    await session.delete(row)
    await session.commit()


async def mark_accepted(
    session: AsyncSession, draft_id: int, *, article_id: int, actor_user_id: int
) -> TiqoraAiDraft | None:
    """Mark a draft accepted once the corresponding article actually exists.

    Called from the article-create hook — never at prefill time (plan §3.1:
    "erst beim TATSÄCHLICHEN Article-Create gesetzt"). Returns ``None`` if the
    draft does not exist or is not ``open`` (stale/discarded/already
    accepted) so the caller can decide whether to treat that as a no-op.
    """
    row = await session.get(TiqoraAiDraft, draft_id)
    if row is None or row.status != DRAFT_STATUS_OPEN:
        return None
    row.status = DRAFT_STATUS_ACCEPTED
    row.accepted_article_id = article_id
    row.change_by = actor_user_id
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


__all__ = [
    "DraftNotFound",
    "DraftStateError",
    "create_draft",
    "delete_draft",
    "discard_draft",
    "get_draft",
    "list_for_ticket",
    "mark_accepted",
]
