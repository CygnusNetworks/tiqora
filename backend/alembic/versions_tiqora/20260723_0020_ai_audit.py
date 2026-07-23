"""Create tiqora_ai_audit_log table.

Revision ID: 20260723_0020
Revises: 20260723_0019
Create Date: 2026-07-23

LLM-Request-Audit (see ``tiqora.ai.audit`` / ``tiqora.ai.models.TiqoraAiAuditLog``):
one row per ``chat()`` call made through :class:`tiqora.ai.audit.AuditingLlmClient`
(main agent/summary model + vision pre-pass + provider connection test),
success or failure.

``request_json``/``response_json`` are plain ``TEXT`` (not MySQL ``LONGTEXT``)
to stay portable across the MariaDB/PostgreSQL test fixtures, matching the
existing ``tiqora_ai_*`` JSON-as-Text convention (see ``tiqora.ai.models``
module docstring) — image bytes are stripped before the row is built.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0020"
down_revision: str | None = "20260723_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_ai_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("provider_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("feature", sa.String(30), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_id", sa.Integer(), nullable=True),
        sa.Column("acting_user_id", sa.Integer(), nullable=True),
        sa.Column("trigger", sa.String(30), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("pii_map_enc", sa.Text(), nullable=True),
        sa.Column("pii_counts_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["tiqora_llm_provider.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_tiqora_ai_audit_log_ts", "tiqora_ai_audit_log", ["ts"])
    op.create_index("ix_tiqora_ai_audit_log_run_id", "tiqora_ai_audit_log", ["run_id"])
    op.create_index(
        "ix_tiqora_ai_audit_log_ticket_ts", "tiqora_ai_audit_log", ["ticket_id", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_ai_audit_log_ticket_ts", table_name="tiqora_ai_audit_log")
    op.drop_index("ix_tiqora_ai_audit_log_run_id", table_name="tiqora_ai_audit_log")
    op.drop_index("ix_tiqora_ai_audit_log_ts", table_name="tiqora_ai_audit_log")
    op.drop_table("tiqora_ai_audit_log")
