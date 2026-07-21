"""Create tiqora_queue_variable + tiqora_placeholder_field.

Revision ID: 20260720_0012
Revises: 20260720_0011
Create Date: 2026-07-20

Configurable placeholder variables for agent-reply templates:
- ``tiqora_queue_variable`` — per-queue (or global) name/value pairs resolved
  as ``<OTRS_QUEUE_X>`` / ``<TIQORA_QUEUE_X>``.
- ``tiqora_placeholder_field`` — customer_user/company column registry for
  the admin picker and optional allow-list gate.

Only ``tiqora_*`` tables are touched (parallel operation — zero Znuny DDL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0012"
down_revision: str | None = "20260720_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_queue_variable",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("queue_id", "name", name="uq_tiqora_queue_variable_queue_name"),
    )
    op.create_index(
        "ix_tiqora_queue_variable_queue_id",
        "tiqora_queue_variable",
        ["queue_id"],
    )

    op.create_table(
        "tiqora_placeholder_field",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_table", sa.String(64), nullable=False),
        sa.Column("column_name", sa.String(120), nullable=False),
        sa.Column("tag_name", sa.String(120), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "source_table",
            "tag_name",
            name="uq_tiqora_placeholder_field_source_tag",
        ),
    )


def downgrade() -> None:
    op.drop_table("tiqora_placeholder_field")
    op.drop_index("ix_tiqora_queue_variable_queue_id", table_name="tiqora_queue_variable")
    op.drop_table("tiqora_queue_variable")
