"""Background jobs: attempts, due time and checkpoint (retries and safe requeue).

Revision ID: 20261009_0063
Revises: 20261008_0062
Create Date: 2026-10-09

``attempts`` counts the claims of a job, ``due_at`` holds a retried job back until that
moment (naive UTC, application-written), ``checkpoint`` is set by a handler right before
an irreversible step (e.g. SMTP) so an interrupted job is only requeued when nothing
happened yet. Existing rows keep their behaviour (0 / NULL / NULL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0063"
down_revision: str | None = "20261008_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_job",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("tiqora_job", sa.Column("due_at", sa.DateTime(), nullable=True))
    op.add_column("tiqora_job", sa.Column("checkpoint", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_job", "checkpoint")
    op.drop_column("tiqora_job", "due_at")
    op.drop_column("tiqora_job", "attempts")
