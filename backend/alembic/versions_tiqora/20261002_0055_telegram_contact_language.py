"""Store the Telegram client language on tiqora_telegram_contact.

Revision ID: 20261002_0055
Revises: 20261001_0054
Create Date: 2026-10-02

``language_code`` is Telegram's ``from.language_code`` (e.g. "de", "en-US").
The bot's own texts (greeting, consent prompt, identity-check messages) and
the agent's reply-button preset fall back to it when the customer's words
don't make their language clear. NULL = unknown (English default).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0055"
down_revision: str | None = "20261001_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_telegram_contact", sa.Column("language_code", sa.String(16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tiqora_telegram_contact", "language_code")
