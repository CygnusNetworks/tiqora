"""Add missing CURRENT_TIMESTAMP server defaults on tiqora_telegram_contact.

Revision 20260813_0032 created ``create_time``/``change_time`` as plain
``DATETIME NOT NULL`` without a server default, while the ORM model
(:class:`TiqoraTelegramContact`) declares ``server_default=func.now()`` and
therefore omits both columns on INSERT. On a strict-mode MariaDB this made
every contact insert fail with error 1364 ("Field 'create_time' doesn't have
a default value") — the schema drift was invisible to the test suite because
test databases are created from the ORM metadata, not from the migrations.

Forward-fix only: 0032 stays untouched (already applied in production).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0034"
down_revision = "20260813_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tiqora_telegram_contact",
        "create_time",
        existing_type=sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    op.alter_column(
        "tiqora_telegram_contact",
        "change_time",
        existing_type=sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def downgrade() -> None:
    op.alter_column(
        "tiqora_telegram_contact",
        "change_time",
        existing_type=sa.DateTime(),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "tiqora_telegram_contact",
        "create_time",
        existing_type=sa.DateTime(),
        nullable=False,
        server_default=None,
    )
