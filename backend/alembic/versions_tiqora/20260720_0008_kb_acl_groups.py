"""Create tiqora_kb_category_group (M2M category<->permission_group ACL).

Revision ID: 20260720_0008
Revises: 20260720_0007
Create Date: 2026-07-20

KB categories can now be visible to *several* permission groups instead of one.
The new join table supersedes the single ``tiqora_kb_category.permission_group_id``
column, which is left in place (deprecated) for parallel-operation/rollback
safety and backfilled here. Only ``tiqora_*`` tables are touched — the Znuny
schema is never modified (project invariant).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0008"
down_revision: str | None = "20260720_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_kb_category_group",
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("permission_group_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("category_id", "permission_group_id"),
    )
    op.create_index(
        "ix_tiqora_kb_category_group_group",
        "tiqora_kb_category_group",
        ["permission_group_id"],
    )
    # Backfill from the deprecated single-group column so existing scoping is
    # preserved the moment the service switches to reading the join table.
    op.execute(
        """
        INSERT INTO tiqora_kb_category_group (category_id, permission_group_id)
        SELECT id, permission_group_id
        FROM tiqora_kb_category
        WHERE permission_group_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_kb_category_group_group", table_name="tiqora_kb_category_group")
    op.drop_table("tiqora_kb_category_group")
