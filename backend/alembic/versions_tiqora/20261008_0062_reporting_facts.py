"""Create the reporting fact, event, snapshot, state and dirty-ticket tables.

Revision ID: 20261008_0062
Revises: 20261008_0061
Create Date: 2026-10-09

``tiqora_report_ticket_fact.ticket_id`` and ``tiqora_report_dirty_ticket.ticket_id``
mirror ``ticket.id`` and are never generated. ``gap_ids`` is ``MEDIUMTEXT`` on
MariaDB (thousands of gaps exceed ``TEXT``'s 64 KiB). All timestamps the
application writes are naive UTC.
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


def _false() -> sa.sql.elements.ColumnElement[bool]:
    return sa.false()


def upgrade() -> None:
    i = sa.Integer()
    op.create_table(
        "tiqora_report_ticket_fact",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tn", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", i, nullable=False),
        sa.Column("queue_id", i, nullable=False),
        sa.Column("queue_id_at_create", i, nullable=False),
        sa.Column("state_id", sa.SmallInteger(), nullable=False),
        sa.Column("state_type", sa.String(30), nullable=False),
        sa.Column("priority_id", sa.SmallInteger(), nullable=False),
        sa.Column("type_id", sa.SmallInteger(), nullable=True),
        sa.Column("service_id", i, nullable=True),
        sa.Column("sla_id", i, nullable=True),
        sa.Column("owner_id", i, nullable=False),
        sa.Column("responsible_id", i, nullable=True),
        sa.Column("customer_id", sa.String(150), nullable=True),
        sa.Column("customer_user", sa.String(250), nullable=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=_false()),
        sa.Column("merged", sa.Boolean(), nullable=False, server_default=_false()),
        sa.Column("first_response_at", sa.DateTime(), nullable=True),
        sa.Column("first_response_minutes", i, nullable=True),
        sa.Column("first_response_working_minutes", i, nullable=True),
        sa.Column("first_response_target_minutes", i, nullable=True),
        sa.Column("first_response_due_at", sa.DateTime(), nullable=True),
        sa.Column("first_response_breached", sa.Boolean(), nullable=True),
        sa.Column("first_closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("solution_minutes", i, nullable=True),
        sa.Column("solution_working_minutes", i, nullable=True),
        sa.Column("solution_target_minutes", i, nullable=True),
        sa.Column("solution_due_at", sa.DateTime(), nullable=True),
        sa.Column("solution_breached", sa.Boolean(), nullable=True),
        sa.Column("update_target_minutes", i, nullable=True),
        sa.Column("update_breach_count", i, nullable=False, server_default="0"),
        sa.Column("calendar_name", sa.String(100), nullable=True),
        sa.Column("close_count", i, nullable=False, server_default="0"),
        sa.Column("reopen_count", i, nullable=False, server_default="0"),
        sa.Column("queue_move_count", i, nullable=False, server_default="0"),
        sa.Column("queue_pingpong", sa.Boolean(), nullable=False, server_default=_false()),
        sa.Column("owner_change_count", i, nullable=False, server_default="0"),
        sa.Column("article_in_count", i, nullable=False, server_default="0"),
        sa.Column("article_out_count", i, nullable=False, server_default="0"),
        sa.Column("note_count", i, nullable=False, server_default="0"),
        sa.Column("agent_touch_count", i, nullable=False, server_default="0"),
        sa.Column("accounted_time", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("ai_triaged", sa.Boolean(), nullable=True),
        sa.Column("ai_triage_queue_applied", sa.Boolean(), nullable=True),
        sa.Column("ai_auto_replied", sa.Boolean(), nullable=True),
        sa.Column("ai_draft_used", sa.Boolean(), nullable=True),
        sa.Column("ai_handed_off", sa.Boolean(), nullable=True),
        sa.Column("process_entity_id", sa.String(50), nullable=True),
        sa.Column("last_event_at", sa.DateTime(), nullable=False),
        sa.Column("last_history_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fact_version", sa.SmallInteger(), nullable=False),
        sa.Column("built_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index(
        "ix_tiqora_report_fact_queue_created",
        "tiqora_report_ticket_fact",
        ["queue_id", "created_at"],
    )
    op.create_index("ix_tiqora_report_fact_created", "tiqora_report_ticket_fact", ["created_at"])
    op.create_index("ix_tiqora_report_fact_closed", "tiqora_report_ticket_fact", ["closed_at"])
    op.create_index(
        "ix_tiqora_report_fact_customer_created",
        "tiqora_report_ticket_fact",
        ["customer_id", "created_at"],
    )
    op.create_index("ix_tiqora_report_fact_owner", "tiqora_report_ticket_fact", ["owner_id"])
    op.create_index(
        "ix_tiqora_report_fact_state_type", "tiqora_report_ticket_fact", ["state_type"]
    )
    op.create_index(
        "ix_tiqora_report_fact_last_event", "tiqora_report_ticket_fact", ["last_event_at"]
    )
    op.create_index(
        "ix_tiqora_report_fact_version", "tiqora_report_ticket_fact", ["fact_version"]
    )

    op.create_table(
        "tiqora_report_ticket_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("history_id", sa.BigInteger(), nullable=True),
        sa.Column("article_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", i, nullable=True),
        sa.Column("from_id", i, nullable=True),
        sa.Column("to_id", i, nullable=True),
        sa.Column("from_value", sa.String(100), nullable=True),
        sa.Column("to_value", sa.String(100), nullable=True),
        sa.Column("from_duration_seconds", sa.BigInteger(), nullable=True),
        sa.Column("queue_id", i, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_report_event_ticket", "tiqora_report_ticket_event", ["ticket_id"])
    op.create_index(
        "ix_tiqora_report_event_kind_time", "tiqora_report_ticket_event", ["kind", "occurred_at"]
    )
    op.create_index(
        "ix_tiqora_report_event_actor_time",
        "tiqora_report_ticket_event",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_tiqora_report_event_queue_time",
        "tiqora_report_ticket_event",
        ["queue_id", "occurred_at"],
    )

    op.create_table(
        "tiqora_report_daily_snapshot",
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("queue_id", i, nullable=False),
        sa.Column("state_type", sa.String(30), nullable=False),
        sa.Column("priority_id", sa.SmallInteger(), nullable=False),
        sa.Column("open_count", i, nullable=False),
        sa.Column("age_0_1d", i, nullable=False),
        sa.Column("age_1_3d", i, nullable=False),
        sa.Column("age_3_7d", i, nullable=False),
        sa.Column("age_7_30d", i, nullable=False),
        sa.Column("age_30d_plus", i, nullable=False),
        sa.Column("built_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_date", "queue_id", "state_type", "priority_id"),
    )
    op.create_index(
        "ix_tiqora_report_snapshot_queue_date",
        "tiqora_report_daily_snapshot",
        ["queue_id", "snapshot_date"],
    )

    op.create_table(
        "tiqora_report_state",
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("last_history_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "gap_ids", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb"), nullable=True
        ),
        sa.Column("last_tick_at", sa.DateTime(), nullable=True),
        sa.Column("last_orphan_sweep_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("backfill_job_id", sa.BigInteger(), nullable=True),
        sa.Column("backfill_start_ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("backfill_cursor_ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("backfill_target_ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("backfill_since", sa.Date(), nullable=True),
        sa.Column("backfill_started_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_snapshot_date", sa.Date(), nullable=True),
        sa.Column("snapshot_time_zone", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "tiqora_report_dirty_ticket",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_report_dirty_ticket")
    op.drop_table("tiqora_report_state")
    op.drop_index("ix_tiqora_report_snapshot_queue_date", table_name="tiqora_report_daily_snapshot")
    op.drop_table("tiqora_report_daily_snapshot")
    for name in (
        "ix_tiqora_report_event_queue_time",
        "ix_tiqora_report_event_actor_time",
        "ix_tiqora_report_event_kind_time",
        "ix_tiqora_report_event_ticket",
    ):
        op.drop_index(name, table_name="tiqora_report_ticket_event")
    op.drop_table("tiqora_report_ticket_event")
    for name in (
        "ix_tiqora_report_fact_version",
        "ix_tiqora_report_fact_last_event",
        "ix_tiqora_report_fact_state_type",
        "ix_tiqora_report_fact_owner",
        "ix_tiqora_report_fact_customer_created",
        "ix_tiqora_report_fact_closed",
        "ix_tiqora_report_fact_created",
        "ix_tiqora_report_fact_queue_created",
    ):
        op.drop_index(name, table_name="tiqora_report_ticket_fact")
    op.drop_table("tiqora_report_ticket_fact")
