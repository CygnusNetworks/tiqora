"""SQLAlchemy models for tiqora_* tables (Alembic-managed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase


class TiqoraApiKey(TiqoraBase):
    """API key for bearer authentication (``Authorization: Bearer``)."""

    __tablename__ = "tiqora_api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class TiqoraSettings(TiqoraBase):
    """Key/value store for indexer watermarks and runtime flags."""

    __tablename__ = "tiqora_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class TiqoraCacheInvalidation(TiqoraBase):
    """Ticket cache invalidation queue consumed by the Znuny TiqoraSync addon."""

    __tablename__ = "tiqora_cache_invalidation"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_cache_inv_id", "id"),)


class TiqoraEventOutbox(TiqoraBase):
    """Transactional outbox for ticket/article events.

    Written in the same transaction as all write operations; drained by the
    taskiq worker which re-indexes affected tickets in Meilisearch and may
    fan out to webhooks in Phase 3.

    Event names match Znuny-style event identifiers (TicketCreate,
    ArticleCreate, TicketStateUpdate, TicketQueueUpdate, …) so that the
    event log is directly comparable against Znuny's event history for
    golden-master validation.
    """

    __tablename__ = "tiqora_event_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    processed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (Index("ix_tiqora_event_outbox_processed", "processed", "id"),)


class TiqoraFormDraft(TiqoraBase):
    """Tiqora-owned form draft storage (JSON content).

    We intentionally DO NOT write to Znuny's ``form_draft`` table because:
    1. Znuny's ``form_draft.content`` is stored as Perl Storable binary blobs
       (``Storable::freeze``), which we cannot read or write from Python.
    2. Writing invalid Storable data would corrupt Znuny's draft UI.
    3. After cutover (Phase 5) we own the table; until then we keep draft
       data in this separate table and surface it only via the Tiqora API.
    """

    __tablename__ = "tiqora_form_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free-form action name (e.g. "AgentTicketNote", "AgentTicketCompose")
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-encoded draft content (subject, body, to, cc, …)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    changed: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_form_draft_ticket_user", "ticket_id", "user_id"),)
