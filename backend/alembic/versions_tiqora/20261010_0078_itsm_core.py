"""ITSM core tables: priority matrix, service meta, problems, major incidents, bulk targets.

Revision ID: 20261010_0078
Revises: 20261010_0077
Create Date: 2026-10-12

``tiqora_itsm_link_type`` belongs to step 0 (``20261010_0077``). No FK to Znuny tables:
``ticket_id``, ``service_id``, ``priority_id`` and ``*_by`` reference Znuny rows by value.
FKs only between Tiqora tables: major-incident updates cascade with their major incident,
``known_error_article_id`` becomes NULL when the KB article is deleted. Timestamps are
written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261010_0078"
down_revision: str | None = "20261010_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "tiqora_itsm_priority_matrix",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("criticality", sa.String(200), nullable=False),
        sa.Column("impact", sa.String(200), nullable=False),
        sa.Column("priority_id", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("criticality", "impact", name="uq_tiqora_itsm_priority_matrix_cell"),
    )
    op.create_table(
        "tiqora_itsm_service_meta",
        sa.Column("service_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("service_type", sa.String(200), nullable=True),
        sa.Column("criticality", sa.String(200), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("portal_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("min_minutes_between_incidents", sa.Integer(), nullable=True),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("service_id"),
    )
    op.create_table(
        "tiqora_itsm_problem",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("root_cause", _MEDIUM_TEXT, nullable=True),
        sa.Column("workaround", _MEDIUM_TEXT, nullable=True),
        sa.Column("known_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("known_error_article_id", sa.Integer(), nullable=True),
        sa.Column("known_error_since", sa.DateTime(), nullable=True),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
        sa.ForeignKeyConstraint(
            ["known_error_article_id"],
            ["tiqora_kb_article.id"],
            name="fk_tiqora_itsm_problem_kb_article",
            ondelete="SET NULL",
        ),
    )
    op.create_table(
        "tiqora_itsm_major_incident",
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("declared_at", sa.DateTime(), nullable=False),
        sa.Column("declared_by", sa.Integer(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ended_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index(
        "ix_tiqora_itsm_major_incident_active", "tiqora_itsm_major_incident", ["ended_at"]
    )
    op.create_table(
        "tiqora_itsm_major_incident_update",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("body", _MEDIUM_TEXT, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tiqora_itsm_major_incident.ticket_id"],
            name="fk_tiqora_itsm_mi_update_major",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_tiqora_itsm_mi_update_ticket",
        "tiqora_itsm_major_incident_update",
        ["ticket_id", "id"],
    )
    op.create_table(
        "tiqora_itsm_bulk_target",
        sa.Column("job_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(50), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "ticket_id"),
    )


def downgrade() -> None:
    # drop_table removes the indexes too (MariaDB refuses to drop the index a FK needs)
    op.drop_table("tiqora_itsm_bulk_target")
    op.drop_table("tiqora_itsm_major_incident_update")
    op.drop_table("tiqora_itsm_major_incident")
    op.drop_table("tiqora_itsm_problem")
    op.drop_table("tiqora_itsm_service_meta")
    op.drop_table("tiqora_itsm_priority_matrix")
