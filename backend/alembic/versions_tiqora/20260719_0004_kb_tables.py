"""Create tiqora_kb_* knowledge base tables.

Revision ID: 20260719_0004
Revises: 20260719_0003
Create Date: 2026-07-19

Knowledge base: categories, articles (Markdown source + versioning), chunks
(heading-aware, ~500 tokens, pushed to the Meilisearch ``kb`` index),
attachments, tags, and ticket links. Mirrors the no-FK-constraint style of
the sibling tiqora migrations (Tiqora tables are Alembic-managed but kept
loosely coupled via plain indexed integer columns, not FK constraints, for
cross-dialect simplicity — same as ``tiqora_form_draft.ticket_id``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_kb_category",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("permission_group_id", sa.Integer(), nullable=True),
        sa.Column("customer_visible", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sort", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("valid", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column(
            "create_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column(
            "change_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tiqora_kb_category_slug"),
    )
    op.create_index("ix_tiqora_kb_category_parent_id", "tiqora_kb_category", ["parent_id"])

    op.create_table(
        "tiqora_kb_article",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("slug", sa.String(length=500), nullable=False),
        sa.Column("language", sa.String(length=10), server_default="en", nullable=False),
        sa.Column("state", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column(
            "create_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column(
            "change_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tiqora_kb_article_slug"),
    )
    op.create_index("ix_tiqora_kb_article_category_id", "tiqora_kb_article", ["category_id"])
    op.create_index("ix_tiqora_kb_article_state", "tiqora_kb_article", ["state"])

    op.create_table(
        "tiqora_kb_article_version",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_kb_article_version_article_id",
        "tiqora_kb_article_version",
        ["article_id", "version"],
    )

    op.create_table(
        "tiqora_kb_attachment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=250), nullable=False),
        sa.Column("content_type", sa.String(length=250), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_kb_attachment_article_id", "tiqora_kb_attachment", ["article_id"])

    op.create_table(
        "tiqora_kb_chunk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("anchor", sa.String(length=255), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_kb_chunk_article_id", "tiqora_kb_chunk", ["article_id", "seq"])

    op.create_table(
        "tiqora_kb_tag",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_kb_tag_name"),
    )

    op.create_table(
        "tiqora_kb_article_tag",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("article_id", "tag_id"),
    )

    op.create_table(
        "tiqora_kb_link",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_kb_link_article_id", "tiqora_kb_link", ["article_id"])
    op.create_index("ix_tiqora_kb_link_object", "tiqora_kb_link", ["object_type", "object_id"])


def downgrade() -> None:
    op.drop_index("ix_tiqora_kb_link_object", table_name="tiqora_kb_link")
    op.drop_index("ix_tiqora_kb_link_article_id", table_name="tiqora_kb_link")
    op.drop_table("tiqora_kb_link")
    op.drop_table("tiqora_kb_article_tag")
    op.drop_table("tiqora_kb_tag")
    op.drop_index("ix_tiqora_kb_chunk_article_id", table_name="tiqora_kb_chunk")
    op.drop_table("tiqora_kb_chunk")
    op.drop_index("ix_tiqora_kb_attachment_article_id", table_name="tiqora_kb_attachment")
    op.drop_table("tiqora_kb_attachment")
    op.drop_index("ix_tiqora_kb_article_version_article_id", table_name="tiqora_kb_article_version")
    op.drop_table("tiqora_kb_article_version")
    op.drop_index("ix_tiqora_kb_article_state", table_name="tiqora_kb_article")
    op.drop_index("ix_tiqora_kb_article_category_id", table_name="tiqora_kb_article")
    op.drop_table("tiqora_kb_article")
    op.drop_index("ix_tiqora_kb_category_parent_id", table_name="tiqora_kb_category")
    op.drop_table("tiqora_kb_category")
