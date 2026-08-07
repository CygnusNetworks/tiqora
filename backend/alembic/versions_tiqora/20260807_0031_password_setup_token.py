"""One-time password-setup tokens for new agents.

Revision ID: 20260807_0031
Revises: 20260807_0030
Create Date: 2026-08-07

New agents used to receive a generated password in plaintext by mail, which
then lived in their inbox indefinitely. Instead the account is now created
with an unusable random hash and the mail carries a one-time link; the
password is first chosen in the recipient's browser.

Only the SHA-256 of the token is stored — a leak of this table yields no
usable links. The unique constraint on ``token_hash`` doubles as the lookup
index for redemption.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0031"
down_revision: str | None = "20260807_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_password_setup_token",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires", sa.DateTime(), nullable=False),
        sa.Column("used", sa.DateTime(), nullable=True),
        sa.Column("created", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_tiqora_password_setup_token_hash"),
    )
    op.create_index(
        "ix_tiqora_password_setup_token_user",
        "tiqora_password_setup_token",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_password_setup_token_user", table_name="tiqora_password_setup_token")
    op.drop_table("tiqora_password_setup_token")
