"""Turn tiqora_crypto_key into a real audit trail of key-store mutations.

Revision ID: 20260929_0051
Revises: 20260928_0050
Create Date: 2026-09-29

The PGP/S-MIME admin (B1 of the crypto parity work) writes one row per
mutation — import, delete, private key added/removed, signer relation
added/removed — so ``action`` records what happened, ``user_id`` who did it
and ``detail`` a short, secret-free description. Existing rows were all
imports, hence the server default.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_0051"
down_revision: str | None = "20260928_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_crypto_key",
        sa.Column("action", sa.String(length=32), nullable=False, server_default="import"),
    )
    op.add_column("tiqora_crypto_key", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("tiqora_crypto_key", sa.Column("detail", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_crypto_key", "detail")
    op.drop_column("tiqora_crypto_key", "user_id")
    op.drop_column("tiqora_crypto_key", "action")
