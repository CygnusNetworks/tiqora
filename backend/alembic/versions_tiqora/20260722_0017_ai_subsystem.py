"""Create tiqora_llm_provider, tiqora_mcp_client(+tool_policy), and
tiqora_ai_* tables for the Tiqora AI subsystem (Phase A foundation).

Revision ID: 20260722_0017
Revises: 20260721_0016
Create Date: 2026-07-22

See ``~/TIQORA_LLM_PLAN.md`` §3.1 for the full data model rationale. All
JSON-shaped columns are plain ``Text`` for MariaDB/PostgreSQL portability
(matches the existing ``tiqora_kb_*`` convention).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0017"
down_revision: str | None = "20260721_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_llm_provider",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False, server_default="openai_compat"),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(200), nullable=False),
        sa.Column("extra_json", sa.Text(), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("eu_hosted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("valid_id", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_llm_provider_name"),
    )

    op.create_table(
        "tiqora_mcp_client",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("auth_token_enc", sa.Text(), nullable=True),
        sa.Column("transport", sa.String(50), nullable=False, server_default="streamable_http"),
        sa.Column("last_discovered_at", sa.DateTime(), nullable=True),
        sa.Column("tools_json", sa.Text(), nullable=True),
        sa.Column("valid_id", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_mcp_client_name"),
    )

    op.create_table(
        "tiqora_mcp_tool_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mcp_client_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("mutating", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("description_snapshot", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["mcp_client_id"], ["tiqora_mcp_client.id"],
            name="fk_tiqora_mcp_tool_policy_client", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "mcp_client_id", "tool_name", name="uq_tiqora_mcp_tool_policy_client_tool"
        ),
    )
    op.create_index(
        "ix_tiqora_mcp_tool_policy_client", "tiqora_mcp_tool_policy", ["mcp_client_id"]
    )

    op.create_table(
        "tiqora_ai_queue_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("enabled_auto_reply", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("enabled_summary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("enabled_manual_assist", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("autonomy", sa.String(20), nullable=False, server_default="off"),
        sa.Column("service_user_id", sa.Integer(), nullable=True),
        sa.Column("llm_provider_id", sa.Integer(), nullable=True),
        sa.Column("model_override", sa.String(200), nullable=True),
        sa.Column("kb_tags", sa.Text(), nullable=True),
        sa.Column("kb_category_ids", sa.Text(), nullable=True),
        sa.Column("mcp_client_ids", sa.Text(), nullable=True),
        sa.Column("mcp_tool_overrides", sa.Text(), nullable=True),
        sa.Column("summary_article_threshold", sa.Integer(), nullable=True),
        sa.Column("summary_char_threshold", sa.Integer(), nullable=True),
        sa.Column("summary_incremental_min_articles", sa.Integer(), nullable=True),
        sa.Column("summary_incremental_min_chars", sa.Integer(), nullable=True),
        sa.Column("max_clarifications", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("max_auto_replies", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_replies_per_hour", sa.Integer(), nullable=True),
        sa.Column("budget_tokens_day", sa.Integer(), nullable=True),
        sa.Column("escalation_rules", sa.Text(), nullable=True),
        sa.Column("ai_disclosure_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("ai_disclosure_text", sa.Text(), nullable=True),
        sa.Column("pii_masking", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("identity_mode", sa.String(30), nullable=False, server_default="ticket_customer_id"),
        sa.Column("clarify_schema_json", sa.Text(), nullable=True),
        sa.Column("valid_id", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("queue_id", name="uq_tiqora_ai_queue_policy_queue_id"),
        sa.ForeignKeyConstraint(
            ["llm_provider_id"], ["tiqora_llm_provider.id"],
            name="fk_tiqora_ai_queue_policy_provider", ondelete="SET NULL",
        ),
    )

    op.create_table(
        "tiqora_ai_draft",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="reply"),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("based_on_article_id", sa.BigInteger(), nullable=True),
        sa.Column("tool_trace_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("accepted_article_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column("create_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column("change_time", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_ai_draft_ticket_status", "tiqora_ai_draft", ["ticket_id", "status"]
    )

    op.create_table(
        "tiqora_ai_article_origin",
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("service_user_id", sa.Integer(), nullable=True),
        sa.Column("created", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("article_id"),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["tiqora_ai_draft.id"],
            name="fk_tiqora_ai_article_origin_draft", ondelete="SET NULL",
        ),
    )

    op.create_table(
        "tiqora_ai_acl",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(30), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("limit_requests_day", sa.Integer(), nullable=True),
        sa.Column("limit_tokens_day", sa.Integer(), nullable=True),
        sa.Column("limit_requests_month", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_type", "subject_id", "feature", name="uq_tiqora_ai_acl_subject_feature"
        ),
    )

    op.create_table(
        "tiqora_ai_usage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("queue_id", sa.Integer(), nullable=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("feature", sa.String(30), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_hint", sa.Float(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extra_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["tiqora_llm_provider.id"],
            name="fk_tiqora_ai_usage_provider", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_tiqora_ai_usage_ts", "tiqora_ai_usage", ["ts"])
    op.create_index("ix_tiqora_ai_usage_queue_ts", "tiqora_ai_usage", ["queue_id", "ts"])

    op.create_table(
        "tiqora_ai_ticket_state",
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("last_customer_article_id", sa.BigInteger(), nullable=True),
        sa.Column("run_lock_owner", sa.String(200), nullable=True),
        sa.Column("run_lock_at", sa.DateTime(), nullable=True),
        sa.Column("summary_body", sa.Text(), nullable=True),
        sa.Column("last_summary_upto_article_id", sa.BigInteger(), nullable=True),
        sa.Column("last_summary_hash", sa.String(64), nullable=True),
        sa.Column("auto_reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clarification_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("ticket_id"),
    )


def downgrade() -> None:
    op.drop_table("tiqora_ai_ticket_state")
    op.drop_index("ix_tiqora_ai_usage_queue_ts", table_name="tiqora_ai_usage")
    op.drop_index("ix_tiqora_ai_usage_ts", table_name="tiqora_ai_usage")
    op.drop_table("tiqora_ai_usage")
    op.drop_table("tiqora_ai_acl")
    op.drop_table("tiqora_ai_article_origin")
    op.drop_index("ix_tiqora_ai_draft_ticket_status", table_name="tiqora_ai_draft")
    op.drop_table("tiqora_ai_draft")
    op.drop_table("tiqora_ai_queue_policy")
    op.drop_index("ix_tiqora_mcp_tool_policy_client", table_name="tiqora_mcp_tool_policy")
    op.drop_table("tiqora_mcp_tool_policy")
    op.drop_table("tiqora_mcp_client")
    op.drop_table("tiqora_llm_provider")
