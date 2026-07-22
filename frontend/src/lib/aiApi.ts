/**
 * Wrappers for the `/api/v1/admin/ai/*` endpoints (Tiqora AI subsystem,
 * Phase A — see `~/TIQORA_LLM_PLAN.md`).
 *
 * These are hand-written rather than added to `@tiqora/api-client`'s
 * `client.ts` because this page set was built in a parallel worktree that is
 * scoped to `frontend/src/` only — the backend team owns `packages/` and
 * `schema.d.ts` regeneration. The shapes below mirror
 * `backend/src/tiqora/api/v1/admin/ai_schemas.py` exactly; once the shared
 * client picks up generated bindings for these routes, callers can switch
 * back to `api.adminAi*` without changing call sites here.
 */
import { api } from "./api";
import type { AdminPage } from "@tiqora/api-client";

export type OperationMode = "parallel" | "tiqora_primary";
export type ProviderKind = "openai_compat" | "anthropic";
export type McpTransport = "streamable_http";
export type Autonomy = "off" | "clarify_only" | "full";
export type IdentityMode = "ticket_customer_id" | "clarify_schema" | "off";
export type AclSubjectType = "group" | "role" | "user";
export type AclFeature = "summary" | "auto_reply" | "manual_assist" | "mcp";

export type AiSettingsOut = {
  operation_mode: OperationMode;
  disclosure_default_text: string;
  global_max_replies_per_hour: number | null;
};

export type AiSettingsUpdate = Partial<AiSettingsOut>;

export type LlmProviderOut = {
  id: number;
  name: string;
  kind: ProviderKind;
  base_url: string;
  default_model: string;
  has_api_key: boolean;
  extra_json: string | null;
  supports_tools: boolean;
  supports_streaming: boolean;
  eu_hosted: boolean;
  supports_vision: boolean;
  valid_id: number;
  create_time: string;
  change_time: string;
};

export type LlmProviderCreate = {
  name: string;
  kind?: ProviderKind;
  base_url: string;
  default_model: string;
  api_key?: string | null;
  extra_json?: string | null;
  supports_tools?: boolean;
  supports_streaming?: boolean;
  eu_hosted?: boolean;
  supports_vision?: boolean;
};

export type LlmProviderUpdate = Partial<LlmProviderCreate> & { valid_id?: number };

export type LlmProviderTestOut = {
  ok: boolean;
  model: string | null;
  tool_calling_ok: boolean;
  error: string | null;
};

export type McpClientOut = {
  id: number;
  name: string;
  url: string;
  has_auth_token: boolean;
  transport: McpTransport;
  last_discovered_at: string | null;
  valid_id: number;
  create_time: string;
  change_time: string;
};

export type McpClientCreate = {
  name: string;
  url: string;
  auth_token?: string | null;
  transport?: McpTransport;
};

export type McpClientUpdate = Partial<McpClientCreate> & { valid_id?: number };

export type McpToolPolicyOut = {
  id: number;
  mcp_client_id: number;
  tool_name: string;
  enabled: boolean;
  mutating: boolean;
  description_snapshot: string | null;
};

export type McpToolPolicyUpdate = {
  enabled?: boolean;
  mutating?: boolean;
};

export type McpDiscoverOut = {
  tool_names: string[];
  added: string[];
  removed: string[];
};

export type AiQueuePolicyOut = {
  id: number;
  queue_id: number;
  enabled_auto_reply: boolean;
  enabled_summary: boolean;
  enabled_manual_assist: boolean;
  system_prompt: string;
  autonomy: Autonomy;
  service_user_id: number | null;
  llm_provider_id: number | null;
  model_override: string | null;
  vision_provider_id: number | null;
  kb_tags: string | null;
  kb_category_ids: string | null;
  mcp_client_ids: string | null;
  mcp_tool_overrides: string | null;
  summary_article_threshold: number | null;
  summary_char_threshold: number | null;
  summary_incremental_min_articles: number | null;
  summary_incremental_min_chars: number | null;
  max_clarifications: number;
  max_auto_replies: number;
  max_replies_per_hour: number | null;
  budget_tokens_day: number | null;
  escalation_rules: string | null;
  ai_disclosure_enabled: boolean;
  ai_disclosure_text: string | null;
  pii_masking: boolean;
  identity_mode: IdentityMode;
  clarify_schema_json: string | null;
  valid_id: number;
  create_time: string;
  change_time: string;
};

export type AiQueuePolicyCreate = {
  queue_id: number;
  enabled_auto_reply?: boolean;
  enabled_summary?: boolean;
  enabled_manual_assist?: boolean;
  system_prompt?: string;
  autonomy?: Autonomy;
  service_user_id?: number | null;
  llm_provider_id?: number | null;
  model_override?: string | null;
  vision_provider_id?: number | null;
  kb_tags?: string | null;
  kb_category_ids?: string | null;
  mcp_client_ids?: string | null;
  mcp_tool_overrides?: string | null;
  summary_article_threshold?: number | null;
  summary_char_threshold?: number | null;
  summary_incremental_min_articles?: number | null;
  summary_incremental_min_chars?: number | null;
  max_clarifications?: number;
  max_auto_replies?: number;
  max_replies_per_hour?: number | null;
  budget_tokens_day?: number | null;
  escalation_rules?: string | null;
  ai_disclosure_enabled?: boolean;
  ai_disclosure_text?: string | null;
  pii_masking?: boolean;
  identity_mode?: IdentityMode;
  clarify_schema_json?: string | null;
};

export type AiQueuePolicyUpdate = Partial<AiQueuePolicyCreate> & { valid_id?: number };

export type AiUsageOut = {
  id: number;
  ts: string;
  user_id: number | null;
  queue_id: number | null;
  ticket_id: number | null;
  feature: AclFeature;
  provider_id: number | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_hint: number | null;
  success: boolean;
  error: string | null;
};

export type AiUsagePageOut = {
  items: AiUsageOut[];
  total: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  page: number;
  page_size: number;
};

export type AiUsageListParams = {
  queue_id?: number;
  feature?: AclFeature;
  from?: string;
  to?: string;
  page?: number;
  page_size?: number;
};

export type AiAclOut = {
  id: number;
  subject_type: AclSubjectType;
  subject_id: number;
  feature: AclFeature;
  allowed: boolean;
  limit_requests_day: number | null;
  limit_tokens_day: number | null;
  limit_requests_month: number | null;
};

export type AiAclCreate = {
  subject_type: AclSubjectType;
  subject_id: number;
  feature: AclFeature;
  allowed?: boolean;
  limit_requests_day?: number | null;
  limit_tokens_day?: number | null;
  limit_requests_month?: number | null;
};

export type AiAclUpdate = Partial<AiAclCreate>;

/** Wraps a plain array endpoint into the `AdminPage` shape the shared admin table components expect. */
function asPage<T>(items: T[]): AdminPage<T> {
  return { items, total: items.length, page: 1, page_size: Math.max(items.length, 1) };
}

export const aiApi = {
  getSettings(signal?: AbortSignal) {
    return api.request<AiSettingsOut>("GET", "/api/v1/admin/ai/settings", { signal });
  },
  putSettings(body: AiSettingsUpdate, signal?: AbortSignal) {
    return api.request<AiSettingsOut>("PUT", "/api/v1/admin/ai/settings", { body, signal });
  },

  listProviders: async (signal?: AbortSignal) =>
    asPage(await api.request<LlmProviderOut[]>("GET", "/api/v1/admin/ai/providers", { signal })),
  createProvider(body: LlmProviderCreate, signal?: AbortSignal) {
    return api.request<LlmProviderOut>("POST", "/api/v1/admin/ai/providers", { body, signal });
  },
  updateProvider(id: number | string, body: LlmProviderUpdate, signal?: AbortSignal) {
    return api.request<LlmProviderOut>("PUT", `/api/v1/admin/ai/providers/${id}`, { body, signal });
  },
  deleteProvider(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/providers/${id}`, { signal });
  },
  testProvider(id: number | string, signal?: AbortSignal) {
    return api.request<LlmProviderTestOut>("POST", `/api/v1/admin/ai/providers/${id}/test`, {
      signal,
    });
  },
  duplicateProvider(id: number | string, signal?: AbortSignal) {
    return api.request<LlmProviderOut>("POST", `/api/v1/admin/ai/providers/${id}/duplicate`, {
      signal,
    });
  },

  listMcpClients: async (signal?: AbortSignal) =>
    asPage(await api.request<McpClientOut[]>("GET", "/api/v1/admin/ai/mcp-clients", { signal })),
  createMcpClient(body: McpClientCreate, signal?: AbortSignal) {
    return api.request<McpClientOut>("POST", "/api/v1/admin/ai/mcp-clients", { body, signal });
  },
  updateMcpClient(id: number | string, body: McpClientUpdate, signal?: AbortSignal) {
    return api.request<McpClientOut>("PUT", `/api/v1/admin/ai/mcp-clients/${id}`, { body, signal });
  },
  deleteMcpClient(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/mcp-clients/${id}`, { signal });
  },
  discoverMcpTools(id: number | string, signal?: AbortSignal) {
    return api.request<McpDiscoverOut>("POST", `/api/v1/admin/ai/mcp-clients/${id}/discover`, {
      signal,
    });
  },
  listMcpToolPolicies(clientId: number | string, signal?: AbortSignal) {
    return api.request<McpToolPolicyOut[]>(
      "GET",
      `/api/v1/admin/ai/mcp-clients/${clientId}/tools`,
      { signal },
    );
  },
  updateMcpToolPolicy(
    clientId: number | string,
    toolName: string,
    body: McpToolPolicyUpdate,
    signal?: AbortSignal,
  ) {
    return api.request<McpToolPolicyOut>(
      "PUT",
      `/api/v1/admin/ai/mcp-clients/${clientId}/tools/${encodeURIComponent(toolName)}`,
      { body, signal },
    );
  },

  listQueuePolicies: async (signal?: AbortSignal) =>
    asPage(
      await api.request<AiQueuePolicyOut[]>("GET", "/api/v1/admin/ai/queue-policies", { signal }),
    ),
  createQueuePolicy(body: AiQueuePolicyCreate, signal?: AbortSignal) {
    return api.request<AiQueuePolicyOut>("POST", "/api/v1/admin/ai/queue-policies", {
      body,
      signal,
    });
  },
  updateQueuePolicy(id: number | string, body: AiQueuePolicyUpdate, signal?: AbortSignal) {
    return api.request<AiQueuePolicyOut>("PUT", `/api/v1/admin/ai/queue-policies/${id}`, {
      body,
      signal,
    });
  },
  deleteQueuePolicy(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/queue-policies/${id}`, { signal });
  },

  listUsage(params: AiUsageListParams = {}, signal?: AbortSignal) {
    return api.request<AiUsagePageOut>("GET", "/api/v1/admin/ai/usage", {
      query: {
        queue_id: params.queue_id,
        feature: params.feature,
        from: params.from,
        to: params.to,
        page: params.page,
        page_size: params.page_size,
      },
      signal,
    });
  },

  listAcl(signal?: AbortSignal) {
    return api.request<AiAclOut[]>("GET", "/api/v1/admin/ai/acl", { signal });
  },
  createAcl(body: AiAclCreate, signal?: AbortSignal) {
    return api.request<AiAclOut>("POST", "/api/v1/admin/ai/acl", { body, signal });
  },
  updateAcl(id: number | string, body: AiAclUpdate, signal?: AbortSignal) {
    return api.request<AiAclOut>("PUT", `/api/v1/admin/ai/acl/${id}`, { body, signal });
  },
  deleteAcl(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/acl/${id}`, { signal });
  },
};
