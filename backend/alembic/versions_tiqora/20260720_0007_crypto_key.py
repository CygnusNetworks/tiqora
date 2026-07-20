"""Create tiqora_crypto_key table.

Revision ID: 20260720_0007
Revises: 20260720_0006
Create Date: 2026-07-20

Phase 2c Task B: bookkeeping record for imported PGP/S/MIME keys (fingerprint
or email + purpose + who/when). Key material itself lives outside the DB —
gpg keyring / cert-dir files — see ``tiqora.crypto.keystore``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0007"
down_revision: str | None = "20260720_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_crypto_key",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_type", sa.String(20), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("purpose", sa.String(20), nullable=False, server_default="both"),
        sa.Column("has_private_key", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_crypto_key_type_identifier",
        "tiqora_crypto_key",
        ["key_type", "identifier"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_crypto_key_type_identifier", table_name="tiqora_crypto_key")
    op.drop_table("tiqora_crypto_key")
