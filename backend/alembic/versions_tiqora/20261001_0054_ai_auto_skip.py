"""Record why the auto worker skipped a ticket on tiqora_ai_ticket_state.

Revision ID: 20261001_0054
Revises: 20260930_0053
Create Date: 2026-10-01

``auto_skip_reason`` / ``auto_skip_at``: a cap (daily token budget, rate
limit, max auto replies) or an unavailable model made the auto worker skip a
customer article. Until now that was only a log line; the ticket's AI panel
shows it from these columns. Both NULL = no skip recorded.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0054"
down_revision: str | None = "20260930_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_ticket_state", sa.Column("auto_skip_reason", sa.String(40), nullable=True)
    )
    op.add_column("tiqora_ai_ticket_state", sa.Column("auto_skip_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "auto_skip_at")
    op.drop_column("tiqora_ai_ticket_state", "auto_skip_reason")
