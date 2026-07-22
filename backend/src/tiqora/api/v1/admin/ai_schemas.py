"""Pydantic schemas for ``/api/v1/admin/ai/*`` (kept out of ``schemas.py`` to
avoid merge conflicts with other in-flight admin work, per plan §Phase A)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OperationMode = Literal["parallel", "tiqora_primary"]
ProviderKind = Literal["openai_compat", "anthropic"]
McpTransport = Literal["streamable_http"]
Autonomy = Literal["off", "clarify_only", "full"]
IdentityMode = Literal["ticket_customer_id", "clarify_schema", "off"]
AclSubjectType = Literal["group", "role", "user"]
AclFeature = Literal["summary", "auto_reply", "manual_assist", "mcp"]


# ---------------------------------------------------------------------------
# System settings
# ---------------------------------------------------------------------------


class AiSettingsOut(BaseModel):
    operation_mode: OperationMode
    disclosure_default_text: str
    global_max_replies_per_hour: int | None


class AiSettingsUpdate(BaseModel):
    operation_mode: OperationMode | None = None
    disclosure_default_text: str | None = None
    global_max_replies_per_hour: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------


class LlmProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: ProviderKind
    base_url: str
    default_model: str
    has_api_key: bool
    extra_json: str | None
    supports_tools: bool
    supports_streaming: bool
    eu_hosted: bool
    supports_vision: bool
    valid_id: int
    create_time: datetime
    change_time: datetime


class LlmProviderCreate(BaseModel):
    name: str
    kind: ProviderKind = "openai_compat"
    base_url: str
    default_model: str
    api_key: str | None = None
    extra_json: str | None = None
    supports_tools: bool = True
    supports_streaming: bool = True
    eu_hosted: bool = False
    supports_vision: bool = False


class LlmProviderUpdate(BaseModel):
    name: str | None = None
    kind: ProviderKind | None = None
    base_url: str | None = None
    default_model: str | None = None
    # Write-only: omit or empty string keeps the stored key.
    api_key: str | None = None
    extra_json: str | None = None
    supports_tools: bool | None = None
    supports_streaming: bool | None = None
    eu_hosted: bool | None = None
    supports_vision: bool | None = None
    valid_id: int | None = None


class LlmProviderTestOut(BaseModel):
    ok: bool
    model: str | None
    tool_calling_ok: bool
    error: str | None


# ---------------------------------------------------------------------------
# MCP clients + tool policies
# ---------------------------------------------------------------------------


class McpClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    has_auth_token: bool
    transport: McpTransport
    last_discovered_at: datetime | None
    valid_id: int
    create_time: datetime
    change_time: datetime


class McpClientCreate(BaseModel):
    name: str
    url: str
    auth_token: str | None = None
    transport: McpTransport = "streamable_http"


class McpClientUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    auth_token: str | None = None
    transport: McpTransport | None = None
    valid_id: int | None = None


class McpToolPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mcp_client_id: int
    tool_name: str
    enabled: bool
    mutating: bool
    description_snapshot: str | None


class McpToolPolicyUpdate(BaseModel):
    enabled: bool | None = None
    mutating: bool | None = None


class McpDiscoverOut(BaseModel):
    tool_names: list[str]
    added: list[str]
    removed: list[str]


# ---------------------------------------------------------------------------
# Queue AI policy
# ---------------------------------------------------------------------------


class AiQueuePolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    queue_id: int
    enabled_auto_reply: bool
    enabled_summary: bool
    enabled_manual_assist: bool
    system_prompt: str
    autonomy: Autonomy
    service_user_id: int | None
    llm_provider_id: int | None
    model_override: str | None
    vision_provider_id: int | None
    kb_tags: str | None
    kb_category_ids: str | None
    mcp_client_ids: str | None
    mcp_tool_overrides: str | None
    summary_article_threshold: int | None
    summary_char_threshold: int | None
    summary_incremental_min_articles: int | None
    summary_incremental_min_chars: int | None
    max_clarifications: int
    max_auto_replies: int
    max_replies_per_hour: int | None
    budget_tokens_day: int | None
    escalation_rules: str | None
    ai_disclosure_enabled: bool
    ai_disclosure_text: str | None
    pii_masking: bool
    identity_mode: IdentityMode
    clarify_schema_json: str | None
    valid_id: int
    create_time: datetime
    change_time: datetime


class AiQueuePolicyCreate(BaseModel):
    queue_id: int
    enabled_auto_reply: bool = False
    enabled_summary: bool = False
    enabled_manual_assist: bool = False
    system_prompt: str = ""
    autonomy: Autonomy = "off"
    service_user_id: int | None = None
    llm_provider_id: int | None = None
    model_override: str | None = None
    vision_provider_id: int | None = None
    kb_tags: str | None = None
    kb_category_ids: str | None = None
    mcp_client_ids: str | None = None
    mcp_tool_overrides: str | None = None
    summary_article_threshold: int | None = None
    summary_char_threshold: int | None = None
    summary_incremental_min_articles: int | None = None
    summary_incremental_min_chars: int | None = None
    max_clarifications: int = 2
    max_auto_replies: int = 5
    max_replies_per_hour: int | None = None
    budget_tokens_day: int | None = None
    escalation_rules: str | None = None
    ai_disclosure_enabled: bool = False
    ai_disclosure_text: str | None = None
    pii_masking: bool = True
    identity_mode: IdentityMode = "ticket_customer_id"
    clarify_schema_json: str | None = None


class AiQueuePolicyUpdate(BaseModel):
    enabled_auto_reply: bool | None = None
    enabled_summary: bool | None = None
    enabled_manual_assist: bool | None = None
    system_prompt: str | None = None
    autonomy: Autonomy | None = None
    service_user_id: int | None = None
    llm_provider_id: int | None = None
    model_override: str | None = None
    vision_provider_id: int | None = None
    kb_tags: str | None = None
    kb_category_ids: str | None = None
    mcp_client_ids: str | None = None
    mcp_tool_overrides: str | None = None
    summary_article_threshold: int | None = None
    summary_char_threshold: int | None = None
    summary_incremental_min_articles: int | None = None
    summary_incremental_min_chars: int | None = None
    max_clarifications: int | None = None
    max_auto_replies: int | None = None
    max_replies_per_hour: int | None = None
    budget_tokens_day: int | None = None
    escalation_rules: str | None = None
    ai_disclosure_enabled: bool | None = None
    ai_disclosure_text: str | None = None
    pii_masking: bool | None = None
    identity_mode: IdentityMode | None = None
    clarify_schema_json: str | None = None
    valid_id: int | None = None


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class AiUsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    user_id: int | None
    queue_id: int | None
    ticket_id: int | None
    feature: AclFeature
    provider_id: int | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_hint: float | None
    success: bool
    error: str | None


class AiUsagePageOut(BaseModel):
    items: list[AiUsageOut]
    total: int
    total_prompt_tokens: int
    total_completion_tokens: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# ACL
# ---------------------------------------------------------------------------


class AiAclOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_type: AclSubjectType
    subject_id: int
    feature: AclFeature
    allowed: bool
    limit_requests_day: int | None
    limit_tokens_day: int | None
    limit_requests_month: int | None


class AiAclCreate(BaseModel):
    subject_type: AclSubjectType
    subject_id: int
    feature: AclFeature
    allowed: bool = True
    limit_requests_day: int | None = None
    limit_tokens_day: int | None = None
    limit_requests_month: int | None = None


class AiAclUpdate(BaseModel):
    subject_type: AclSubjectType | None = None
    subject_id: int | None = None
    feature: AclFeature | None = None
    allowed: bool | None = None
    limit_requests_day: int | None = None
    limit_tokens_day: int | None = None
    limit_requests_month: int | None = None
