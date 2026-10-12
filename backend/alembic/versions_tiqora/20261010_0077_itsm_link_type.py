"""ITSM link types: tiqora_itsm_link_type (wave-4 step 0).

Revision ID: 20261010_0077
Revises: 20261010_0073
Create Date: 2026-10-12

One table shared by the ITSM core (Mode A, link-type admin) and the Tiqora CMDB (Mode B,
``tiqora_itsm_link.link_type`` references ``name``). ``name`` is the stable key and never
changes after insert. ``origin`` is ``znuny`` (defined in Znuny SysConfig
``LinkObject::Type###<name>``) or ``tiqora``. ``propagation`` follows Znuny's
``ITSM::Core::IncidentLinkTypeDirection``: ``source`` = the SOURCE end gets the warning when
the TARGET is in incident. No seed rows (``tiqora.itsm.link_type_defaults`` inserts them).
Timestamps are written by the application as naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0077"
down_revision: str | None = "20261010_0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_itsm_link_type",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("source_label", sa.String(100), nullable=True),
        sa.Column("target_label", sa.String(100), nullable=True),
        sa.Column("pointed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("propagation", sa.String(10), nullable=False, server_default="none"),
        sa.Column("offer_ticket_ticket", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offer_ticket_ci", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offer_ci_ci", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offer_ci_service", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("create_time", sa.DateTime(), nullable=False),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_itsm_link_type_name"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_itsm_link_type")
