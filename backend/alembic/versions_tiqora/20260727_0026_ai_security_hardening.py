"""AI security hardening: MCP parameters_snapshot + capabilities_json.

Revision ID: 20260727_0026
Revises: 20260724_0025
Create Date: 2026-07-27

``tiqora_mcp_tool_policy.parameters_snapshot`` stores the discovered MCP
tool JSON Schema so the executor can reject unknown argument keys.

``tiqora_ai_queue_policy.capabilities_json`` holds optional per-queue
capability overrides (bool map) on top of autonomy-derived defaults.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0026"
down_revision: str | None = "20260724_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_mcp_tool_policy",
        sa.Column("parameters_snapshot", sa.Text(), nullable=True),
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("capabilities_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_queue_policy", "capabilities_json")
    op.drop_column("tiqora_mcp_tool_policy", "parameters_snapshot")
