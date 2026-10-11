"""Report schedules and delivery runs: five tiqora_report_* tables.

Revision ID: 20261010_0074
Revises: 20261010_0072
Create Date: 2026-10-11

``owner_user_id`` points into Znuny (no FK). ``tiqora_report_run.schedule_id`` has no FK:
runs outlive a deleted schedule until retention. Items, recipients and run files cascade.
Chained on 0072 on this branch; the controller re-chains at merge (0073 is web chat).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")
LONG_BLOB = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")

revision: str = "20261010_0074"
down_revision: str | None = "20261010_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_report_schedule",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("format", sa.String(4), nullable=False, server_default="pdf"),
        sa.Column("recurrence", sa.Text(), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="de"),
        sa.Column("subject", sa.String(250), nullable=False),
        sa.Column("body", MEDIUM_TEXT, nullable=False),
        sa.Column("only_if_nonempty", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused_reason", sa.String(500), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(10), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_report_schedule_due", "tiqora_report_schedule", ["active", "next_run_at"]
    )
    op.create_index("ix_tiqora_report_schedule_owner", "tiqora_report_schedule", ["owner_user_id"])

    op.create_table(
        "tiqora_report_schedule_item",
        sa.Column("schedule_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("position", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("report_key", sa.String(120), nullable=False),
        sa.Column("overrides", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["tiqora_report_schedule.id"],
            name="fk_tiqora_report_schedule_item_schedule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("schedule_id", "position"),
    )
    op.create_index(
        "ix_tiqora_report_schedule_item_key", "tiqora_report_schedule_item", ["report_key"]
    )

    op.create_table(
        "tiqora_report_schedule_recipient",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(250), nullable=True),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["tiqora_report_schedule.id"],
            name="fk_tiqora_report_schedule_recipient_schedule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_report_schedule_recipient_schedule",
        "tiqora_report_schedule_recipient",
        ["schedule_id"],
    )
    op.create_index(
        "ix_tiqora_report_schedule_recipient_email",
        "tiqora_report_schedule_recipient",
        ["email"],
    )

    op.create_table(
        "tiqora_report_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=True),
        sa.Column("schedule_name", sa.String(200), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("run_kind", sa.String(10), nullable=False),
        sa.Column("owner_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("report_keys", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_report_run_schedule", "tiqora_report_run", ["schedule_id", "id"])
    op.create_index("ix_tiqora_report_run_finished", "tiqora_report_run", ["finished_at"])
    op.create_index("ix_tiqora_report_run_owner", "tiqora_report_run", ["owner_user_id"])

    op.create_table(
        "tiqora_report_run_file",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("data", LONG_BLOB, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["tiqora_report_run.id"],
            name="fk_tiqora_report_run_file_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_report_run_file_run", "tiqora_report_run_file", ["run_id"])


def downgrade() -> None:
    op.drop_table("tiqora_report_run_file")
    op.drop_table("tiqora_report_run")
    op.drop_table("tiqora_report_schedule_recipient")
    op.drop_table("tiqora_report_schedule_item")
    op.drop_table("tiqora_report_schedule")
