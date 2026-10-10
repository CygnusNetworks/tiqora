"""Create the dashboard tables (dashboards, role/group links, widgets, counter samples).

Revision ID: 20261010_0071
Revises: 20261010_0070
Create Date: 2026-10-10

``filters``, ``settings`` and ``layout`` hold JSON text, ``MEDIUMTEXT`` on MariaDB.
Znuny ids (users, roles, groups) are mirrored without foreign keys; every FK stays
inside the ``tiqora_*`` tables. Nothing is seeded: the default dashboard is code.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261010_0071"
down_revision: str | None = "20261010_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "tiqora_dashboard",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("icon", sa.String(40), nullable=True),
        sa.Column("sort", sa.Integer(), server_default="0", nullable=False),
        sa.Column("kind", sa.String(10), server_default="personal", nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_key", sa.String(60), nullable=True),
        sa.Column("source_template_id", sa.BigInteger(), nullable=True),
        sa.Column("source_template_version", sa.Integer(), nullable=True),
        sa.Column("filters", _MEDIUM_TEXT, nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "source_key", name="uq_tiqora_dashboard_source"),
    )
    op.create_index(
        "ix_tiqora_dashboard_owner", "tiqora_dashboard", ["owner_user_id", "kind", "sort"]
    )

    op.create_table(
        "tiqora_dashboard_role",
        sa.Column("dashboard_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("role_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            ["tiqora_dashboard.id"],
            name="fk_tiqora_dashboard_role_dashboard",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dashboard_id", "role_id"),
    )
    op.create_index("ix_tiqora_dashboard_role_role", "tiqora_dashboard_role", ["role_id"])

    op.create_table(
        "tiqora_dashboard_group",
        sa.Column("dashboard_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("group_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            ["tiqora_dashboard.id"],
            name="fk_tiqora_dashboard_group_dashboard",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dashboard_id", "group_id"),
    )
    op.create_index("ix_tiqora_dashboard_group_group", "tiqora_dashboard_group", ["group_id"])

    op.create_table(
        "tiqora_dashboard_widget",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("dashboard_id", sa.BigInteger(), nullable=False),
        sa.Column("type_key", sa.String(120), nullable=False),
        sa.Column("type_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("title_override", sa.String(200), nullable=True),
        sa.Column("settings", _MEDIUM_TEXT, nullable=False),
        sa.Column("layout", _MEDIUM_TEXT, nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            ["tiqora_dashboard.id"],
            name="fk_tiqora_dashboard_widget_dashboard",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_dashboard_widget_dashboard",
        "tiqora_dashboard_widget",
        ["dashboard_id", "position"],
    )

    op.create_table(
        "tiqora_dashboard_counter_sample",
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("counter_key", sa.String(120), nullable=False),
        sa.Column("sample_day", sa.Date(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "counter_key", "sample_day"),
    )
    op.create_index(
        "ix_tiqora_dashboard_sample_day", "tiqora_dashboard_counter_sample", ["sample_day"]
    )


def downgrade() -> None:
    op.drop_table("tiqora_dashboard_counter_sample")
    op.drop_table("tiqora_dashboard_widget")
    op.drop_table("tiqora_dashboard_group")
    op.drop_table("tiqora_dashboard_role")
    op.drop_table("tiqora_dashboard")
