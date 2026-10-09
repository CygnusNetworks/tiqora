"""Create tiqora_ai_customer_pause for per-customer AI automation stops.

Revision ID: 20261009_0061
Revises: 20261007_0060
Create Date: 2026-10-09

An agent can switch all automatic AI actions (auto-reply, triage,
auto-summary) off for one customer user or a whole customer company,
indefinitely or until a date. Soft joins, no FK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0061"
down_revision: str | None = "20261007_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_ai_customer_pause",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("customer_key", sa.String(200), nullable=False),
        sa.Column("paused_until", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "customer_key", name="uq_tiqora_ai_customer_pause_key"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_ai_customer_pause")
