"""Create tiqora_feature_grant for agent features granted to users/groups/roles.

Revision ID: 20261006_0058
Revises: 20261002_0057
Create Date: 2026-10-06

First feature: ``customer_directory`` (the agent "Kunden" page with list and
vCard exports). A grant names a single agent, a permission group (all its
members) or a role. Admins always have every feature. Soft joins, no FK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0058"
down_revision: str | None = "20261002_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_feature_grant",
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("feature", "subject_type", "subject_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_feature_grant")
