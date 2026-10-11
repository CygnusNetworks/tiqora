"""Create tiqora_plugin, tiqora_plugin_runtime and tiqora_plugin_hook (plugin system state).

Revision ID: 20261010_0070
Revises: 20261010_0068
Create Date: 2026-10-10

``id`` / ``plugin_id`` / ``hook_key`` are ``VARCHAR(190)`` (utf8mb4 index limit on MariaDB).
All timestamps are written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0070"
down_revision: str | None = "20261010_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_plugin",
        sa.Column("id", sa.String(190), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "signature_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'unsigned'"),
        ),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tiqora_plugin_runtime",
        sa.Column("plugin_id", sa.String(190), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("boot_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("plugin_id", "role"),
    )
    op.create_table(
        "tiqora_plugin_hook",
        sa.Column("hook_key", sa.String(190), nullable=False),
        sa.Column("plugin_id", sa.String(190), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("tripped_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("hook_key"),
    )
    op.create_index("ix_tiqora_plugin_hook_plugin_id", "tiqora_plugin_hook", ["plugin_id"])


def downgrade() -> None:
    op.drop_table("tiqora_plugin_hook")
    op.drop_table("tiqora_plugin_runtime")
    op.drop_table("tiqora_plugin")
