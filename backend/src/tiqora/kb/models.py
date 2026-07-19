"""SQLAlchemy models for the ``tiqora_kb_*`` knowledge base tables.

Mirrors the style of ``tiqora.db.tiqora.models`` (same declarative base,
same Alembic-managed metadata) but lives in the ``kb`` package for module
cohesion — the KB feature owns its schema, chunker, service, and API
together. Migration: ``alembic/versions_tiqora/20260719_0004_kb_tables.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase

# Article lifecycle states.
STATE_DRAFT = "draft"
STATE_REVIEW = "review"
STATE_PUBLISHED = "published"
STATE_ARCHIVED = "archived"
ARTICLE_STATES = frozenset({STATE_DRAFT, STATE_REVIEW, STATE_PUBLISHED, STATE_ARCHIVED})


class TiqoraKbCategory(TiqoraBase):
    """KB category tree (self-referential via ``parent_id``, not FK-enforced)."""

    __tablename__ = "tiqora_kb_category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    permission_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_tiqora_kb_category_parent_id", "parent_id"),)


class TiqoraKbArticle(TiqoraBase):
    """KB article: Markdown source, versioned, chunked on publish."""

    __tablename__ = "tiqora_kb_article"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="en", server_default="en"
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATE_DRAFT, server_default=STATE_DRAFT
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_tiqora_kb_article_category_id", "category_id"),
        Index("ix_tiqora_kb_article_state", "state"),
    )


class TiqoraKbArticleVersion(TiqoraBase):
    """Snapshot of an article's title/content taken before each content update."""

    __tablename__ = "tiqora_kb_article_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    article_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_kb_article_version_article_id", "article_id", "version"),)


class TiqoraKbAttachment(TiqoraBase):
    """Binary attachment belonging to a KB article."""

    __tablename__ = "tiqora_kb_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    article_id: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(250), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (Index("ix_tiqora_kb_attachment_article_id", "article_id"),)


class TiqoraKbChunk(TiqoraBase):
    """Heading-aware chunk of a published article's Markdown, indexed in Meilisearch."""

    __tablename__ = "tiqora_kb_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    article_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_tiqora_kb_chunk_article_id", "article_id", "seq"),)


class TiqoraKbTag(TiqoraBase):
    """A reusable tag name."""

    __tablename__ = "tiqora_kb_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)


class TiqoraKbArticleTag(TiqoraBase):
    """Many-to-many article<->tag association."""

    __tablename__ = "tiqora_kb_article_tag"

    article_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)


class TiqoraKbLink(TiqoraBase):
    """Link from a KB article to another object (e.g. a ticket)."""

    __tablename__ = "tiqora_kb_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    article_id: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_kb_link_article_id", "article_id"),
        Index("ix_tiqora_kb_link_object", "object_type", "object_id"),
    )
