"""Remember the customer article auto-reply held back for an open triage.

Revision ID: 20260924_0048
Revises: 20260919_0047
Create Date: 2026-09-24

``tiqora_ai_triage.reply_deferred_article_id``: while a triage proposal is
OPEN the auto-reply worker skips the ticket, but its outbox watermark moves
on, so nothing ever came back to that article after accept/reject — the
customer's opening mail was never answered. The worker now records the
skipped article here and answers it once the proposal is decided.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0048"
down_revision: str | None = "20260919_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_triage",
        sa.Column("reply_deferred_article_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_triage", "reply_deferred_article_id")
