"""Add cache_type to tiqora_cache_invalidation; make ticket_id nullable.

Revision ID: 20260720_0011
Revises: 20260720_0010
Create Date: 2026-07-20

Master-data admin edits (queues, states, users, templates, …) need to signal
Znuny CacheType cleanups, not just ticket-level invalidation. A row is now
either a ticket signal (ticket_id set, cache_type NULL) or a cache-type signal
(cache_type set, ticket_id NULL).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0011"
down_revision: str | None = "20260720_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tiqora_cache_invalidation",
        sa.Column("cache_type", sa.String(100), nullable=True),
    )
    op.alter_column(
        "tiqora_cache_invalidation",
        "ticket_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    # Rows with NULL ticket_id cannot survive NOT NULL; drop them first.
    op.execute("DELETE FROM tiqora_cache_invalidation WHERE ticket_id IS NULL")
    op.alter_column(
        "tiqora_cache_invalidation",
        "ticket_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_column("tiqora_cache_invalidation", "cache_type")
