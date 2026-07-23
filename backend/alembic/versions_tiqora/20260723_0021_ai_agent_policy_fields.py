"""Add sender-blocklist, reply-language and state-whitelist policy fields.

Revision ID: 20260723_0021
Revises: 20260723_0020
Create Date: 2026-07-23

Combines the plan's blocks 2, 3, 5 (see ``~/.claude/plans/declarative-bubbling-pixel.md``)
into one migration on ``tiqora_ai_queue_policy``:

- ``ignored_senders`` / ``ignore_senders_manual`` — sender blocklist (block 2,
  see ``tiqora.ai.senders``).
- ``reply_language_mode`` / ``reply_language_fixed`` / ``reply_language_default``
  — configurable reply language (block 3, see ``tiqora.ai.reply_language``).
  No hardcoded language default: `mode="off"` (server default) reproduces
  today's behaviour exactly.
- ``allowed_state_types`` — ``update_ticket_fields`` state-change whitelist by
  Znuny state *type* (block 5, see ``tiqora.ai.tools``). NULL means "use the
  code default `['open']`" — never a hardcoded value at the DB layer.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0021"
down_revision: str | None = "20260723_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_queue_policy", sa.Column("ignored_senders", sa.Text(), nullable=True)
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column(
            "ignore_senders_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column(
            "reply_language_mode",
            sa.String(10),
            nullable=False,
            server_default="off",
        ),
    )
    op.add_column(
        "tiqora_ai_queue_policy", sa.Column("reply_language_fixed", sa.String(20), nullable=True)
    )
    op.add_column(
        "tiqora_ai_queue_policy", sa.Column("reply_language_default", sa.String(20), nullable=True)
    )
    op.add_column(
        "tiqora_ai_queue_policy", sa.Column("allowed_state_types", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_queue_policy", "allowed_state_types")
    op.drop_column("tiqora_ai_queue_policy", "reply_language_default")
    op.drop_column("tiqora_ai_queue_policy", "reply_language_fixed")
    op.drop_column("tiqora_ai_queue_policy", "reply_language_mode")
    op.drop_column("tiqora_ai_queue_policy", "ignore_senders_manual")
    op.drop_column("tiqora_ai_queue_policy", "ignored_senders")
