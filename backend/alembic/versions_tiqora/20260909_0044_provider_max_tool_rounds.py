"""Add tiqora_llm_provider.max_tool_rounds.

Revision ID: 20260909_0044
Revises: 20260908_0043
Create Date: 2026-09-09

Per-provider override for how many tool rounds the agent loop grants before the
terminal-force closes a run. Nullable with no server default, mirroring the ORM
column exactly: NULL means "use DEFAULT_MAX_TOOL_ROUNDS", the same
"not configured" semantics the pricing and budget columns already use, so
existing rows keep the code default without a data migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0044"
down_revision: str | None = "20260908_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_llm_provider",
        sa.Column("max_tool_rounds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_llm_provider", "max_tool_rounds")
