"""Read-only orphan-FK report across the ~15 most important Znuny relations.

Used by ``tiqora ownership orphan-report`` (Phase 5, subtask 2). This is
**read-only**: it only counts dangling foreign-key references. No cleanup is
performed — that is explicitly out of scope for v1 (see
``alembic/versions_owned/README.md`` and ``docs/cutover.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from tiqora.db.legacy.article import Article, ArticleDataMime, ArticleDataMimeAttachment
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketLockType,
    TicketPriority,
    TicketState,
    TicketType,
    TicketWatcher,
)
from tiqora.db.legacy.user import Users


@dataclass(frozen=True)
class OrphanRelation:
    relation: str
    orphan_count: int


@dataclass(frozen=True)
class _Relation:
    name: str
    child: type[DeclarativeBase]
    child_fk: Any
    parent: type[DeclarativeBase]
    parent_pk: Any
    fk_nullable: bool = False


# The ~15 most important relations for a ticket system: ticket's own FKs into
# lookup/master tables, article's chain down to MIME data, plus the history
# and dynamic-field-value tables that everything else joins against.
_RELATIONS: list[_Relation] = [
    _Relation("ticket.queue_id -> queue.id", Ticket, Ticket.queue_id, Queue, Queue.id),
    _Relation("ticket.user_id -> users.id", Ticket, Ticket.user_id, Users, Users.id),
    _Relation(
        "ticket.responsible_user_id -> users.id",
        Ticket,
        Ticket.responsible_user_id,
        Users,
        Users.id,
    ),
    _Relation(
        "ticket.ticket_state_id -> ticket_state.id",
        Ticket,
        Ticket.ticket_state_id,
        TicketState,
        TicketState.id,
    ),
    _Relation(
        "ticket.ticket_priority_id -> ticket_priority.id",
        Ticket,
        Ticket.ticket_priority_id,
        TicketPriority,
        TicketPriority.id,
    ),
    _Relation(
        "ticket.ticket_lock_id -> ticket_lock_type.id",
        Ticket,
        Ticket.ticket_lock_id,
        TicketLockType,
        TicketLockType.id,
    ),
    _Relation(
        "ticket.type_id -> ticket_type.id",
        Ticket,
        Ticket.type_id,
        TicketType,
        TicketType.id,
        fk_nullable=True,
    ),
    _Relation(
        "ticket.service_id -> service.id",
        Ticket,
        Ticket.service_id,
        Service,
        Service.id,
        fk_nullable=True,
    ),
    _Relation("ticket.sla_id -> sla.id", Ticket, Ticket.sla_id, Sla, Sla.id, fk_nullable=True),
    _Relation("article.ticket_id -> ticket.id", Article, Article.ticket_id, Ticket, Ticket.id),
    _Relation(
        "article_data_mime.article_id -> article.id",
        ArticleDataMime,
        ArticleDataMime.article_id,
        Article,
        Article.id,
    ),
    _Relation(
        "article_data_mime_attachment.article_id -> article.id",
        ArticleDataMimeAttachment,
        ArticleDataMimeAttachment.article_id,
        Article,
        Article.id,
    ),
    _Relation(
        "ticket_history.ticket_id -> ticket.id",
        TicketHistory,
        TicketHistory.ticket_id,
        Ticket,
        Ticket.id,
    ),
    _Relation(
        "ticket_watcher.ticket_id -> ticket.id",
        TicketWatcher,
        TicketWatcher.ticket_id,
        Ticket,
        Ticket.id,
    ),
    _Relation(
        "dynamic_field_value.field_id -> dynamic_field.id",
        DynamicFieldValue,
        DynamicFieldValue.field_id,
        DynamicField,
        DynamicField.id,
    ),
]


async def build_orphan_report(session: AsyncSession) -> list[OrphanRelation]:
    """Count dangling FK references for each relation in :data:`_RELATIONS`.

    Read-only: emits one ``SELECT COUNT(*) ... LEFT JOIN ... WHERE parent IS NULL``
    per relation.
    """
    results: list[OrphanRelation] = []
    for rel in _RELATIONS:
        stmt = (
            select(func.count())
            .select_from(rel.child)
            .outerjoin(
                rel.parent,
                cast(rel.child_fk, Integer) == cast(rel.parent_pk, Integer),
            )
            .where(and_(rel.parent_pk.is_(None), rel.child_fk.is_not(None)))
        )
        count = (await session.execute(stmt)).scalar_one()
        results.append(OrphanRelation(relation=rel.name, orphan_count=count))
    return results
