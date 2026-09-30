"""Add the per-ticket AI pause to tiqora_ai_ticket_state.

Revision ID: 20260930_0052
Revises: 20260929_0051
Create Date: 2026-09-30

``ai_paused_at`` / ``ai_paused_by``: an agent can switch off all automatic AI
actions (auto-reply, triage, auto-summary) for a single ticket. Unlike
``ai_escalated_at`` the flag is never cleared automatically. Both NULL =
unchanged behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_0052"
down_revision: str | None = "20260929_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tiqora_ai_ticket_state", sa.Column("ai_paused_at", sa.DateTime(), nullable=True))
    op.add_column("tiqora_ai_ticket_state", sa.Column("ai_paused_by", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "ai_paused_by")
    op.drop_column("tiqora_ai_ticket_state", "ai_paused_at")
