"""GI debugger: tiqora_gi_debug_entry and tiqora_gi_debug_content.

Revision ID: 20261010_0076
Revises: 20261010_0075
Create Date: 2026-10-11

Same columns as Znuny's ``gi_debugger_entry`` / ``gi_debugger_entry_content`` plus
``name``, ``ticket_id`` and ``replay`` on the entry. ``webservice_id`` and ``ticket_id``
point into Znuny tables (no FK). Content rows go with their entry (``ON DELETE CASCADE``).
``replay`` is MEDIUMTEXT and ``content`` LONGBLOB on MariaDB. Timestamps are written by the
application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")
LONG_BLOB = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")

revision: str = "20261010_0076"
down_revision: str | None = "20261010_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_gi_debug_entry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("communication_id", sa.String(32), nullable=False),
        sa.Column("communication_type", sa.String(50), nullable=False),
        sa.Column("remote_ip", sa.String(50), nullable=True),
        sa.Column("webservice_id", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("replay", MEDIUM_TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("communication_id", name="uq_tiqora_gi_debug_entry_comm"),
    )
    op.create_index(
        "ix_tiqora_gi_debug_entry_ws_time",
        "tiqora_gi_debug_entry",
        ["webservice_id", "create_time"],
    )
    op.create_index("ix_tiqora_gi_debug_entry_ticket", "tiqora_gi_debug_entry", ["ticket_id"])
    op.create_index("ix_tiqora_gi_debug_entry_time", "tiqora_gi_debug_entry", ["create_time"])

    op.create_table(
        "tiqora_gi_debug_content",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("debug_level", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("content", LONG_BLOB, nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["tiqora_gi_debug_entry.id"],
            name="fk_tiqora_gi_debug_content_entry",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_tiqora_gi_debug_content_entry", "tiqora_gi_debug_content", ["entry_id", "id"]
    )
    op.create_index("ix_tiqora_gi_debug_content_time", "tiqora_gi_debug_content", ["create_time"])
    op.create_index("ix_tiqora_gi_debug_content_level", "tiqora_gi_debug_content", ["debug_level"])


def downgrade() -> None:
    op.drop_table("tiqora_gi_debug_content")
    op.drop_table("tiqora_gi_debug_entry")
