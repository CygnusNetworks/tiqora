"""Create tiqora_ai_prompt_part table.

Revision ID: 20260723_0022
Revises: 20260723_0021
Create Date: 2026-07-23

"Prompt-Bausteine" (see ``tiqora.ai.models.TiqoraAiPromptPart``): ordered,
enable-able system-prompt fragments per queue policy. The effective system
prompt sent to the model is ``tiqora_ai_queue_policy.system_prompt`` followed
by every *enabled* part's ``content``, in ``position`` order (see
``tiqora.ai.runtime._build_system_prompt``). The base ``system_prompt``
column is untouched — parts are purely additive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0022"
down_revision: str | None = "20260723_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_ai_prompt_part",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column(
            "create_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column(
            "change_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["tiqora_ai_queue_policy.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_tiqora_ai_prompt_part_policy_position",
        "tiqora_ai_prompt_part",
        ["policy_id", "position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tiqora_ai_prompt_part_policy_position", table_name="tiqora_ai_prompt_part"
    )
    op.drop_table("tiqora_ai_prompt_part")
