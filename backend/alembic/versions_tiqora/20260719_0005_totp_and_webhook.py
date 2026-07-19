"""Create tiqora_user_totp and tiqora_webhook tables.

Revision ID: 20260719_0005
Revises: 20260719_0004
Create Date: 2026-07-19

Phase 3c: TOTP 2FA enrollment (tiqora_user_totp) and outbound webhook
subscriptions (tiqora_webhook) fed by the tiqora_event_outbox drain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0005"
down_revision: str | None = "20260719_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_user_totp",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "tiqora_webhook",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("events", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default="1"),
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


def downgrade() -> None:
    op.drop_table("tiqora_webhook")
    op.drop_table("tiqora_user_totp")
