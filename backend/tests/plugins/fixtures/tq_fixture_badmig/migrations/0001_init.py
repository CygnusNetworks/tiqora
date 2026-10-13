"""Touches a Znuny table: the guard must refuse it."""

from alembic import op

revision = "badmig_0001"
down_revision = None


def upgrade() -> None:
    op.execute("UPDATE ticket SET title = 'x'")


def downgrade() -> None:
    pass
