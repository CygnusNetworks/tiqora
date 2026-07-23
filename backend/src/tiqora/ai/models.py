"""SQLAlchemy models for the ``tiqora_ai_*`` / ``tiqora_llm_*`` / ``tiqora_mcp_*``
tables (Tiqora AI subsystem, see ``~/TIQORA_LLM_PLAN.md`` §3.1).

Mirrors the style of ``tiqora.kb.models``: same declarative base
(:class:`tiqora.db.tiqora.base.TiqoraBase`), same Alembic-managed metadata,
lives in its own package for module cohesion. Migration:
``alembic/versions_tiqora/20260722_0017_ai_subsystem.py``.

JSON-shaped columns are stored as ``Text`` (not a dialect JSON type) to stay
portable across the MariaDB and PostgreSQL test fixtures and match the
existing ``tiqora_kb_*`` / ``tiqora_form_draft`` convention in this repo.

Notes on things deliberately *not* enforced at the DB layer (v1):

- ``tiqora_ai_queue_policy.queue_id`` references the legacy ``queue`` table
  by plain integer column, not a DB foreign key — that table lives outside
  ``tiqora_metadata`` (Alembic here only owns ``tiqora_*`` tables).
- "Max one open draft per (ticket_id, based_on_article_id, kind)" (plan
  §3.1) is enforced in the DraftService (Phase B), not via a partial unique
  index — MariaDB has no partial/filtered index support.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase

# tiqora_llm_provider.kind
PROVIDER_KIND_OPENAI_COMPAT = "openai_compat"
PROVIDER_KIND_ANTHROPIC = "anthropic"
PROVIDER_KINDS = frozenset({PROVIDER_KIND_OPENAI_COMPAT, PROVIDER_KIND_ANTHROPIC})

# tiqora_mcp_client.transport
MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"

# tiqora_ai_queue_policy.autonomy
AUTONOMY_OFF = "off"
AUTONOMY_CLARIFY_ONLY = "clarify_only"
AUTONOMY_FULL = "full"
AUTONOMY_MODES = frozenset({AUTONOMY_OFF, AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL})

# tiqora_ai_queue_policy.identity_mode
IDENTITY_TICKET_CUSTOMER_ID = "ticket_customer_id"
IDENTITY_CLARIFY_SCHEMA = "clarify_schema"
IDENTITY_OFF = "off"
IDENTITY_MODES = frozenset({IDENTITY_TICKET_CUSTOMER_ID, IDENTITY_CLARIFY_SCHEMA, IDENTITY_OFF})

# tiqora_ai_draft.kind
DRAFT_KIND_REPLY = "reply"
DRAFT_KIND_CLARIFY = "clarify"
DRAFT_KINDS = frozenset({DRAFT_KIND_REPLY, DRAFT_KIND_CLARIFY})

# tiqora_ai_draft.status
DRAFT_STATUS_OPEN = "open"
DRAFT_STATUS_ACCEPTED = "accepted"
DRAFT_STATUS_DISCARDED = "discarded"
DRAFT_STATUS_SUPERSEDED = "superseded"
DRAFT_STATUSES = frozenset(
    {DRAFT_STATUS_OPEN, DRAFT_STATUS_ACCEPTED, DRAFT_STATUS_DISCARDED, DRAFT_STATUS_SUPERSEDED}
)

# tiqora_ai_draft.source / tiqora_ai_article_origin.source
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
ARTICLE_ORIGIN_SOURCES = frozenset({SOURCE_AUTO, "manual_accept"})

# tiqora_ai_acl.subject_type
ACL_SUBJECT_GROUP = "group"
ACL_SUBJECT_ROLE = "role"
ACL_SUBJECT_USER = "user"
ACL_SUBJECT_TYPES = frozenset({ACL_SUBJECT_GROUP, ACL_SUBJECT_ROLE, ACL_SUBJECT_USER})

# tiqora_ai_acl.feature / tiqora_ai_usage.feature
FEATURE_SUMMARY = "summary"
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_MANUAL_ASSIST = "manual_assist"
FEATURE_MCP = "mcp"
AI_FEATURES = frozenset({FEATURE_SUMMARY, FEATURE_AUTO_REPLY, FEATURE_MANUAL_ASSIST, FEATURE_MCP})


class TiqoraLlmProvider(TiqoraBase):
    """A configured LLM backend (OpenAI-compatible first, plan §3.2)."""

    __tablename__ = "tiqora_llm_provider"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(
        String(50), nullable=False, default=PROVIDER_KIND_OPENAI_COMPAT
    )
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str] = mapped_column(String(200), nullable=False)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    supports_tools: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    supports_streaming: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    eu_hosted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Vision (image-description) capability — a separate pre-pass call, never
    # the main agent/summary model (see tiqora.ai.vision module docstring).
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    valid_id: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TiqoraMcpClient(TiqoraBase):
    """A registered external MCP server (plan §3.3)."""

    __tablename__ = "tiqora_mcp_client"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    auth_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(
        String(50), nullable=False, default=MCP_TRANSPORT_STREAMABLE_HTTP
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_id: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TiqoraMcpToolPolicy(TiqoraBase):
    """Admin allow/deny + read-only/mutating classification for one MCP tool."""

    __tablename__ = "tiqora_mcp_tool_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    mcp_client_id: Mapped[int] = mapped_column(
        ForeignKey("tiqora_mcp_client.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(300), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    mutating: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    description_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "mcp_client_id", "tool_name", name="uq_tiqora_mcp_tool_policy_client_tool"
        ),
        Index("ix_tiqora_mcp_tool_policy_client", "mcp_client_id"),
    )


class TiqoraAiQueuePolicy(TiqoraBase):
    """Per-queue AI configuration (plan §3.1). No inheritance to subqueues (v1)."""

    __tablename__ = "tiqora_ai_queue_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)

    enabled_auto_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    enabled_summary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    enabled_manual_assist: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    autonomy: Mapped[str] = mapped_column(String(20), nullable=False, default=AUTONOMY_OFF)

    # Acting principal for auto writes. NULL allowed until enabled_auto_reply
    # is flipped true (API-validated requirement, not a DB constraint).
    service_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    llm_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_llm_provider.id", ondelete="SET NULL"), nullable=True
    )
    model_override: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Image attachments are never shown to the main model (plan: vision
    # pre-pass) — NULL means images are ignored entirely for this queue.
    vision_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_llm_provider.id", ondelete="SET NULL"), nullable=True
    )

    kb_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    kb_category_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_client_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_tool_overrides: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary_article_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_char_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_incremental_min_articles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_incremental_min_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)

    max_clarifications: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    max_auto_replies: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    max_replies_per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_tokens_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    escalation_rules: Mapped[str | None] = mapped_column(Text, nullable=True)

    ai_disclosure_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    ai_disclosure_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    pii_masking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    identity_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default=IDENTITY_TICKET_CUSTOMER_ID
    )
    clarify_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    valid_id: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TiqoraAiDraft(TiqoraBase):
    """A customer-message draft (plan §3.1/§3.4). Never an article until a
    human accepts it (or autonomy sends it, mapped by the runtime — never the
    model)."""

    __tablename__ = "tiqora_ai_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT_KIND_REPLY)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    based_on_article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT_STATUS_OPEN)
    accepted_article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_AUTO)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_tiqora_ai_draft_ticket_status", "ticket_id", "status"),)


class TiqoraAiArticleOrigin(TiqoraBase):
    """Origin marker for AI-written articles (auto-reply / manual-accept).

    Used to filter own AI output out of the LLM context and summarization
    input (plan §3.4 step 5) without touching the core article schema.
    """

    __tablename__ = "tiqora_ai_article_origin"

    article_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_ai_draft.id", ondelete="SET NULL"), nullable=True
    )
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    service_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class TiqoraAiAcl(TiqoraBase):
    """Feature ACL + optional daily/monthly limits per group/role/user."""

    __tablename__ = "tiqora_ai_acl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[str] = mapped_column(String(30), nullable=False)
    allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    limit_requests_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_tokens_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_requests_month: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", "feature", name="uq_tiqora_ai_acl_subject_feature"
        ),
    )


class TiqoraAiUsage(TiqoraBase):
    """One LLM call's usage/cost record (audit + budget reporting)."""

    __tablename__ = "tiqora_ai_usage"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    feature: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_llm_provider.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cost_hint: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_tiqora_ai_usage_ts", "ts"),
        Index("ix_tiqora_ai_usage_queue_ts", "queue_id", "ts"),
    )


class TiqoraAiTicketState(TiqoraBase):
    """Per-ticket AI runtime state: loop guard, run lock, canonical summary."""

    __tablename__ = "tiqora_ai_ticket_state"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    last_customer_article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_lock_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    run_lock_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_summary_upto_article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When summary_body was (re)generated — distinct from last_run_at, which
    # also moves on runs that leave the summary untouched (up_to_date checks).
    summary_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_reply_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    clarification_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
