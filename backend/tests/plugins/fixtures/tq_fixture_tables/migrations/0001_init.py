"""Create the notes table."""

import sqlalchemy as sa
from alembic import op

revision = "tables_0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "tqp_tables_note",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("body", sa.String(200), nullable=False),
    )
    op.create_index("tqp_tables_note_body", "tqp_tables_note", ["body"])


def downgrade() -> None:
    op.drop_index("tqp_tables_note_body", table_name="tqp_tables_note")
    op.drop_table("tqp_tables_note")
