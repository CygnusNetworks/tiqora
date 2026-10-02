"""Per-ticket AI autopilot: a release of N automatic replies, and the handoff reason.

Revision ID: 20261002_0057
Revises: 20261002_0056
Create Date: 2026-10-02

``ai_grant_remaining``/``ai_grant_total``/``ai_grant_by``/``ai_grant_at``: an
agent switched the autopilot back on for N automatic replies. While set, the
release replaces the queue's per-ticket caps; NULL = no release, the caps
apply. ``ai_escalated_reason`` says why the AI handed the ticket over
(``max_clarifications``, ``grant_used``, ``escalate_to_human``, ...), so the
ticket view can show it after later skips have overwritten
``auto_skip_reason``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0057"
down_revision: str | None = "20261002_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tiqora_ai_ticket_state"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("ai_grant_remaining", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("ai_grant_total", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("ai_grant_by", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("ai_grant_at", sa.DateTime(), nullable=True))
    op.add_column(_TABLE, sa.Column("ai_escalated_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    for name in (
        "ai_escalated_reason",
        "ai_grant_at",
        "ai_grant_by",
        "ai_grant_total",
        "ai_grant_remaining",
    ):
        op.drop_column(_TABLE, name)
