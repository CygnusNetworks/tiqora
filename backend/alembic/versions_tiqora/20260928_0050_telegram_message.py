"""Add tiqora_telegram_message: map table between articles and Telegram messages.

Revision ID: 20260928_0050
Revises: 20260925_0049
Create Date: 2026-09-28

One row per article sent/received on the Telegram channel, keyed by
``article_id`` (no FK, like ``tiqora_ai_article_origin`` — Znuny's
``article`` table lives outside ``tiqora_metadata``). See
``tiqora.channels.telegram.messages`` for the read/write helpers, used by
later tasks for outbound send, inbound receipt, button callbacks and
edit/retract.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0050"
down_revision: str | None = "20260925_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_telegram_message",
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("extra_message_ids", sa.Text(), nullable=True),
        sa.Column("reply_to_article_id", sa.BigInteger(), nullable=True),
        sa.Column("buttons_json", sa.Text(), nullable=True),
        sa.Column("answered_button", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("original_body", sa.Text(), nullable=True),
        sa.Column("retracted_at", sa.DateTime(), nullable=True),
        sa.Column("retracted_by", sa.Integer(), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index(
        "ix_tiqora_telegram_message_ticket_id",
        "tiqora_telegram_message",
        ["ticket_id"],
    )
    op.create_index(
        "ux_tiqora_telegram_message_chat_msg",
        "tiqora_telegram_message",
        ["chat_id", "message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_tiqora_telegram_message_chat_msg", table_name="tiqora_telegram_message")
    op.drop_index("ix_tiqora_telegram_message_ticket_id", table_name="tiqora_telegram_message")
    op.drop_table("tiqora_telegram_message")
