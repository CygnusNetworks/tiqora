"""Create tiqora_mail_log (inbound/outbound email communication log).

Revision ID: 20260720_0010
Revises: 20260720_0009
Create Date: 2026-07-20

Additive table for Znuny-style Communication Log: one row per outbound
agent-reply send attempt and per inbound fetch/process outcome. Only
``tiqora_*`` tables are touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0010"
down_revision: str | None = "20260720_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_mail_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("from_addr", sa.String(500), server_default="", nullable=False),
        sa.Column("to_addr", sa.String(1000), server_default="", nullable=False),
        sa.Column("cc_addr", sa.String(1000), nullable=True),
        sa.Column("subject", sa.String(500), server_default="", nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("article_id", sa.BigInteger(), nullable=True),
        sa.Column("queue", sa.String(200), nullable=True),
        sa.Column("smtp_code", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_mail_log_created_at",
        "tiqora_mail_log",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_tiqora_mail_log_direction_status",
        "tiqora_mail_log",
        ["direction", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_mail_log_direction_status", table_name="tiqora_mail_log")
    op.drop_index("ix_tiqora_mail_log_created_at", table_name="tiqora_mail_log")
    op.drop_table("tiqora_mail_log")
