"""Create customer TOTP and passkey tables (portal second factor).

Revision ID: 20261009_0066
Revises: 20261009_0065
Create Date: 2026-10-09

Additive ``tiqora_*`` only. Keyed by ``customer_user.id`` (column
``customer_user_id``), never by the agent id space.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0066"
down_revision: str | None = "20261009_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_customer_totp",
        sa.Column("customer_user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("customer_user_id"),
    )
    op.create_table(
        "tiqora_customer_passkey",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("customer_user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(255), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", sa.Text(), nullable=True),
        sa.Column("aaguid", sa.String(64), nullable=True),
        sa.Column("name", sa.String(120), nullable=False, server_default="Passkey"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index(
        "ix_tiqora_customer_passkey_user", "tiqora_customer_passkey", ["customer_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_customer_passkey_user", table_name="tiqora_customer_passkey")
    op.drop_table("tiqora_customer_passkey")
    op.drop_table("tiqora_customer_totp")
