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

# tiqora_ai_queue_policy.reply_language_mode
REPLY_LANGUAGE_OFF = "off"
REPLY_LANGUAGE_FIXED = "fixed"
REPLY_LANGUAGE_AUTO = "auto"
REPLY_LANGUAGE_MODES = frozenset({REPLY_LANGUAGE_OFF, REPLY_LANGUAGE_FIXED, REPLY_LANGUAGE_AUTO})

# tiqora_ai_queue_policy.allowed_state_types default (plan block 5) — applied
# when the column is NULL/blank, see tiqora.ai.tools.resolve_allowed_state_types.
DEFAULT_ALLOWED_STATE_TYPES = ("open",)

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

# tiqora_ai_triage.status
#   open      — a proposal is waiting for an agent to accept/reject it
#   applied   — the worker itself applied it (confidence >= auto threshold)
#   accepted  — a human accepted the proposal from the ticket UI
#   rejected  — a human rejected it
#   no_action — the run finished but nothing reached the suggest threshold
#   error     — the run failed; the row exists only as the "already triaged" guard
TRIAGE_STATUS_OPEN = "open"
TRIAGE_STATUS_APPLIED = "applied"
TRIAGE_STATUS_ACCEPTED = "accepted"
TRIAGE_STATUS_REJECTED = "rejected"
TRIAGE_STATUS_NO_ACTION = "no_action"
TRIAGE_STATUS_ERROR = "error"
TRIAGE_STATUSES = frozenset(
    {
        TRIAGE_STATUS_OPEN,
        TRIAGE_STATUS_APPLIED,
        TRIAGE_STATUS_ACCEPTED,
        TRIAGE_STATUS_REJECTED,
        TRIAGE_STATUS_NO_ACTION,
        TRIAGE_STATUS_ERROR,
    }
)

# tiqora_ai_triage.customer_source — how the forwarded-sender address was
# obtained. "header_parse" is the only one that can auto-apply; "llm" exists
# for the (currently disabled) tie-breaker path, see tiqora.ai.triage.
TRIAGE_CUSTOMER_SOURCE_HEADER = "header_parse"
TRIAGE_CUSTOMER_SOURCE_LLM = "llm"

# tiqora_ai_draft.source / tiqora_ai_article_origin.source
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
ARTICLE_ORIGIN_SOURCES = frozenset({SOURCE_AUTO, "manual_accept"})

# tiqora_ai_prompt_part.kind
PROMPT_PART_FILE = "file"
PROMPT_PART_NOTE = "note"
PROMPT_PART_KINDS = frozenset({PROMPT_PART_FILE, PROMPT_PART_NOTE})

# tiqora_ai_queue_policy.summary_detail (tiqora.ai.summary system-prompt
# verbosity) — "standard" reproduces the pre-existing prompt/behaviour
# exactly; "detailed" asks for longer paragraphs and a 3-5 sentence
# per-document summary instead of 1-2.
DETAIL_STANDARD = "standard"
DETAIL_DETAILED = "detailed"
SUMMARY_DETAIL_MODES = frozenset({DETAIL_STANDARD, DETAIL_DETAILED})

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
FEATURE_REFINE = "refine"
AI_FEATURES = frozenset(
    {FEATURE_SUMMARY, FEATURE_AUTO_REPLY, FEATURE_MANUAL_ASSIST, FEATURE_MCP, FEATURE_REFINE}
)

# tiqora_ai_usage.feature for triage runs. Deliberately **not** in
# AI_FEATURES: that set is what tiqora.ai.acl validates against, and triage
# has no interactive user to grant or deny it to — listing it there would
# surface a meaningless per-user ACL row in the admin UI. The usage column is
# free-form String(30), so the literal is all that is needed.
FEATURE_TRIAGE = "triage"

# tiqora_ai_audit_log.feature — distinct from AI_FEATURES above: this is the
# LLM *call site*, not the ACL/usage feature name (manual assist calls are
# logged as "draft" — every Manual Assist run is always the draft path, see
# tiqora.ai.runtime module docstring).
AUDIT_FEATURE_DRAFT = "draft"
AUDIT_FEATURE_SUMMARY = "summary"
AUDIT_FEATURE_AUTO_REPLY = "auto_reply"
AUDIT_FEATURE_VISION = "vision"
AUDIT_FEATURE_TEST = "test"
AUDIT_FEATURE_REFINE = "refine"
AUDIT_FEATURE_TRIAGE = "triage"
AUDIT_FEATURES = frozenset(
    {
        AUDIT_FEATURE_DRAFT,
        AUDIT_FEATURE_SUMMARY,
        AUDIT_FEATURE_AUTO_REPLY,
        AUDIT_FEATURE_VISION,
        AUDIT_FEATURE_TEST,
        AUDIT_FEATURE_REFINE,
        AUDIT_FEATURE_TRIAGE,
    }
)


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
    # How many tool rounds the agent loop grants this model before the
    # terminal-force closes the run (see tiqora.ai.runtime). A model that
    # batches its tool calls needs fewer; a weaker one needs more. ``None``
    # means "use DEFAULT_MAX_TOOL_ROUNDS", the same "not configured" semantics
    # as the pricing and budget columns above.
    #
    # Every extra round re-sends the whole grown conversation, so raising this
    # costs more than linearly — see the module docstring of
    # ``tiqora.ai.runtime`` for the loop that spends it.
    max_tool_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Token pricing (per 1M tokens), all optional — see
    # ``tiqora.ai.usage.record_usage`` for how these feed ``cost_hint``.
    price_input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Cost budgets (in price_currency), all optional — see
    # ``tiqora.ai.usage.provider_budget_exceeded`` for enforcement. ``None``
    # means "no limit configured", same semantics as unset pricing.
    budget_cost_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_cost_week: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_cost_month: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # JSON Schema object for tool arguments (from MCP discovery inputSchema).
    # Used by the tool executor to reject unknown argument keys (plan B #4).
    parameters_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # "Text verfeinern" in the agent composer (tiqora.ai.refine) — a plain
    # rewrite of what the agent typed, so it is independent of the autonomy
    # ladder above and needs no service user.
    enabled_refine: Mapped[bool] = mapped_column(
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
    # spaCy NER (de+en) additionally feeds person names it finds in the raw
    # article/attachment text into the same name-masking path as
    # collect_known_names — only takes effect when pii_masking is also on
    # (see tiqora.ai.ner, tiqora.ai.context.collect_known_names).
    pii_ner_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    identity_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default=IDENTITY_TICKET_CUSTOMER_ID
    )
    clarify_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Priority-ordered LLM provider fallback list (plan block: LLM fallback) —
    # JSON array of {"provider_id": int, "model": str|null}, tried in order
    # when llm_provider_id errors or is unavailable. NULL/empty = no fallback.
    llm_fallback_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Sender blocklist (plan block 2) — JSON array of exact addresses or
    # "*@domain" globs (see tiqora.ai.senders.matches_ignored). NULL/empty =
    # no blocklist.
    ignored_senders: Mapped[str | None] = mapped_column(Text, nullable=True)
    ignore_senders_manual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Reply language (plan block 3) — see tiqora.ai.reply_language. "off"
    # (default) reproduces today's behaviour (no binding language line).
    reply_language_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default=REPLY_LANGUAGE_OFF, server_default=REPLY_LANGUAGE_OFF
    )
    reply_language_fixed: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reply_language_default: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # update_ticket_fields state-change guard (plan block 5) — JSON array of
    # ticket_state_type names. NULL/blank = default ["open"] (reopen allowed,
    # never close); an explicit "[]" disables state changes entirely.
    allowed_state_types: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional partial override of capability bits derived from autonomy
    # (plan C #5). JSON object of bool fields; see tiqora.ai.capabilities.
    capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # tiqora.ai.summary system-prompt verbosity — see DETAIL_STANDARD/
    # DETAIL_DETAILED above.
    summary_detail: Mapped[str] = mapped_column(
        String(12), nullable=False, default=DETAIL_STANDARD, server_default=DETAIL_STANDARD
    )

    # --- AI triage (tiqora.ai.triage / tiqora.ai.triage_worker) ------------
    # Routing of a newly created ticket into the correct queue, decided once
    # on the ticket's first article, before the reply agent runs.
    enabled_triage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Free text describing what belongs in **this** queue. Used twice: shown
    # to *other* queues that list this one in their triage_target_queue_ids,
    # and shown under the "stay" option of this queue's own triage run, so
    # the model has a concrete reason to leave a ticket where it is. Empty
    # means a target is offered by name only, which degrades routing badly;
    # the admin UI warns about it.
    routing_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON array of queue ids this queue may route *into* (parsed with
    # tiqora.ai.listfields.parse_int_list, same shape as kb_category_ids).
    # This list is also the authorization boundary for the automatic move:
    # the worker uses the module-level move_queue mutator, which does not
    # check move_into for the service user — an admin putting a queue here
    # *is* the grant. See tiqora.ai.triage.apply_decision.
    triage_target_queue_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Confidence is an int 0..100 (never a float — every other threshold in
    # this table is Integer, and it avoids MariaDB/Postgres rounding drift).
    # Default 100 means "suggest only, never move on its own": the thresholds
    # are unmeasured on a fresh install, so autonomy has to be switched on
    # deliberately per queue once /admin/ai/triage/stats supports it.
    triage_auto_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    triage_suggest_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50, server_default="50"
    )
    # Self-consistency votes per triage run (see tiqora.ai.triage.decide).
    # 1 disables voting and falls back to the model's self-report alone.
    triage_samples: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )

    triage_customer_fix_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    triage_customer_fix_auto_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # Set on the *destination* queue: after a triage move into this queue,
    # park the reply agent until the customer writes again (implemented by
    # setting tiqora_ai_ticket_state.last_customer_article_id). Off by
    # default — with it on, the customer gets no AI answer to their first
    # mail at all.
    triage_delay_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Triage may use a cheaper model than the reply agent. NULL falls back to
    # llm_provider_id / model_override.
    triage_llm_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_llm_provider.id", ondelete="SET NULL"), nullable=True
    )
    triage_model_override: Mapped[str | None] = mapped_column(String(200), nullable=True)

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


class TiqoraAiTriage(TiqoraBase):
    """One triage decision per ticket (tiqora.ai.triage_worker).

    A row is written for **every** run, including ``no_action`` and
    ``error`` — it doubles as the permanent "this ticket was already
    triaged" guard (hence ``UNIQUE(ticket_id)``) and as the labelled
    dataset that turns ``triage_auto_threshold`` from a guess into a
    measured number: accepted-vs-rejected per confidence bucket is exactly
    what ``GET /admin/ai/triage/stats`` reports.

    Deliberately its own table rather than columns on
    ``tiqora_ai_ticket_state``: that row is read on the hot path of every
    auto-reply tick, and the two independent proposals here (queue and
    customer), each with its own confidence and applied flag, would add
    ~15 columns to it. Shape mirrors ``tiqora_ai_draft``.
    """

    __tablename__ = "tiqora_ai_triage"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_tiqora_ai_triage_ticket"),
        Index("ix_tiqora_ai_triage_status_created", "status", "create_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The article that triggered the run — always the ticket's first.
    article_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- queue proposal ---
    suggested_queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Model rationale. Internal only — never shown to a customer.
    queue_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON array of [{"key": "q_42", "votes": 3, "self": 95}, ...] — the raw
    # vote distribution, kept because the run is not deterministic on replay.
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- customer proposal (forwarded mail) ---
    extracted_email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # customer_user.LOGIN, not the numeric PK — that is what ticket.
    # customer_user_id stores (see tiqora.ai.context.customer_user_name).
    suggested_customer_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    suggested_customer_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    customer_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- bookkeeping ---
    # Joins tiqora_ai_audit_log and tiqora_ai_usage; one run_id covers all
    # `triage_samples` LLM calls.
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TiqoraAiPromptPart(TiqoraBase):
    """An ordered, enable-able system-prompt fragment attached to a queue
    policy ("Prompt-Bausteine"). The effective system prompt sent to the
    model is ``policy.system_prompt`` followed by every *enabled* part's
    ``content``, in ``position`` order — see
    ``tiqora.ai.runtime._build_system_prompt``. ``kind`` is metadata only
    (``file`` vs. free-form ``note``); both are stored and rendered the
    same way.
    """

    __tablename__ = "tiqora_ai_prompt_part"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("tiqora_ai_queue_policy.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=PROMPT_PART_NOTE)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_tiqora_ai_prompt_part_policy_position", "policy_id", "position"),)


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
    tool_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # Failed AI identity-check attempts (plan: identity verification) — reset
    # once identity is confirmed; used to cap retries before escalating.
    identity_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Manual Assist background-run bookkeeping (nginx-90s-timeout fix): the
    # API route now returns immediately and runs the agent in a background
    # task, so the frontend polls GET /tickets/{id}/ai for these instead of
    # waiting on the POST response. Written ONLY by the manual-trigger API
    # path (tiqora.api.v1.ai) — the auto-reply worker (ai/auto_worker.py)
    # never touches them.
    manual_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manual_run_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_run_error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manual_run_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set when the agent hands off to a human (escalate_to_human or an
    # escalation-rule hit). Independent of Znuny SLA ``ticket.escalation_*``
    # columns — those are rebuilt from queue minutes and would wipe a fake
    # timestamp. Cleared when an agent sends a customer-visible article or
    # the ticket moves to closed/merged/removed.
    ai_escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TiqoraAiAuditLog(TiqoraBase):
    """One LLM chat-completion call, in/out (full audit — separate from
    :class:`TiqoraAiUsage`, which is aggregate cost/budget reporting only).

    ``request_json``/``response_json`` store the wire payload **after**
    PII masking (:mod:`tiqora.ai.pii`) — exactly what the provider saw. When
    masking is off for the run, both columns hold the unmasked payload (the
    provider saw that anyway). Vision requests never carry raw image bytes —
    ``image_url`` data-URLs are replaced with a ``"[image: <n> bytes]"``
    placeholder before the row is written (see :mod:`tiqora.ai.audit`).

    ``pii_map_enc`` is a Fernet-encrypted JSON ``{placeholder: original}``
    snapshot (same at-rest scheme as ``tiqora_llm_provider.api_key_enc``,
    see :mod:`tiqora.crypto.secret`) — decrypted only via the admin
    "reveal PII" action, which is itself logged. ``pii_counts_json`` is the
    non-sensitive ``{"EMAIL": 3, ...}`` summary shown in list/detail views
    without needing to decrypt anything.

    Columns are plain ``Text`` rather than a MySQL-specific ``LONGTEXT`` to
    stay portable across the MariaDB/PostgreSQL test fixtures (see the
    module docstring's "not a dialect JSON type" convention) — image bytes
    are already stripped before the row is built, so ordinary chat/tool
    payloads comfortably fit.
    """

    __tablename__ = "tiqora_ai_audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("tiqora_llm_provider.id", ondelete="SET NULL"), nullable=True
    )
    provider_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    feature: Mapped[str] = mapped_column(String(30), nullable=False)
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acting_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pii_map_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    pii_counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_tiqora_ai_audit_log_ts", "ts"),
        Index("ix_tiqora_ai_audit_log_run_id", "run_id"),
        Index("ix_tiqora_ai_audit_log_ticket_ts", "ticket_id", "ts"),
    )
