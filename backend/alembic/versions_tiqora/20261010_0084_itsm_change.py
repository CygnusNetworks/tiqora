"""ITSM change management: changes, tasks, templates, risk, freeze windows, CAB, legacy archive.

Revision ID: 20261010_0084
Revises: 20261010_0083
Create Date: 2026-10-27

Seventeen tables. No foreign keys to Znuny tables: ``ticket_id``, ``service_id``, ``*_by``,
``calendar_appointment_id`` and ``approval_id`` reference other rows by value; FKs only join
``tiqora_*`` tables. CAB members use the approval engine's approver vocabulary (kind + id as
text). Free text is MEDIUMTEXT on MariaDB. Timestamps are naive UTC written by the app.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")

revision: str = "20261010_0084"
down_revision: str | None = "20261010_0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# children before parents
_TABLES = (
    "tiqora_itsm_legacy_erased",
    "tiqora_itsm_legacy_change_ref",
    "tiqora_itsm_legacy_change",
    "tiqora_itsm_change_event",
    "tiqora_itsm_cab_agenda_item",
    "tiqora_itsm_cab_meeting",
    "tiqora_itsm_change_freeze_service",
    "tiqora_itsm_change_freeze",
    "tiqora_itsm_change_risk_answer",
    "tiqora_itsm_change_risk_question",
    "tiqora_itsm_change_task",
    "tiqora_itsm_change_impact",
    "tiqora_itsm_change_service",
    "tiqora_itsm_change",
    "tiqora_itsm_cab_member",
    "tiqora_itsm_cab",
    "tiqora_itsm_change_template",
)


def _audit(updated: bool = True) -> list[sa.Column]:
    cols = [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
    ]
    if updated:
        cols += [
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=False),
        ]
    return cols


def upgrade() -> None:
    op.create_table(
        "tiqora_itsm_change_template",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("change_type", sa.String(10), nullable=False),
        sa.Column("is_standard", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("defaults", MEDIUM_TEXT, nullable=False),
        sa.Column("tasks", MEDIUM_TEXT, nullable=False),
        *_audit(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_itsm_change_template_name"),
    )

    op.create_table(
        "tiqora_itsm_cab",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rule", sa.String(10), nullable=False),
        sa.Column("quorum", sa.Integer(), nullable=True),
        sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_itsm_cab_name"),
    )

    op.create_table(
        "tiqora_itsm_cab_member",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cab_id", sa.Integer(), nullable=False),
        sa.Column("member_kind", sa.String(16), nullable=False),
        sa.Column("member_ref", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["cab_id"],
            ["tiqora_itsm_cab.id"],
            name="fk_tiqora_itsm_cab_member_cab",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cab_id", "member_kind", "member_ref", name="uq_tiqora_itsm_cab_member"
        ),
    )

    op.create_table(
        "tiqora_itsm_change",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("change_type", sa.String(10), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("cab_id", sa.Integer(), nullable=True),
        sa.Column("planned_start", sa.DateTime(), nullable=True),
        sa.Column("planned_end", sa.DateTime(), nullable=True),
        sa.Column("actual_start", sa.DateTime(), nullable=True),
        sa.Column("actual_end", sa.DateTime(), nullable=True),
        sa.Column("reason", MEDIUM_TEXT, nullable=True),
        sa.Column("implementation_plan", MEDIUM_TEXT, nullable=True),
        sa.Column("backout_plan", MEDIUM_TEXT, nullable=True),
        sa.Column("test_plan", MEDIUM_TEXT, nullable=True),
        sa.Column("pir_notes", MEDIUM_TEXT, nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("risk_findings", MEDIUM_TEXT, nullable=True),
        sa.Column("risk_evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("risk_override_level", sa.String(10), nullable=True),
        sa.Column("risk_override_reason", sa.String(1000), nullable=True),
        sa.Column("risk_override_by", sa.Integer(), nullable=True),
        sa.Column("approval_via", sa.String(10), nullable=True),
        sa.Column("approval_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approval_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("impact_requested_at", sa.DateTime(), nullable=True),
        sa.Column("impact_computed_at", sa.DateTime(), nullable=True),
        sa.Column("impact_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("portal_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calendar_appointment_id", sa.BigInteger(), nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["tiqora_itsm_change_template.id"],
            name="fk_tiqora_itsm_change_template",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cab_id"],
            ["tiqora_itsm_cab.id"],
            name="fk_tiqora_itsm_change_cab",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index("ix_tiqora_itsm_change_phase", "tiqora_itsm_change", ["phase"])
    op.create_index(
        "ix_tiqora_itsm_change_window", "tiqora_itsm_change", ["planned_start", "planned_end"]
    )

    op.create_table(
        "tiqora_itsm_change_service",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("service_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_change_service_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id", "service_id"),
    )

    op.create_table(
        "tiqora_itsm_change_impact",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("ref_key", sa.String(100), nullable=False),
        sa.Column("direct", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_change_impact_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id", "kind", "ref_key"),
    )
    op.create_index(
        "ix_tiqora_itsm_change_impact_ref", "tiqora_itsm_change_impact", ["kind", "ref_key"]
    )

    op.create_table(
        "tiqora_itsm_change_task",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("change_ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("task_kind", sa.String(12), nullable=False),
        sa.Column("planned_start", sa.DateTime(), nullable=True),
        sa.Column("planned_end", sa.DateTime(), nullable=True),
        sa.Column("actual_start", sa.DateTime(), nullable=True),
        sa.Column("actual_end", sa.DateTime(), nullable=True),
        sa.Column("planned_effort_minutes", sa.Integer(), nullable=True),
        sa.Column("template_task_key", sa.String(50), nullable=True),
        *_audit(updated=False),
        sa.ForeignKeyConstraint(
            ["change_ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_change_task_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id"),
        sa.UniqueConstraint(
            "change_ticket_id", "template_task_key", name="uq_tiqora_itsm_change_task_tpl"
        ),
    )
    op.create_index(
        "ix_tiqora_itsm_change_task_change",
        "tiqora_itsm_change_task",
        ["change_ticket_id", "position"],
    )

    op.create_table(
        "tiqora_itsm_change_risk_question",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_key", sa.String(50), nullable=False),
        sa.Column("question", sa.String(500), nullable=False),
        sa.Column("help", sa.Text(), nullable=True),
        sa.Column("answers", MEDIUM_TEXT, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_key", name="uq_tiqora_itsm_change_risk_q_key"),
    )

    op.create_table(
        "tiqora_itsm_change_risk_answer",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("question_key", sa.String(50), nullable=False),
        sa.Column("answer_key", sa.String(50), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=False),
        sa.Column("answered_by", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_change_risk_answer_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_id", "question_key"),
    )

    op.create_table(
        "tiqora_itsm_change_freeze",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("enforcement", sa.String(5), nullable=False),
        sa.Column("allow_emergency", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("recur_type", sa.String(10), nullable=True),
        sa.Column("recur_interval", sa.Integer(), nullable=True),
        sa.Column("recur_count", sa.Integer(), nullable=True),
        sa.Column("recur_until", sa.DateTime(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_itsm_change_freeze_start", "tiqora_itsm_change_freeze", ["starts_at"]
    )

    op.create_table(
        "tiqora_itsm_change_freeze_service",
        sa.Column("freeze_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("service_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["freeze_id"],
            ["tiqora_itsm_change_freeze.id"],
            name="fk_tiqora_itsm_change_freeze_service_freeze",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("freeze_id", "service_id"),
    )

    op.create_table(
        "tiqora_itsm_cab_meeting",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cab_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("minutes", MEDIUM_TEXT, nullable=True),
        *_audit(),
        sa.ForeignKeyConstraint(
            ["cab_id"],
            ["tiqora_itsm_cab.id"],
            name="fk_tiqora_itsm_cab_meeting_cab",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_itsm_cab_meeting_start", "tiqora_itsm_cab_meeting", ["starts_at"])

    op.create_table(
        "tiqora_itsm_cab_agenda_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.BigInteger(), nullable=False),
        sa.Column("change_ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["tiqora_itsm_cab_meeting.id"],
            name="fk_tiqora_itsm_cab_agenda_meeting",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["change_ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_cab_agenda_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "change_ticket_id", name="uq_tiqora_itsm_cab_agenda"),
    )

    op.create_table(
        "tiqora_itsm_change_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        *_audit(updated=False),
        sa.Column("event", sa.String(30), nullable=False),
        sa.Column("from_phase", sa.String(20), nullable=True),
        sa.Column("to_phase", sa.String(20), nullable=True),
        sa.Column("detail", MEDIUM_TEXT, nullable=True),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tiqora_itsm_change.ticket_id"],
            name="fk_tiqora_itsm_change_event_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_itsm_change_event_ticket", "tiqora_itsm_change_event", ["ticket_id", "id"]
    )
    op.create_index(
        "ix_tiqora_itsm_change_event_time", "tiqora_itsm_change_event", ["event", "created_at"]
    )

    op.create_table(
        "tiqora_itsm_legacy_change",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("znuny_change_id", sa.BigInteger(), nullable=False),
        sa.Column("change_number", sa.String(100), nullable=False),
        sa.Column("title", sa.String(250), nullable=True),
        sa.Column("state", sa.String(200), nullable=True),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("impact", sa.String(200), nullable=True),
        sa.Column("priority", sa.String(200), nullable=True),
        sa.Column("manager_login", sa.String(200), nullable=True),
        sa.Column("builder_login", sa.String(200), nullable=True),
        sa.Column("requested_time", sa.DateTime(), nullable=True),
        sa.Column("planned_start", sa.DateTime(), nullable=True),
        sa.Column("planned_end", sa.DateTime(), nullable=True),
        sa.Column("actual_start", sa.DateTime(), nullable=True),
        sa.Column("actual_end", sa.DateTime(), nullable=True),
        sa.Column("znuny_created_at", sa.DateTime(), nullable=False),
        sa.Column("znuny_changed_at", sa.DateTime(), nullable=False),
        sa.Column("workorder_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", MEDIUM_TEXT, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("znuny_change_id", name="uq_tiqora_itsm_legacy_change_znuny"),
    )
    op.create_index(
        "ix_tiqora_itsm_legacy_change_number", "tiqora_itsm_legacy_change", ["change_number"]
    )
    op.create_index(
        "ix_tiqora_itsm_legacy_change_planned", "tiqora_itsm_legacy_change", ["planned_start"]
    )

    op.create_table(
        "tiqora_itsm_legacy_change_ref",
        sa.Column("legacy_change_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ref_kind", sa.String(20), nullable=False),
        sa.Column("ref_key", sa.String(200), nullable=False),
        sa.Column(
            "workorder_number",
            sa.Integer(),
            autoincrement=False,
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_change_id"],
            ["tiqora_itsm_legacy_change.id"],
            name="fk_tiqora_itsm_legacy_change_ref_change",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("legacy_change_id", "ref_kind", "ref_key", "workorder_number"),
    )
    op.create_index(
        "ix_tiqora_itsm_legacy_change_ref",
        "tiqora_itsm_legacy_change_ref",
        ["ref_kind", "ref_key"],
    )

    op.create_table(
        "tiqora_itsm_legacy_erased",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("identifier_hash", sa.CHAR(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier_hash", name="uq_tiqora_itsm_legacy_erased_hash"),
    )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_table(table)
