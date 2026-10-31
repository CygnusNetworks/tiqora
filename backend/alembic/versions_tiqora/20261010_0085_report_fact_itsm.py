"""ITSM statistics: first_level_solved and itsm_solution_working_minutes on the ticket facts.

Revision ID: 20261010_0085
Revises: 20261010_0084
Create Date: 2026-10-26

Both columns are nullable and stay NULL until the facts loop rebuilds the row
(``FACT_VERSION`` 3), so the ITSM measures skip not yet rebuilt tickets.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0085"
down_revision: str | None = "20261010_0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_report_ticket_fact", sa.Column("first_level_solved", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "tiqora_report_ticket_fact",
        sa.Column("itsm_solution_working_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # Without this, version 3 rows would come back with empty columns on the way forward, and
    # the upgrade (fact_version < 3) would never fill them.
    op.execute("UPDATE tiqora_report_ticket_fact SET fact_version = 2 WHERE fact_version > 2")
    op.drop_column("tiqora_report_ticket_fact", "itsm_solution_working_minutes")
    op.drop_column("tiqora_report_ticket_fact", "first_level_solved")
