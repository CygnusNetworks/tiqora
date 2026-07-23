"""Add token-pricing columns to tiqora_llm_provider.

Revision ID: 20260723_0023
Revises: 20260723_0022
Create Date: 2026-07-23

Adds ``price_input_per_1m`` / ``price_output_per_1m`` (USD-or-whatever
``price_currency`` says, per 1M tokens) and ``price_currency`` (ISO 4217,
e.g. ``"USD"``/``"EUR"``) to ``tiqora_llm_provider``. All three are
nullable — pricing is optional metadata; when unset, cost computation
(``tiqora.ai.usage.record_usage``) leaves ``cost_hint`` at ``None`` exactly
as before this migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0023"
down_revision: str | None = "20260723_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_llm_provider", sa.Column("price_input_per_1m", sa.Float(), nullable=True)
    )
    op.add_column(
        "tiqora_llm_provider", sa.Column("price_output_per_1m", sa.Float(), nullable=True)
    )
    op.add_column(
        "tiqora_llm_provider", sa.Column("price_currency", sa.String(3), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tiqora_llm_provider", "price_currency")
    op.drop_column("tiqora_llm_provider", "price_output_per_1m")
    op.drop_column("tiqora_llm_provider", "price_input_per_1m")
