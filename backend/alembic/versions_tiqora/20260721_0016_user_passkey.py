"""Create tiqora_user_passkey for WebAuthn passkeys as 2nd factor.

Revision ID: 20260721_0016
Revises: 20260721_0015
Create Date: 2026-07-21

Additive ``tiqora_*`` only (parallel operation). One-to-many credentials
per agent; ``credential_id`` is base64url and globally unique.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0016"
down_revision: str | None = "20260721_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_user_passkey",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
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
    op.create_index("ix_tiqora_user_passkey_user_id", "tiqora_user_passkey", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_tiqora_user_passkey_user_id", table_name="tiqora_user_passkey")
    op.drop_table("tiqora_user_passkey")
