"""Telegram DSGVO consent columns.

Revision ID: 20260813_0033
Revises: 20260813_0032
Create Date: 2026-08-13

Adds the two timestamps the Telegram consent flow (Task 13) needs on
``tiqora_telegram_contact``:

- ``consent_time`` — when the contact accepted the DSGVO consent prompt
  (``NULL`` until then; nothing beyond chat_id/identity fields is stored for
  a contact before this is set).
- ``consent_prompt_time`` — when the consent prompt was last sent, used to
  rate-limit re-prompting (see ``CONSENT_REPROMPT_SECONDS`` in
  ``tiqora.channels.telegram.service``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0033"
down_revision: str | None = "20260813_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_telegram_contact",
        sa.Column("consent_time", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tiqora_telegram_contact",
        sa.Column("consent_prompt_time", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_telegram_contact", "consent_prompt_time")
    op.drop_column("tiqora_telegram_contact", "consent_time")
