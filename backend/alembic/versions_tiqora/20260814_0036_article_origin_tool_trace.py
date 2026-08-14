"""Add ``tool_trace_json`` to ``tiqora_ai_article_origin`` (auto-sent AI
articles carry the same tool trace as drafts, exposed in the ticket zoom).

Nullable, no server default — mirrors the ORM model exactly (see
20260814_0034/0035 for why a model/migration default mismatch is dangerous:
test DBs are built from ORM metadata, not from migrations, so drift there is
invisible until a strict MariaDB INSERT fails in production).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0036"
down_revision = "20260814_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_article_origin",
        sa.Column("tool_trace_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_article_origin", "tool_trace_json")
