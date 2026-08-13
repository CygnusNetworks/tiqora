"""Telegram contact table and Telegram-channel support columns.

Revision ID: 20260813_0032
Revises: 20260807_0031
Create Date: 2026-08-13

Adds the storage the Telegram bot channel needs:

- ``tiqora_telegram_contact`` maps a Telegram chat to a (best-effort)
  customer identity — resolved once and cached, since chat/user ids never
  change but a customer's login may (re-linking, merges).
- ``tiqora_ai_queue_policy.llm_fallback_json`` carries a priority-ordered
  list of LLM providers to fall back to when the queue's primary provider
  errors or is unavailable.
- ``tiqora_ai_ticket_state.identity_attempts`` counts failed AI identity
  checks per ticket, mirroring the existing ``auto_reply_count`` /
  ``clarification_count`` guard counters.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0032"
down_revision: str | None = "20260807_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_telegram_contact",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("customer_user_login", sa.String(length=200), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", name="uq_tiqora_telegram_contact_chat_id"),
    )
    op.create_index(
        "ix_tiqora_telegram_contact_customer_login",
        "tiqora_telegram_contact",
        ["customer_user_login"],
    )

    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("llm_fallback_json", sa.Text(), nullable=True),
    )

    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column(
            "identity_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "identity_attempts")
    op.drop_column("tiqora_ai_queue_policy", "llm_fallback_json")
    op.drop_index(
        "ix_tiqora_telegram_contact_customer_login",
        table_name="tiqora_telegram_contact",
    )
    op.drop_table("tiqora_telegram_contact")
