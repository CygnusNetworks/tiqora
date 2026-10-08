"""Create tiqora_job and tiqora_job_file for background jobs.

Revision ID: 20261008_0061
Revises: 20261007_0060
Create Date: 2026-10-08

``result`` is ``MEDIUMTEXT`` on MariaDB (handler results are JSON up to 1 MB),
``tiqora_job_file.data`` is ``LONGBLOB`` (files up to 200 MiB). All timestamps
are written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261008_0061"
down_revision: str | None = "20261007_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "result", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb"), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("progress_message", sa.String(500), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_job_status_created", "tiqora_job", ["status", "created_at"])
    op.create_index("ix_tiqora_job_created_by", "tiqora_job", ["created_by", "created_at"])
    op.create_index("ix_tiqora_job_kind_dedupe", "tiqora_job", ["kind", "dedupe_key"])
    op.create_index("ix_tiqora_job_finished", "tiqora_job", ["finished_at"])

    op.create_table(
        "tiqora_job_file",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "data",
            sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_job_file_job", "tiqora_job_file", ["job_id"])


def downgrade() -> None:
    op.drop_table("tiqora_job_file")
    op.drop_table("tiqora_job")
