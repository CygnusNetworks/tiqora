"""Create tiqora_mail_outbound (admin-configurable outbound SMTP).

Revision ID: 20260720_0009
Revises: 20260720_0008
Create Date: 2026-07-20

Single-row store for agent-reply SMTP settings (host/port/security/auth/
from/timeout). Password is Fernet-encrypted at the application layer before
insert — the column is opaque text. Only ``tiqora_*`` tables are touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0009"
down_revision: str | None = "20260720_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_mail_outbound",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("host", sa.String(255), server_default="", nullable=False),
        sa.Column("port", sa.Integer(), server_default="25", nullable=False),
        sa.Column("security", sa.String(20), server_default="none", nullable=False),
        sa.Column("auth_type", sa.String(20), server_default="none", nullable=False),
        sa.Column("auth_user", sa.String(255), server_default="", nullable=False),
        sa.Column("auth_password", sa.Text(), server_default="", nullable=False),
        sa.Column("from_default", sa.String(255), server_default="", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column(
            "change_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_mail_outbound")
