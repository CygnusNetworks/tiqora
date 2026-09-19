"""Add the reply agent's final-answer model to tiqora_ai_queue_policy.

Revision ID: 20260919_0047
Revises: 20260916_0046
Create Date: 2026-09-19

``final_answer_llm_provider_id`` / ``final_answer_model_override``: the
primary model runs the tool loop, and the run is handed over to this model
once the primary wants to write the customer message. Ticket 43087: the
primary (Qwen3-235B-Instruct) misread a correct diagnosis in 6 of 6 replays,
while stronger models given the same conversation answered correctly.
Both NULL = unchanged behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0047"
down_revision: str | None = "20260916_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("final_answer_llm_provider_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("final_answer_model_override", sa.String(length=200), nullable=True),
    )
    op.create_foreign_key(
        "fk_tiqora_ai_queue_policy_final_answer_llm_provider",
        "tiqora_ai_queue_policy",
        "tiqora_llm_provider",
        ["final_answer_llm_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tiqora_ai_queue_policy_final_answer_llm_provider",
        "tiqora_ai_queue_policy",
        type_="foreignkey",
    )
    op.drop_column("tiqora_ai_queue_policy", "final_answer_model_override")
    op.drop_column("tiqora_ai_queue_policy", "final_answer_llm_provider_id")
