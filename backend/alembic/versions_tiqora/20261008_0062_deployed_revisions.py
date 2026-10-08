"""Create tiqora_process_revision and tiqora_acl_revision (deployed snapshots).

Revision ID: 20261008_0062
Revises: 20261008_0061
Create Date: 2026-10-08

``config`` is the canonical JSON dump of the deployed process/ACL configuration,
``MEDIUMTEXT`` on MariaDB (plain ``TEXT`` stops at 64 KiB). ``deployed_at`` is
written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261008_0062"
down_revision: str | None = "20261008_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("process", "acl")


def upgrade() -> None:
    for kind in _TABLES:
        table = f"tiqora_{kind}_revision"
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("entity_count", sa.Integer(), nullable=False),
            sa.Column(
                "config",
                sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb"),
                nullable=False,
            ),
            sa.Column("deployed_at", sa.DateTime(), nullable=False),
            sa.Column("deployed_by", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(f"ix_{table}_checksum", table, ["checksum"])


def downgrade() -> None:
    for kind in reversed(_TABLES):
        op.drop_table(f"tiqora_{kind}_revision")
