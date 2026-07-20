"""Create tiqora_gdpr_audit table.

Revision ID: 20260720_0006
Revises: 20260719_0005
Create Date: 2026-07-20

Phase 2c: audit trail for ``tiqora gdpr anonymize-customer`` /
``tiqora gdpr retention-run`` (and the retention taskiq worker task). Rows
never contain PII — only who/what/when and row counts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0006"
down_revision: str | None = "20260719_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_gdpr_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("counts", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("force_parallel", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_gdpr_audit_action_created",
        "tiqora_gdpr_audit",
        ["action", "created"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_gdpr_audit_action_created", table_name="tiqora_gdpr_audit")
    op.drop_table("tiqora_gdpr_audit")
