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
import type { AdminPage, Schemas } from "@tiqora/api-client";

export type OperationMode = "parallel" | "tiqora_primary";
export type ProviderKind = Schemas["LlmProviderOut"]["kind"];
export type McpTransport = "streamable_http";
export type Autonomy = "off" | "clarify_only" | "full";
export type IdentityMode = "ticket_customer_id" | "clarify_schema" | "off";
export type ReplyLanguageMode = "off" | "fixed" | "auto";
export type AclSubjectType = "group" | "role" | "user";
export type AclFeature =
  "summary" | "auto_reply" | "manual_assist" | "mcp" | "refine";
export type UsageFeature = AclFeature | "triage";

export type AiSettingsOut = {
  operation_mode: OperationMode;
  disclosure_default_text: string;
  global_max_replies_per_hour: number | null;
  audit_retention_days: number;
  /** Global kill-switch for auto-reply (independent of operation_mode). */
  auto_reply_paused: boolean;
  /** Read-only: tool-round budget a provider gets when it sets none itself. */
  default_max_tool_rounds: number;
};

export type AiSettingsUpdate = Partial<
  Omit<AiSettingsOut, "default_max_tool_rounds">
>;

// Provider, model, profile and task-assignment shapes come straight from the
// generated OpenAPI schema (packages/api-client), so a backend change shows up
// as a type error here instead of a silent mismatch.
export type LlmProviderOut = Schemas["LlmProviderOut"];
export type LlmProviderCreate = Schemas["LlmProviderCreate"];
export type LlmProviderUpdate = Schemas["LlmProviderUpdate"];
/** Connection test of a provider: lists its models (`model_count`); `detail`
 * carries the shortened error when `ok` is false. */
export type LlmProviderTestOut = Schemas["LlmProviderTestOut"];
export type RemoteModelsOut = Schemas["RemoteModelsOut"];

export type LlmModelOut = Schemas["LlmModelOut"];
export type LlmModelIn = Schemas["LlmModelIn"];
export type LlmModelTestOut = Schemas["LlmModelTestOut"];

export type LlmProfileOut = Schemas["LlmProfileOut"];
export type LlmProfileIn = Schemas["LlmProfileIn"];
export type LlmProfileEntryOut = Schemas["LlmProfileEntryOut"];
/** A direct assignment of a profile: global default when `queue_policy_id` is
 * null, else a queue override. Inherited uses are not listed. */
export type LlmProfileUseOut = Schemas["LlmProfileUseOut"];
/** `profile_id: null` = "Kein eigenes Profil" (the task's fallback). */
export type AiTaskProfileItem = Schemas["AiTaskProfileItem"];

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

/** Queue policy. `task_profiles` holds this queue's own task assignments only:
 * a task not listed inherits the global default, `profile_id: null` is an
 * explicit "Kein eigenes Profil". An update with a list replaces the set. */
export type AiQueuePolicyOut = Schemas["AiQueuePolicyOut"];
export type AiQueuePolicyCreate = Schemas["AiQueuePolicyCreate"];
export type AiQueuePolicyUpdate = Schemas["AiQueuePolicyUpdate"];

export type AiUsageOut = {
  id: number;
  ts: string;
  user_id: number | null;
  queue_id: number | null;
  ticket_id: number | null;
  feature: UsageFeature;
  provider_id: number | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_hint: number | null;
  success: boolean;
  error: string | null;
};

export type AiLimitOut = Schemas["AiLimitOut"];

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
  feature?: UsageFeature;
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

// ── LLM-Request-Audit ──────────────────────────────────────────────────

export type AuditFeature =
  "draft" | "summary" | "auto_reply" | "vision" | "test" | "refine" | "triage";
export type AuditRequestStatus = "ok" | "error";

export type AiAuditLogListItemOut = {
  id: number;
  ts: string;
  run_id: string | null;
  provider_id: number | null;
  provider_name: string;
  model: string;
  feature: AuditFeature;
  ticket_id: number | null;
  queue_id: number | null;
  acting_user_id: number | null;
  trigger: string | null;
  status_code: number | null;
  error: string | null;
  duration_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  pii_counts: Record<string, number> | null;
  cost: number | null;
  cost_currency: string | null;
};

export type AiAuditLogDetailOut = AiAuditLogListItemOut & {
  request_json: string;
  response_json: string | null;
};

export type AiAuditLogPageOut = {
  items: AiAuditLogListItemOut[];
  total: number;
  page: number;
  page_size: number;
};

export type AiAuditLogStatsOut = {
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  error_rate: number;
  per_day: { date: string; count: number }[];
  top_model: string | null;
  total_cost: number | null;
  cost_currency: string | null;
};

export type AiAuditLogFilterParams = {
  from?: string;
  to?: string;
  provider_id?: number;
  feature?: AuditFeature;
  ticket?: string;
  status?: AuditRequestStatus;
};

export type AiAuditLogListParams = AiAuditLogFilterParams & {
  page?: number;
  page_size?: number;
};

export type PiiRevealOut = { mapping: Record<string, string> };

export type PromptPartKind = "file" | "note";

export type AiPromptPartOut = {
  id: number;
  policy_id: number;
  kind: PromptPartKind;
  title: string;
  content: string;
  position: number;
  enabled: boolean;
  create_time: string;
  change_time: string;
};

export type AiPromptPartCreate = {
  kind: PromptPartKind;
  title: string;
  content: string;
};

export type AiPromptPartUpdate = {
  title?: string;
  content?: string;
  enabled?: boolean;
};

export type EscalationTestIn = {
  rules_json: string;
  tool: string;
  sample_json: string;
};

export type EscalationHitOut = {
  rule_index: number;
  tool: string;
  field: string | null;
  match: string;
  value: string;
};

export type EscalationTestOut = {
  valid: boolean;
  error?: string | null;
  hit?: EscalationHitOut | null;
};

/** Wraps a plain array endpoint into the `AdminPage` shape the shared admin table components expect. */
function asPage<T>(items: T[]): AdminPage<T> {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: Math.max(items.length, 1),
  };
}

export const aiApi = {
  getSettings(signal?: AbortSignal) {
    return api.request<AiSettingsOut>("GET", "/api/v1/admin/ai/settings", {
      signal,
    });
  },
  putSettings(body: AiSettingsUpdate, signal?: AbortSignal) {
    return api.request<AiSettingsOut>("PUT", "/api/v1/admin/ai/settings", {
      body,
      signal,
    });
  },

  listProviders: async (signal?: AbortSignal) =>
    asPage(
      await api.request<LlmProviderOut[]>("GET", "/api/v1/admin/ai/providers", {
        signal,
      }),
    ),
  createProvider(body: LlmProviderCreate, signal?: AbortSignal) {
    return api.request<LlmProviderOut>("POST", "/api/v1/admin/ai/providers", {
      body,
      signal,
    });
  },
  updateProvider(
    id: number | string,
    body: LlmProviderUpdate,
    signal?: AbortSignal,
  ) {
    return api.request<LlmProviderOut>(
      "PUT",
      `/api/v1/admin/ai/providers/${id}`,
      { body, signal },
    );
  },
  deleteProvider(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/providers/${id}`, {
      signal,
    });
  },
  testProvider(id: number | string, signal?: AbortSignal) {
    return api.request<LlmProviderTestOut>(
      "POST",
      `/api/v1/admin/ai/providers/${id}/test`,
      {
        signal,
      },
    );
  },
  duplicateProvider(id: number | string, signal?: AbortSignal) {
    return api.request<LlmProviderOut>(
      "POST",
      `/api/v1/admin/ai/providers/${id}/duplicate`,
      {
        signal,
      },
    );
  },

  listProviderRemoteModels(id: number | string, signal?: AbortSignal) {
    return api.request<RemoteModelsOut>(
      "GET",
      `/api/v1/admin/ai/providers/${id}/remote-models`,
      { signal },
    );
  },

  listModels(signal?: AbortSignal) {
    return api.request<LlmModelOut[]>("GET", "/api/v1/admin/ai/models", {
      signal,
    });
  },
  createModel(body: LlmModelIn, signal?: AbortSignal) {
    return api.request<LlmModelOut>("POST", "/api/v1/admin/ai/models", {
      body,
      signal,
    });
  },
  updateModel(id: number, body: LlmModelIn, signal?: AbortSignal) {
    return api.request<LlmModelOut>("PUT", `/api/v1/admin/ai/models/${id}`, {
      body,
      signal,
    });
  },
  deleteModel(id: number, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/models/${id}`, {
      signal,
    });
  },
  testModel(id: number, signal?: AbortSignal) {
    return api.request<LlmModelTestOut>(
      "POST",
      `/api/v1/admin/ai/models/${id}/test`,
      { signal },
    );
  },

  listProfiles(signal?: AbortSignal) {
    return api.request<LlmProfileOut[]>("GET", "/api/v1/admin/ai/profiles", {
      signal,
    });
  },
  createProfile(body: LlmProfileIn, signal?: AbortSignal) {
    return api.request<LlmProfileOut>("POST", "/api/v1/admin/ai/profiles", {
      body,
      signal,
    });
  },
  updateProfile(id: number, body: LlmProfileIn, signal?: AbortSignal) {
    return api.request<LlmProfileOut>(
      "PUT",
      `/api/v1/admin/ai/profiles/${id}`,
      { body, signal },
    );
  },
  deleteProfile(id: number, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/profiles/${id}`, {
      signal,
    });
  },

  getTaskDefaults(signal?: AbortSignal) {
    return api.request<AiTaskProfileItem[]>(
      "GET",
      "/api/v1/admin/ai/task-defaults",
      { signal },
    );
  },
  putTaskDefaults(body: AiTaskProfileItem[], signal?: AbortSignal) {
    return api.request<AiTaskProfileItem[]>(
      "PUT",
      "/api/v1/admin/ai/task-defaults",
      { body, signal },
    );
  },

  listMcpClients: async (signal?: AbortSignal) =>
    asPage(
      await api.request<McpClientOut[]>("GET", "/api/v1/admin/ai/mcp-clients", {
        signal,
      }),
    ),
  createMcpClient(body: McpClientCreate, signal?: AbortSignal) {
    return api.request<McpClientOut>("POST", "/api/v1/admin/ai/mcp-clients", {
      body,
      signal,
    });
  },
  updateMcpClient(
    id: number | string,
    body: McpClientUpdate,
    signal?: AbortSignal,
  ) {
    return api.request<McpClientOut>(
      "PUT",
      `/api/v1/admin/ai/mcp-clients/${id}`,
      { body, signal },
    );
  },
  deleteMcpClient(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/mcp-clients/${id}`, {
      signal,
    });
  },
  discoverMcpTools(id: number | string, signal?: AbortSignal) {
    return api.request<McpDiscoverOut>(
      "POST",
      `/api/v1/admin/ai/mcp-clients/${id}/discover`,
      {
        signal,
      },
    );
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
      await api.request<AiQueuePolicyOut[]>(
        "GET",
        "/api/v1/admin/ai/queue-policies",
        { signal },
      ),
    ),
  createQueuePolicy(body: AiQueuePolicyCreate, signal?: AbortSignal) {
    return api.request<AiQueuePolicyOut>(
      "POST",
      "/api/v1/admin/ai/queue-policies",
      {
        body,
        signal,
      },
    );
  },
  updateQueuePolicy(
    id: number | string,
    body: AiQueuePolicyUpdate,
    signal?: AbortSignal,
  ) {
    return api.request<AiQueuePolicyOut>(
      "PUT",
      `/api/v1/admin/ai/queue-policies/${id}`,
      {
        body,
        signal,
      },
    );
  },
  deleteQueuePolicy(id: number | string, signal?: AbortSignal) {
    return api.request<void>(
      "DELETE",
      `/api/v1/admin/ai/queue-policies/${id}`,
      { signal },
    );
  },

  listPromptParts(policyId: number | string, signal?: AbortSignal) {
    return api.request<AiPromptPartOut[]>(
      "GET",
      `/api/v1/admin/ai/queues/${policyId}/prompt-parts`,
      { signal },
    );
  },
  createPromptPart(
    policyId: number | string,
    body: AiPromptPartCreate,
    signal?: AbortSignal,
  ) {
    return api.request<AiPromptPartOut>(
      "POST",
      `/api/v1/admin/ai/queues/${policyId}/prompt-parts`,
      { body, signal },
    );
  },
  updatePromptPart(
    policyId: number | string,
    partId: number,
    body: AiPromptPartUpdate,
    signal?: AbortSignal,
  ) {
    return api.request<AiPromptPartOut>(
      "PUT",
      `/api/v1/admin/ai/queues/${policyId}/prompt-parts/${partId}`,
      { body, signal },
    );
  },
  deletePromptPart(
    policyId: number | string,
    partId: number,
    signal?: AbortSignal,
  ) {
    return api.request<void>(
      "DELETE",
      `/api/v1/admin/ai/queues/${policyId}/prompt-parts/${partId}`,
      { signal },
    );
  },
  /** Admin-only hard delete of an AI draft (any status) — distinct from the
   * agent-side discard, which only flips an open draft to `discarded`. */
  adminDeleteDraft(draftId: number, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/drafts/${draftId}`, {
      signal,
    });
  },
  /** Admin-only: drop a ticket's stored AI summary (state-only, next run
   * starts from scratch). */
  adminDeleteSummary(ticketId: number, signal?: AbortSignal) {
    return api.request<void>(
      "DELETE",
      `/api/v1/admin/ai/summaries/${ticketId}`,
      { signal },
    );
  },
  testEscalationRules(body: EscalationTestIn, signal?: AbortSignal) {
    return api.request<EscalationTestOut>(
      "POST",
      "/api/v1/admin/ai/escalation-test",
      {
        body,
        signal,
      },
    );
  },
  reorderPromptParts(
    policyId: number | string,
    orderedIds: number[],
    signal?: AbortSignal,
  ) {
    return api.request<AiPromptPartOut[]>(
      "PUT",
      `/api/v1/admin/ai/queues/${policyId}/prompt-parts/reorder`,
      { body: { ordered_ids: orderedIds }, signal },
    );
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

  listLimits(signal?: AbortSignal) {
    return api.request<Schemas["AiLimitsOut"]>("GET", "/api/v1/admin/ai/limits", { signal });
  },

  listAcl(signal?: AbortSignal) {
    return api.request<AiAclOut[]>("GET", "/api/v1/admin/ai/acl", { signal });
  },
  createAcl(body: AiAclCreate, signal?: AbortSignal) {
    return api.request<AiAclOut>("POST", "/api/v1/admin/ai/acl", {
      body,
      signal,
    });
  },
  updateAcl(id: number | string, body: AiAclUpdate, signal?: AbortSignal) {
    return api.request<AiAclOut>("PUT", `/api/v1/admin/ai/acl/${id}`, {
      body,
      signal,
    });
  },
  deleteAcl(id: number | string, signal?: AbortSignal) {
    return api.request<void>("DELETE", `/api/v1/admin/ai/acl/${id}`, {
      signal,
    });
  },

  listAuditLog(params: AiAuditLogListParams = {}, signal?: AbortSignal) {
    return api.request<AiAuditLogPageOut>("GET", "/api/v1/admin/ai/audit", {
      query: {
        from: params.from,
        to: params.to,
        provider_id: params.provider_id,
        feature: params.feature,
        ticket: params.ticket,
        status: params.status,
        page: params.page,
        page_size: params.page_size,
      },
      signal,
    });
  },
  getAuditLogStats(params: AiAuditLogFilterParams = {}, signal?: AbortSignal) {
    return api.request<AiAuditLogStatsOut>(
      "GET",
      "/api/v1/admin/ai/audit/stats",
      {
        query: {
          from: params.from,
          to: params.to,
          provider_id: params.provider_id,
          feature: params.feature,
          ticket: params.ticket,
        },
        signal,
      },
    );
  },
  getAuditLogEntry(id: number | string, signal?: AbortSignal) {
    return api.request<AiAuditLogDetailOut>(
      "GET",
      `/api/v1/admin/ai/audit/${id}`,
      { signal },
    );
  },
  revealAuditPii(id: number | string, signal?: AbortSignal) {
    return api.request<PiiRevealOut>(
      "POST",
      `/api/v1/admin/ai/audit/${id}/reveal-pii`,
      {
        signal,
      },
    );
  },
};
