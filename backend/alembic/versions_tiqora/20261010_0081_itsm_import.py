"""ITSM importer: tiqora_itsm_import_run and tiqora_itsm_import_map.

Revision ID: 20261010_0081
Revises: 20261010_0079
Create Date: 2026-10-13

Tables only. The add-on views ``tiqora_v1_itsm_ci`` / ``tiqora_v1_itsm_ci_links`` belong to
the CMDB migration (20261010_0079); the importer adds no view. ``summary`` is MEDIUMTEXT on
MariaDB. ``started_by`` is a Znuny user id and ``job_id`` a job id (no foreign keys: Znuny
tables are not referenced and jobs are purged). Timestamps are naive UTC written by the app.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")

revision: str = "20261010_0081"
down_revision: str | None = "20261010_0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_itsm_import_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("started_by", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("profile", sa.String(50), nullable=True),
        sa.Column("summary", MEDIUM_TEXT, nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_itsm_import_run_started", "tiqora_itsm_import_run", ["started_at"])

    op.create_table(
        "tiqora_itsm_import_map",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_id", name="uq_tiqora_itsm_import_map_src"),
    )
    op.create_index(
        "ix_tiqora_itsm_import_map_target",
        "tiqora_itsm_import_map",
        ["source_kind", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_itsm_import_map_target", table_name="tiqora_itsm_import_map")
    op.drop_table("tiqora_itsm_import_map")
    op.drop_index("ix_tiqora_itsm_import_run_started", table_name="tiqora_itsm_import_run")
    op.drop_table("tiqora_itsm_import_run")
