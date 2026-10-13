"""Runtime plugin install: packages, repositories, process registry, restarts, kept data.

Revision ID: 20261010_0082
Revises: 20261010_0077
Create Date: 2026-10-13

New columns on ``tiqora_plugin`` plus ``tiqora_plugin_package``,
``tiqora_plugin_repository``, ``tiqora_process``, ``tiqora_restart`` and
``tiqora_plugin_data`` (ownership of kept ``tqp_<short_id>_*`` tables). Ids/keys are at
most ``VARCHAR(190)`` (utf8mb4 index limit on MariaDB); timestamps are naive UTC written
by the application.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.types import TypeEngine

revision: str = "20261010_0082"
down_revision: str | None = "20261010_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BLOB = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")
_MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")

_PLUGIN_COLUMNS: tuple[tuple[str, TypeEngine[Any]], ...] = (
    ("short_id", sa.String(16)),
    ("package_sha256", sa.CHAR(64)),
    ("repository_id", sa.Integer()),
    ("key_id", sa.String(16)),
    ("signer", sa.String(200)),
    ("installed_by", sa.Integer()),
    ("migrated_version", sa.String(64)),
    ("migrated_head", sa.String(64)),
    ("migrated_at", sa.DateTime()),
    ("migration_error", sa.Text()),
)


def upgrade() -> None:
    for name, type_ in _PLUGIN_COLUMNS:
        op.add_column("tiqora_plugin", sa.Column(name, type_, nullable=True))

    op.create_table(
        "tiqora_plugin_package",
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("plugin_id", sa.String(190), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("data", _BLOB, nullable=False),
        sa.Column("manifest", sa.Text(), nullable=False),
        sa.Column("report", _MEDIUM_TEXT, nullable=False),
        sa.Column("signature_status", sa.String(20), nullable=False),
        sa.Column("key_id", sa.String(16), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("installed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_index("ix_tiqora_plugin_package_plugin_id", "tiqora_plugin_package", ["plugin_id"])

    op.create_table(
        "tiqora_plugin_repository",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("url_key", sa.CHAR(64), nullable=False),
        sa.Column("public_key", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("index_json", _MEDIUM_TEXT, nullable=True),
        sa.Column("index_sha256", sa.CHAR(64), nullable=True),
        sa.Column("index_generated_at", sa.DateTime(), nullable=True),
        sa.Column("index_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_tiqora_plugin_repository_url_key",
        "tiqora_plugin_repository",
        ["url_key"],
        unique=True,
    )

    op.create_table(
        "tiqora_process",
        sa.Column("instance_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("hostname", sa.String(200), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("boot_generation", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("exiting_at", sa.DateTime(), nullable=True),
        # JSON list of "<plugin id>@<package sha>" this process loaded (F8: runtime dir GC)
        sa.Column("loaded_packages", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_index("ix_tiqora_process_role", "tiqora_process", ["role"])

    op.create_table(
        "tiqora_restart",
        sa.Column("generation", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("roles", sa.String(200), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("failed_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("generation"),
    )

    # F2: who owns the kept ``tqp_<short_id>_*`` tables of a removed plugin
    op.create_table(
        "tiqora_plugin_data",
        sa.Column("short_id", sa.String(16), nullable=False),
        sa.Column("plugin_id", sa.String(190), nullable=False),
        sa.Column("kept_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("short_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_plugin_data")
    op.drop_table("tiqora_restart")
    op.drop_index("ix_tiqora_process_role", table_name="tiqora_process")
    op.drop_table("tiqora_process")
    op.drop_index("uq_tiqora_plugin_repository_url_key", table_name="tiqora_plugin_repository")
    op.drop_table("tiqora_plugin_repository")
    op.drop_index("ix_tiqora_plugin_package_plugin_id", table_name="tiqora_plugin_package")
    op.drop_table("tiqora_plugin_package")
    for name, _type in reversed(_PLUGIN_COLUMNS):
        op.drop_column("tiqora_plugin", name)
