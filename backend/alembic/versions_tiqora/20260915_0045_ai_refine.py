"""Add tiqora_ai_queue_policy.enabled_refine.

Revision ID: 20260915_0045
Revises: 20260909_0044
Create Date: 2026-09-15

Per-queue switch for the composer's "Text verfeinern" action
(``tiqora.ai.refine``). Off by default like every other AI feature flag, so
enabling it stays a deliberate admin decision per queue.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0045"
down_revision: str | None = "20260909_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column(
            "enabled_refine",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_queue_policy", "enabled_refine")
