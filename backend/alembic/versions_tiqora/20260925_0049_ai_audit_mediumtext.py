"""Widen the AI audit payload columns to ``MEDIUMTEXT`` on MariaDB.

Revision ID: 20260925_0049
Revises: 20260924_0048
Create Date: 2026-09-25

``tiqora_ai_audit_log.request_json``/``response_json``/``pii_map_enc`` were
plain ``TEXT`` — 64 KiB on MariaDB. A tool run re-sends its whole grown
conversation every round, so from a few rounds on every audit INSERT failed
with ``Data too long`` and was silently dropped (in production, a
twelve-round run left no row at all). PostgreSQL ``TEXT`` is unbounded;
nothing changes there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260925_0049"
down_revision: str | None = "20260924_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("request_json", False),
    ("response_json", True),
    ("pii_map_enc", True),
)


def upgrade() -> None:
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for name, nullable in _COLUMNS:
        op.alter_column(
            "tiqora_ai_audit_log",
            name,
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    # Rows longer than 64 KiB would be rejected (or cut) by the narrower type;
    # only the shape is restored, which is what a downgrade can promise.
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for name, nullable in _COLUMNS:
        op.alter_column(
            "tiqora_ai_audit_log",
            name,
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=nullable,
        )
