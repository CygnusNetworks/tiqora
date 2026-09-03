"""Add cost-budget columns to tiqora_llm_provider.

Revision ID: 20260903_0041
Revises: 20260814_0040
Create Date: 2026-09-03

Adds ``budget_cost_day`` / ``budget_cost_week`` / ``budget_cost_month``
(in whatever currency ``price_currency`` says) to ``tiqora_llm_provider``.
All three are nullable — a budget is optional; when unset, the provider's
spend is never capped, exactly as before this migration. Enforcement lives
in ``tiqora.ai.usage.provider_budget_exceeded``, which sums the existing
``tiqora_ai_usage.cost_hint`` column against these limits.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0041"
down_revision: str | None = "20260814_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_llm_provider", sa.Column("budget_cost_day", sa.Float(), nullable=True)
    )
    op.add_column(
        "tiqora_llm_provider", sa.Column("budget_cost_week", sa.Float(), nullable=True)
    )
    op.add_column(
        "tiqora_llm_provider", sa.Column("budget_cost_month", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tiqora_llm_provider", "budget_cost_month")
    op.drop_column("tiqora_llm_provider", "budget_cost_week")
    op.drop_column("tiqora_llm_provider", "budget_cost_day")
