"""Add ``run_id`` to ``tiqora_ai_article_origin`` (exact origin↔audit-log
correlation, replacing the heuristic ticket_id/feature/ts-nearest match used
by the backfill CLI for pre-feature rows).

Nullable, no server default — mirrors the ORM model exactly (see
20260814_0034/0035 for why a model/migration default mismatch is dangerous:
test DBs are built from ORM metadata, not from migrations, so drift there is
invisible until a strict MariaDB INSERT fails in production).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0037"
down_revision = "20260814_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tiqora_ai_article_origin",
        sa.Column("run_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_ai_article_origin", "run_id")
