"""Add expires_at, last_used_at, created_by to tiqora_api_key.

Revision ID: 20260721_0013
Revises: 20260720_0012
Create Date: 2026-07-21

Lifecycle metadata for API keys (hard expiry, last-used stamp, issuer).
All columns nullable so the migration applies cleanly to the live prod table.
Only ``tiqora_*`` tables are touched (parallel operation — zero Znuny DDL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0013"
down_revision: str | None = "20260720_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tiqora_api_key", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.add_column("tiqora_api_key", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.add_column("tiqora_api_key", sa.Column("created_by", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_api_key", "created_by")
    op.drop_column("tiqora_api_key", "last_used_at")
    op.drop_column("tiqora_api_key", "expires_at")
