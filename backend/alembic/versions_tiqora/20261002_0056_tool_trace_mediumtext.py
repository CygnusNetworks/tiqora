"""Widen the AI tool-trace columns to ``MEDIUMTEXT`` on MariaDB.

Revision ID: 20261002_0056
Revises: 20261002_0055
Create Date: 2026-10-02

``tiqora_ai_draft.tool_trace_json`` and ``tiqora_ai_article_origin.tool_trace_json``
were plain ``TEXT`` — 64 KiB on MariaDB — so every tool result in a trace was
cut to 4,000 characters to fit. A single netadmin ``diagnose_connection``
result is ~18,000 characters: agents saw it cut off mid-object, and the UI
could not render the rest as JSON. PostgreSQL ``TEXT`` is unbounded; nothing
changes there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261002_0056"
down_revision: str | None = "20261002_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tiqora_ai_draft", "tiqora_ai_article_origin")


def upgrade() -> None:
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for table in _TABLES:
        op.alter_column(
            table,
            "tool_trace_json",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Rows longer than 64 KiB would be rejected (or cut) by the narrower type;
    # only the shape is restored, which is what a downgrade can promise.
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for table in _TABLES:
        op.alter_column(
            table,
            "tool_trace_json",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=True,
        )
