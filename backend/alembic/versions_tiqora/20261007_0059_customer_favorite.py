"""Create tiqora_customer_favorite for the agent's starred customer users.

Revision ID: 20261007_0059
Revises: 20261006_0058
Create Date: 2026-10-07

Personal favorites on the "Kunden" page, listed above the recent and
frequent customers. Soft joins to users.id and customer_user.login, no FK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0059"
down_revision: str | None = "20261006_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_customer_favorite",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("customer_login", sa.String(200), nullable=False),
        sa.Column("create_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id", "customer_login"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_customer_favorite")
