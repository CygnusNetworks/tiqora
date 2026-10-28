"""Shared approval engine: tiqora_approval* (ten tables).

Revision ID: 20261010_0083
Revises: 20261010_0082
Create Date: 2026-10-15

Request (``tiqora_approval``), steps, resolved approvers, votes, e-mail link tokens, audit
events, templates, approver lists (+ members) and standing delegations. No foreign keys to
Znuny tables: ``ticket_id``, user ids and customer logins reference Znuny rows by value. The
child tables of a request carry a denormalised ``ticket_id`` (GDPR erasure keys on it).
Principal columns are 250 wide (``customer:`` + Znuny's 200-character login). Free text is
MEDIUMTEXT on MariaDB. Timestamps are naive UTC written by the app.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")

revision: str = "20261010_0083"
down_revision: str | None = "20261010_0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# children before parents
_TABLES = (
    "tiqora_approval_event",
    "tiqora_approval_token",
    "tiqora_approval_vote",
    "tiqora_approval_approver",
    "tiqora_approval_step",
    "tiqora_approval",
    "tiqora_approval_delegation",
    "tiqora_approval_list_member",
    "tiqora_approval_list",
    "tiqora_approval_template",
)


def upgrade() -> None:
    op.create_table(
        "tiqora_approval_template",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", MEDIUM_TEXT, nullable=True),
        sa.Column("subject_types", sa.String(500), nullable=False, server_default="[]"),
        sa.Column("definition", MEDIUM_TEXT, nullable=False),
        sa.Column("origin", sa.String(16), nullable=False, server_default="admin"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key", name="uq_tiqora_approval_template_key"),
    )

    op.create_table(
        "tiqora_approval_list",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(190), nullable=False),
        sa.Column("description", MEDIUM_TEXT, nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_approval_list_name"),
    )

    op.create_table(
        "tiqora_approval_list_member",
        sa.Column("list_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["tiqora_approval_list.id"],
            name="fk_tiqora_approval_list_member_list",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("list_id", "user_id"),
    )

    op.create_table(
        "tiqora_approval_delegation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("substitute_user_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_approval_delegation_sub", "tiqora_approval_delegation", ["substitute_user_id"]
    )
    op.create_index("ix_tiqora_approval_delegation_user", "tiqora_approval_delegation", ["user_id"])

    op.create_table(
        "tiqora_approval",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_ref", sa.String(190), nullable=True),
        sa.Column("approval_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("description", MEDIUM_TEXT, nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("current_stage", sa.Integer(), nullable=True),
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requester", sa.String(250), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["tiqora_approval_template.id"],
            name="fk_tiqora_approval_template",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_approval_ticket_key", "tiqora_approval", ["ticket_id", "approval_key", "id"]
    )
    op.create_index("ix_tiqora_approval_status", "tiqora_approval", ["status", "id"])

    op.create_table(
        "tiqora_approval_step",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("rule", sa.String(10), nullable=False),
        sa.Column("quorum", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("spec", MEDIUM_TEXT, nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("last_reminder_at", sa.DateTime(), nullable=True),
        sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overdue_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["tiqora_approval.id"],
            name="fk_tiqora_approval_step_approval",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id", "position", name="uq_tiqora_approval_step_pos"),
    )
    op.create_index("ix_tiqora_approval_step_due", "tiqora_approval_step", ["status", "due_at"])
    op.create_index("ix_tiqora_approval_step_ticket", "tiqora_approval_step", ["ticket_id"])

    op.create_table(
        "tiqora_approval_approver",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("principal", sa.String(250), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("customer_login", sa.String(200), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(190), nullable=True),
        sa.Column("source_label", sa.String(200), nullable=True),
        sa.Column("decisive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["tiqora_approval.id"],
            name="fk_tiqora_approval_approver_approval",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["tiqora_approval_step.id"],
            name="fk_tiqora_approval_approver_step",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "principal", name="uq_tiqora_approval_approver"),
    )
    op.create_index(
        "ix_tiqora_approval_approver_inbox", "tiqora_approval_approver", ["principal", "status"]
    )
    op.create_index(
        "ix_tiqora_approval_approver_user", "tiqora_approval_approver", ["user_id", "status"]
    )
    op.create_index("ix_tiqora_approval_approver_ticket", "tiqora_approval_approver", ["ticket_id"])
    op.create_index(
        "ix_tiqora_approval_approver_status", "tiqora_approval_approver", ["status", "approval_id"]
    )

    op.create_table(
        "tiqora_approval_vote",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=False),
        sa.Column("approver_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("comment", MEDIUM_TEXT, nullable=True),
        sa.Column("decided_by", sa.String(250), nullable=False),
        sa.Column("on_behalf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["tiqora_approval.id"],
            name="fk_tiqora_approval_vote_approval",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["tiqora_approval_step.id"],
            name="fk_tiqora_approval_vote_step",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["tiqora_approval_approver.id"],
            name="fk_tiqora_approval_vote_approver",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approver_id", name="uq_tiqora_approval_vote_approver"),
    )
    op.create_index("ix_tiqora_approval_vote_ticket", "tiqora_approval_vote", ["ticket_id"])

    op.create_table(
        "tiqora_approval_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("approver_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["tiqora_approval.id"],
            name="fk_tiqora_approval_token_approval",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["tiqora_approval_approver.id"],
            name="fk_tiqora_approval_token_approver",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_tiqora_approval_token_hash"),
    )
    op.create_index("ix_tiqora_approval_token_approver", "tiqora_approval_token", ["approver_id"])
    op.create_index("ix_tiqora_approval_token_ticket", "tiqora_approval_token", ["ticket_id"])

    op.create_table(
        "tiqora_approval_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(250), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=True),
        sa.Column("approver_id", sa.BigInteger(), nullable=True),
        sa.Column("channel", sa.String(10), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("detail", MEDIUM_TEXT, nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["tiqora_approval.id"],
            name="fk_tiqora_approval_event_approval",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_approval_event_approval", "tiqora_approval_event", ["approval_id", "id"]
    )
    op.create_index("ix_tiqora_approval_event_ticket", "tiqora_approval_event", ["ticket_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_table(table)
