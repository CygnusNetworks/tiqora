"""Web chat phase 2: AI first line, identified visitors, handoff bookkeeping.

Revision ID: 20261010_0073
Revises: 20261010_0072
Create Date: 2026-10-11

Add-only. ``tiqora_chat_session.status`` gains the value ``bot`` and
``tiqora_chat_message.sender`` gains ``bot`` (both columns already fit the value), so
there is no backfill: existing rows keep working with the defaults below.
``identity_secret`` holds ``encrypt_secret`` ciphertext, never plain text.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0073"
down_revision: str | None = "20261010_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIG = "tiqora_chat_config"
_SESSION = "tiqora_chat_session"
_INDEX = "ix_tiqora_chat_session_bot"


def upgrade() -> None:
    op.add_column(_CONFIG, sa.Column("bot_name", sa.String(80), nullable=True))
    op.add_column(_CONFIG, sa.Column("bot_tone_prompt", sa.Text(), nullable=True))
    op.add_column(
        _CONFIG,
        sa.Column("show_on_portal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        _CONFIG,
        sa.Column(
            "identity_create_customers", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(_CONFIG, sa.Column("identity_secret", sa.Text(), nullable=True))

    op.add_column(_SESSION, sa.Column("identity_source", sa.String(10), nullable=True))
    for name in ("last_visitor_seq", "bot_answered_seq"):
        op.add_column(
            _SESSION, sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column(_SESSION, sa.Column("bot_claimed_at", sa.DateTime(), nullable=True))
    op.add_column(_SESSION, sa.Column("bot_claim_owner", sa.String(64), nullable=True))
    op.add_column(
        _SESSION, sa.Column("bot_attempts", sa.SmallInteger(), nullable=False, server_default="0")
    )
    op.add_column(_SESSION, sa.Column("bot_outcome", sa.String(12), nullable=True))
    op.add_column(_SESSION, sa.Column("handoff_at", sa.DateTime(), nullable=True))
    op.add_column(_SESSION, sa.Column("handoff_reason", sa.String(12), nullable=True))
    op.create_index(_INDEX, _SESSION, ["status", "bot_claimed_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_SESSION)
    for column in (
        "handoff_reason",
        "handoff_at",
        "bot_outcome",
        "bot_attempts",
        "bot_claim_owner",
        "bot_claimed_at",
        "bot_answered_seq",
        "last_visitor_seq",
        "identity_source",
    ):
        op.drop_column(_SESSION, column)
    for column in (
        "identity_secret",
        "identity_create_customers",
        "show_on_portal",
        "bot_tone_prompt",
        "bot_name",
    ):
        op.drop_column(_CONFIG, column)
