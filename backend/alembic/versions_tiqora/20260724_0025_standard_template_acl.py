"""Create tiqora_standard_template_group/_user for per-template edit ACL.

Revision ID: 20260724_0025
Revises: 20260723_0024
Create Date: 2026-07-24

Per-template edit grants to permission groups and individual agents, so
non-admins can be allowed to edit specific Standard Templates. Znuny's
``standard_template`` has no permission model and must not be altered under
parallel operation, so the ACL lives entirely in these ``tiqora_*`` tables.
Soft joins (no FK) to ``standard_template.id`` / ``permission_groups.id`` /
``users.id``. A template with no rows here stays admin-only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0025"
down_revision: str | None = "20260723_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_standard_template_group",
        sa.Column("standard_template_id", sa.Integer(), nullable=False),
        sa.Column("permission_group_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("standard_template_id", "permission_group_id"),
    )
    op.create_index(
        "ix_tiqora_standard_template_group_group",
        "tiqora_standard_template_group",
        ["permission_group_id"],
    )
    op.create_table(
        "tiqora_standard_template_user",
        sa.Column("standard_template_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("standard_template_id", "user_id"),
    )
    op.create_index(
        "ix_tiqora_standard_template_user_user",
        "tiqora_standard_template_user",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tiqora_standard_template_user_user",
        table_name="tiqora_standard_template_user",
    )
    op.drop_table("tiqora_standard_template_user")
    op.drop_index(
        "ix_tiqora_standard_template_group_group",
        table_name="tiqora_standard_template_group",
    )
    op.drop_table("tiqora_standard_template_group")
