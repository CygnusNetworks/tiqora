"""Add ai_escalated_at to tiqora_ai_ticket_state.

Marks tickets the AI handed off to a human (escalate_to_human or an
escalation-rule hit). Nullable, no server default — mirrors the ORM
column exactly (see 20260814_0038 for why a model/migration default
mismatch is dangerous: test DBs are built from ORM metadata, not from
migrations).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0042"
down_revision: str | None = "20260903_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_ticket_state",
        sa.Column("ai_escalated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_ticket_state", "ai_escalated_at")
