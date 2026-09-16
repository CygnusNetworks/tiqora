"""Add AI triage: tiqora_ai_triage + tiqora_ai_queue_policy triage columns.

Revision ID: 20260916_0046
Revises: 20260915_0045
Create Date: 2026-09-16

Routing of a newly created ticket into the correct queue, decided once on
the ticket's first article before the reply agent runs, plus the customer
correction for forwarded mails. See ``tiqora.ai.triage``.

Both thresholds default to 100, i.e. "propose, never act": on a fresh
install nothing has calibrated them yet, so autonomy has to be turned on
per queue deliberately.

The watermark for the triage outbox consumer is **not** seeded here — that
would be non-idempotent and wrong on a fresh install. The worker seeds it
from ``MAX(tiqora_event_outbox.id)`` on its first tick instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0046"
down_revision: str | None = "20260915_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, type, nullable, server_default) — server_default must match the
# model column exactly or the metadata-diff check reports drift.
_POLICY_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object], bool, object | None], ...] = (
    ("enabled_triage", sa.Boolean(), False, sa.false()),
    ("routing_description", sa.Text(), True, None),
    ("triage_target_queue_ids", sa.Text(), True, None),
    ("triage_auto_threshold", sa.Integer(), False, "100"),
    ("triage_suggest_threshold", sa.Integer(), False, "50"),
    ("triage_samples", sa.SmallInteger(), False, "3"),
    ("triage_customer_fix_enabled", sa.Boolean(), False, sa.false()),
    ("triage_customer_fix_auto_threshold", sa.Integer(), False, "100"),
    ("triage_delay_reply", sa.Boolean(), False, sa.false()),
    ("triage_model_override", sa.String(length=200), True, None),
)


def upgrade() -> None:
    for name, type_, nullable, server_default in _POLICY_COLUMNS:
        op.add_column(
            "tiqora_ai_queue_policy",
            sa.Column(name, type_, nullable=nullable, server_default=server_default),
        )
    # Separate from the loop: carries an FK, which needs its own constraint.
    op.add_column(
        "tiqora_ai_queue_policy",
        sa.Column("triage_llm_provider_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tiqora_ai_queue_policy_triage_llm_provider",
        "tiqora_ai_queue_policy",
        "tiqora_llm_provider",
        ["triage_llm_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "tiqora_ai_triage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("source_queue_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("suggested_queue_id", sa.Integer(), nullable=True),
        sa.Column("queue_confidence", sa.Integer(), nullable=True),
        sa.Column("queue_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("queue_reason", sa.Text(), nullable=True),
        sa.Column("candidates_json", sa.Text(), nullable=True),
        sa.Column("extracted_email", sa.String(length=150), nullable=True),
        sa.Column("suggested_customer_user_id", sa.String(length=200), nullable=True),
        sa.Column("suggested_customer_id", sa.String(length=150), nullable=True),
        sa.Column("customer_confidence", sa.Integer(), nullable=True),
        sa.Column("customer_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("customer_source", sa.String(length=20), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decided_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("change_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        # Enforces "one triage per ticket" at the DB layer, which is also the
        # race guard for two workers seeing the same event.
        sa.UniqueConstraint("ticket_id", name="uq_tiqora_ai_triage_ticket"),
    )
    op.create_index(
        "ix_tiqora_ai_triage_status_created",
        "tiqora_ai_triage",
        ["status", "create_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_tiqora_ai_triage_status_created", table_name="tiqora_ai_triage")
    op.drop_table("tiqora_ai_triage")
    op.drop_constraint(
        "fk_tiqora_ai_queue_policy_triage_llm_provider",
        "tiqora_ai_queue_policy",
        type_="foreignkey",
    )
    op.drop_column("tiqora_ai_queue_policy", "triage_llm_provider_id")
    for name, _type, _nullable, _default in reversed(_POLICY_COLUMNS):
        op.drop_column("tiqora_ai_queue_policy", name)
