"""Add supports_vision (tiqora_llm_provider) and vision_provider_id
(tiqora_ai_queue_policy) for the AI attachment vision pre-pass.

Revision ID: 20260722_0018
Revises: 20260722_0017
Create Date: 2026-07-22

See attachment-handling architecture: document attachments (PDF/docx/xlsx/
odt/plaintext) are text-extracted and embedded directly in the main model's
context; image attachments are only ever shown to a dedicated vision model
(this column pair), never the main model — the vision model's textual
description is what gets embedded instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0018"
down_revision: str | None = "20260722_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_llm_provider",
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("vision_provider_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tiqora_ai_queue_policy_vision_provider",
        "tiqora_ai_queue_policy",
        "tiqora_llm_provider",
        ["vision_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tiqora_ai_queue_policy_vision_provider",
        "tiqora_ai_queue_policy",
        type_="foreignkey",
    )
    op.drop_column("tiqora_ai_queue_policy", "vision_provider_id")
    op.drop_column("tiqora_llm_provider", "supports_vision")
