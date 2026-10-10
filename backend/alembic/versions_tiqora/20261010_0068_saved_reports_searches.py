"""Create saved reports and saved searches (five tiqora_* tables).

Revision ID: 20261010_0068
Revises: 20261009_0067
Create Date: 2026-10-10

``definition`` / ``criteria`` are JSON documents, ``MEDIUMTEXT`` on MariaDB (plain
``TEXT`` stops at 64 KiB). ``owner_user_id`` and ``*_by`` mirror Znuny ``users.id`` and
``group_id`` mirrors ``permission_groups.id``; neither has a foreign key. Timestamps
are written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261010_0068"
down_revision: str | None = "20261009_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "tiqora_report",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="private"),
        sa.Column("definition", _MEDIUM_TEXT, nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("copied_from", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_report_owner", "tiqora_report", ["owner_user_id", "valid"])
    op.create_index("ix_tiqora_report_visibility", "tiqora_report", ["visibility", "valid"])

    op.create_table(
        "tiqora_report_group",
        sa.Column("report_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("group_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["tiqora_report.id"],
            name="fk_tiqora_report_group_report",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("report_id", "group_id"),
    )
    op.create_index("ix_tiqora_report_group_group", "tiqora_report_group", ["group_id"])

    op.create_table(
        "tiqora_saved_search",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("criteria", _MEDIUM_TEXT, nullable=False),
        sa.Column("include_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_by", sa.String(20), nullable=False, server_default="age"),
        sa.Column("sort_order", sa.String(4), nullable=False, server_default="desc"),
        sa.Column("source", sa.String(10), nullable=False, server_default="tiqora"),
        sa.Column("source_name", sa.String(200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id", "source", "source_name", name="uq_tiqora_saved_search_source"
        ),
    )
    op.create_index("ix_tiqora_saved_search_owner", "tiqora_saved_search", ["owner_user_id"])

    op.create_table(
        "tiqora_saved_search_group",
        sa.Column("saved_search_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("group_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["saved_search_id"],
            ["tiqora_saved_search.id"],
            name="fk_tiqora_saved_search_group_search",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("saved_search_id", "group_id"),
    )
    op.create_index(
        "ix_tiqora_saved_search_group_group", "tiqora_saved_search_group", ["group_id"]
    )

    op.create_table(
        "tiqora_saved_search_pref",
        sa.Column("saved_search_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("show_in_sidebar", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_count", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["saved_search_id"],
            ["tiqora_saved_search.id"],
            name="fk_tiqora_saved_search_pref_search",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("saved_search_id", "user_id"),
    )
    op.create_index(
        "ix_tiqora_saved_search_pref_user", "tiqora_saved_search_pref", ["user_id", "position"]
    )


def downgrade() -> None:
    op.drop_table("tiqora_saved_search_pref")
    op.drop_table("tiqora_saved_search_group")
    op.drop_table("tiqora_saved_search")
    op.drop_table("tiqora_report_group")
    op.drop_table("tiqora_report")
