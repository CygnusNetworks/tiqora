"""Create tiqora_gdpr_job and tiqora_gdpr_backup for GDPR erasure.

Revision ID: 20260721_0015
Revises: 20260721_0014
Create Date: 2026-07-21

Admin GDPR erasure (anonymize / hard-delete) with 30-day backup+rollback.
Additive ``tiqora_*`` only (parallel operation).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0015"
down_revision: str | None = "20260721_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_gdpr_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.Column("resolved_logins", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("counts", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("force_parallel", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Column("backup_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_gdpr_job_status_expires",
        "tiqora_gdpr_job",
        ["status", "backup_expires_at"],
    )

    op.create_table(
        "tiqora_gdpr_backup",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("row_pk", sa.Text(), nullable=False),
        sa.Column("original_row", sa.Text(), nullable=False),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_gdpr_backup_job_id", "tiqora_gdpr_backup", ["job_id"])
    op.create_index("ix_tiqora_gdpr_backup_created", "tiqora_gdpr_backup", ["created"])


def downgrade() -> None:
    op.drop_index("ix_tiqora_gdpr_backup_created", table_name="tiqora_gdpr_backup")
    op.drop_index("ix_tiqora_gdpr_backup_job_id", table_name="tiqora_gdpr_backup")
    op.drop_table("tiqora_gdpr_backup")
    op.drop_index("ix_tiqora_gdpr_job_status_expires", table_name="tiqora_gdpr_job")
    op.drop_table("tiqora_gdpr_job")
