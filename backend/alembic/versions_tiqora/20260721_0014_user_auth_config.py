"""Create tiqora_user_auth_config for per-agent SSO + 2FA policy.

Revision ID: 20260721_0014
Revises: 20260721_0013
Create Date: 2026-07-21

Per-agent Kerberos SSO eligibility and 2FA enforcement flags. Soft user_id
join (no FK) — only ``tiqora_*`` DDL (parallel operation).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0014"
down_revision: str | None = "20260721_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_user_auth_config",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sso_eligible", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("enforce_2fa", sa.Boolean(), nullable=False, server_default="0"),
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
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_user_auth_config")
