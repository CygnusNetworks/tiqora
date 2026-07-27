"""Widen tiqora_api_key.scopes for area RO/RW token lists.

Revision ID: 20260728_0028
Revises: 20260727_0027
Create Date: 2026-07-28

Area-level scopes (tickets:ro,kb:rw,…) exceed the original 200-char column
when several areas are selected. Widen to 500; still only ``tiqora_*`` DDL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0028"
down_revision: str | None = "20260727_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tiqora_api_key",
        "scopes",
        existing_type=sa.String(length=200),
        type_=sa.String(length=500),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "tiqora_api_key",
        "scopes",
        existing_type=sa.String(length=500),
        type_=sa.String(length=200),
        existing_nullable=True,
    )
