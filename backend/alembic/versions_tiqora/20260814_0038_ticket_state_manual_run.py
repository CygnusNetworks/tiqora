"""Add Manual Assist run-bookkeeping columns to ``tiqora_ai_ticket_state``.

Manual Assist (``POST /tickets/{id}/ai/draft``) used to run the agent
synchronously in the request. With Hetzner-hosted reasoning models a run can
take 4-7 minutes — well past nginx's ``proxy_read_timeout 90s`` — so the
route now kicks the run off in a background task and returns immediately.
These columns let the frontend poll ``GET /tickets/{id}/ai`` for the
outcome of that background task instead.

Nullable, no server default — mirrors the ORM model exactly (see
20260814_0034/0035 for why a model/migration default mismatch is
dangerous: test DBs are built from ORM metadata, not from migrations, so
drift there is invisible until a strict MariaDB INSERT fails in
production).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0038"
down_revision = "20260814_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("manual_run_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("manual_run_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("manual_run_error_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("manual_run_started_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "manual_run_started_at")
    op.drop_column("tiqora_ai_ticket_state", "manual_run_error_code")
    op.drop_column("tiqora_ai_ticket_state", "manual_run_notes")
    op.drop_column("tiqora_ai_ticket_state", "manual_run_status")
