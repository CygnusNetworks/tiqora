"""Strip the id generator from tiqora_user_auth_config.user_id.

Revision ID: 20260908_0043
Revises: 20260904_0042
Create Date: 2026-09-08

The column mirrors ``users.id``; it must never invent one. Migration
``20260721_0014`` always created it as a plain integer, but the ORM model
defaulted to ``autoincrement="auto"``, so every database built from
``TiqoraBase.metadata.create_all`` — the test databases, and production —
got AUTO_INCREMENT on MariaDB or SERIAL on PostgreSQL instead.

A database built purely from this chain is already correct, so this is written
to be a no-op there rather than to assume the broken shape. The matching model
change carries ``autoincrement=False`` so the two stop diverging.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0043"
down_revision: str | None = "20260904_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        # MODIFY rewrites the column definition; omitting AUTO_INCREMENT drops
        # it. Already-plain columns are rewritten to the same shape.
        op.execute("ALTER TABLE tiqora_user_auth_config MODIFY user_id INT NOT NULL")
    elif bind.dialect.name == "postgresql":
        # SERIAL is sugar for "integer + default nextval() + owned sequence".
        # Both statements are no-ops when the column is already plain.
        op.execute("ALTER TABLE tiqora_user_auth_config ALTER COLUMN user_id DROP DEFAULT")
        op.execute("DROP SEQUENCE IF EXISTS tiqora_user_auth_config_user_id_seq")


def downgrade() -> None:
    """Deliberately empty.

    The state this migration leaves behind is the one ``20260721_0014``
    intended, so there is nothing to restore — re-adding the generator would
    recreate the defect rather than the previous schema.
    """
