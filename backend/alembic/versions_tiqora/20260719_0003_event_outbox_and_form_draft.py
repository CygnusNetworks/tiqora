"""Create tiqora_event_outbox and tiqora_form_draft tables.

Revision ID: 20260719_0003
Revises: 20260719_0002
Create Date: 2026-07-19

tiqora_event_outbox: transactional outbox written in the same DB transaction
as all ticket write operations. Drained by the taskiq worker for Meilisearch
re-indexing and (Phase 3) webhook delivery.

tiqora_form_draft: Tiqora-owned JSON draft storage. We don't write to Znuny's
form_draft table because its content column holds Perl Storable binary blobs
that we cannot read or produce from Python.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0003"
down_revision: str | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_event_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_event_outbox_processed",
        "tiqora_event_outbox",
        ["processed", "id"],
    )

    op.create_table(
        "tiqora_form_draft",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "changed",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_form_draft_ticket_user",
        "tiqora_form_draft",
        ["ticket_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_form_draft_ticket_user", table_name="tiqora_form_draft")
    op.drop_table("tiqora_form_draft")
    op.drop_index("ix_tiqora_event_outbox_processed", table_name="tiqora_event_outbox")
    op.drop_table("tiqora_event_outbox")
