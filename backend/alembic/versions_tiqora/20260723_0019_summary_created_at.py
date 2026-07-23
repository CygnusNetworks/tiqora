"""Add summary_created_at to tiqora_ai_ticket_state.

Revision ID: 20260723_0019
Revises: 20260722_0018
Create Date: 2026-07-23

The ticket-zoom AI panel shows when the current summary was generated and
anchors a "summarized up to here" marker in the article list — that needs a
dedicated timestamp for the summary itself (``last_run_at`` also moves on
runs that produce no new summary, e.g. up_to_date checks).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0019"
down_revision: str | None = "20260722_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("summary_created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "summary_created_at")
