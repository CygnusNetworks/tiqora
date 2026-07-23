"""Add pii_ner_enabled + summary_detail columns to tiqora_ai_queue_policy.

Revision ID: 20260723_0024
Revises: 20260723_0023
Create Date: 2026-07-23

``pii_ner_enabled`` (default true) toggles the spaCy-NER-derived name
masking supplement (see ``tiqora.ai.ner``, ``tiqora.ai.context``) — only
takes effect together with the existing ``pii_masking`` flag.

``summary_detail`` (default "standard") selects the ``tiqora.ai.summary``
system-prompt verbosity — "standard" reproduces the exact pre-existing
prompt; "detailed" asks for longer paragraphs and 3-5 sentence per-document
summaries (see ``tiqora.ai.models.DETAIL_STANDARD``/``DETAIL_DETAILED``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0024"
down_revision: str | None = "20260723_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("pii_ner_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("summary_detail", sa.String(12), nullable=False, server_default="standard"),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_queue_policy", "summary_detail")
    op.drop_column("tiqora_ai_queue_policy", "pii_ner_enabled")
