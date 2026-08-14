"""Add ``new_dialog_since`` to ``tiqora_telegram_contact`` (/start = new dialog).

Nullable, no server default and explicitly set in code (``process_update`` on
``/start``) — mirrors the ORM model exactly. See 20260814_0034 for why a
model/migration default mismatch is dangerous: test DBs are built from ORM
metadata, not from migrations, so drift there is invisible until a strict
MariaDB INSERT fails in production.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0035"
down_revision = "20260814_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tiqora_telegram_contact",
        sa.Column("new_dialog_since", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tiqora_telegram_contact", "new_dialog_since")
