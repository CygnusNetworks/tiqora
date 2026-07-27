"""Add optional scopes column to tiqora_api_key.

Revision ID: 20260727_0026
Revises: 20260724_0025
Create Date: 2026-07-27

Optional blast-radius reduction for automation keys: comma-separated scope
tokens (read, write, mcp). NULL/empty means unrestricted (legacy behaviour).
Only ``tiqora_*`` tables are touched (parallel operation — zero Znuny DDL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0026"
down_revision: str | None = "20260724_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tiqora_api_key", sa.Column("scopes", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("tiqora_api_key", "scopes")
