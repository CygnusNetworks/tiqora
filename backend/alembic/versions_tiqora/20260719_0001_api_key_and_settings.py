"""Create tiqora_api_key and tiqora_settings tables.

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_api_key",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("valid", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_tiqora_api_key_hash"),
    )
    op.create_index("ix_tiqora_api_key_user_id", "tiqora_api_key", ["user_id"])

    op.create_table(
        "tiqora_settings",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_settings")
    op.drop_index("ix_tiqora_api_key_user_id", table_name="tiqora_api_key")
    op.drop_table("tiqora_api_key")
