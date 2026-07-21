/**
 * Thin typed fetch wrapper for Tiqora REST /api/v1.
 *
 * - credentials: 'include' for session cookies
 * - normalises errors to ApiError
 * - optional 401 → login redirect handler
 */

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type UserMe = Schemas["UserMe"];
export type LoginRequest = Schemas["LoginRequest"];
export type LoginResponse = Schemas["LoginResponse"];
export type AuthMethodsOut = Schemas["AuthMethodsOut"];
export type TOTPCodeIn = Schemas["TOTPCodeIn"];
export type TOTPEnrollOut = Schemas["TOTPEnrollOut"];
export type TOTPStatusOut = Schemas["TOTPStatusOut"];
export type QueueNode = Schemas["QueueNode"];
export type QueueCounts = Schemas["QueueCounts"];
export type TicketListItem = Schemas["TicketListItem"];
export type PaginatedTickets = Schemas["PaginatedTickets"];
export type MyTicketCounts = Schemas["MyTicketCounts"];
// Hand-written (see the Stats block below for why we don't regenerate
// schema.d.ts): mirrors the DashboardSummary model in tiqora/api/v1/tickets.py.
export type DashboardSummary = {
  my_open: number;
  my_new: number;
  unowned_new: number;
  escalated: number;
};
export type TicketDetail = Schemas["TicketDetail"];
export type ArticleListItem = Schemas["ArticleListItem"];
export type ArticleBody = Schemas["ArticleBody"];
export type AttachmentMetaOut = Schemas["AttachmentMetaOut"];
export type HistoryEntry = Schemas["HistoryEntry"];
export type CustomerUserOut = Schemas["CustomerUserOut"];
export type SearchHit = Schemas["SearchHit"];
export type SearchResponse = Schemas["SearchResponse"];
export type DynamicFieldValueOut = Schemas["DynamicFieldValueOut"];
export type PresenceIn = Schemas["PresenceIn"];
export type PresenceEntry = Schemas["PresenceEntry"];
export type ArticleCreateRequest = Schemas["ArticleCreateRequest"];
export type ArticleCreateResponse = Schemas["ArticleCreateResponse"];
export type ReplyDraftOut = Schemas["ReplyDraftOut"];
export type TemplateOut = Schemas["TemplateOut"];
export type MutationRequest = Schemas["MutationRequest"];
export type MergeRequest = Schemas["MergeRequest"];
export type TicketCreateRequest = Schemas["TicketCreateRequest"];
export type TicketCreateResponse = Schemas["TicketCreateResponse"];
export type ForwardRequest = Schemas["ForwardRequest"];
export type BounceRequest = Schemas["BounceRequest"];
export type SplitRequest = Schemas["SplitRequest"];
export type TicketLinkTargetOut = Schemas["TicketLinkTargetOut"];
export type TicketLinkCreateRequest = Schemas["TicketLinkCreateRequest"];

// ── Portal ────────────────────────────────────────────────────────────────
export type CustomerMe = Schemas["CustomerMe"];
export type CustomerLoginResponse = Schemas["CustomerLoginResponse"];
export type PortalArticleOut = Schemas["PortalArticleOut"];
export type PortalReplyRequest = Schemas["PortalReplyRequest"];
export type PortalReplyResponse = Schemas["PortalReplyResponse"];
export type PortalTicketCreateRequest = Schemas["PortalTicketCreateRequest"];
export type PortalTicketCreateResponse = Schemas["PortalTicketCreateResponse"];
export type PortalAttachmentUploadResponse = Schemas["PortalAttachmentUploadResponse"];
export type KbSearchHit = Schemas["KbSearchHit"];
export type KbSearchResponse = Schemas["KbSearchResponse"];

// ── Knowledge base (agent) ───────────────────────────────────────────────
export type CategoryOut = Schemas["CategoryOut"];
export type CategoryIn = Schemas["CategoryIn"];
export type CategoryUpdateIn = Schemas["CategoryUpdateIn"];
export type KbArticleOut = Schemas["ArticleOut"];
export type KbArticleIn = Schemas["ArticleIn"];
export type KbArticleUpdateIn = Schemas["ArticleUpdateIn"];
export type ArticleSummary = Schemas["ArticleSummary"];
export type ArticleVersionOut = Schemas["ArticleVersionOut"];
export type AssignableGroup = Schemas["AssignableGroup"];
export type KbAttachmentOut = Schemas["AttachmentOut"];
export type KnowledgeArticle = Schemas["KnowledgeArticle"];
export type KnowledgeBundle = Schemas["KnowledgeBundle"];

// ── Admin ─────────────────────────────────────────────────────────────────
/** Validity filter for admin resource lists; defaults to hiding invalid rows. */
export type AdminValidFilter = "valid" | "invalid" | "all";

/** Query params for a paginated admin list. */
export type AdminListParams = {
  page?: number;
  pageSize?: number;
  valid?: AdminValidFilter;
  /** Optional server-side substring search (customer users / companies). */
  search?: string;
};

/** Paginated envelope returned by every admin resource list endpoint. */
export type AdminPage<Out> = {
  items: Out[];
  total: number;
  page: number;
  page_size: number;
};

export type UserOut = Schemas["UserOut"];
export type UserCreate = Schemas["UserCreate"];
export type UserUpdate = Schemas["UserUpdate"];
export type GroupOut = Schemas["GroupOut"];
export type GroupCreate = Schemas["GroupCreate"];
export type GroupUpdate = Schemas["GroupUpdate"];
export type GroupAssignment = Schemas["GroupAssignment"];
export type RoleOut = Schemas["RoleOut"];
export type RoleCreate = Schemas["RoleCreate"];
export type RoleUpdate = Schemas["RoleUpdate"];
export type RoleAssignment = Schemas["RoleAssignment"];
export type GroupRoleAssignment = Schemas["GroupRoleAssignment"];
export type QueueOut = Schemas["QueueOut"];
export type QueueCreate = Schemas["QueueCreate"];
export type QueueUpdate = Schemas["QueueUpdate"];
export type StateOut = Schemas["StateOut"];
export type StateCreate = Schemas["StateCreate"];
export type StateUpdate = Schemas["StateUpdate"];
export type PriorityOut = Schemas["PriorityOut"];
export type PriorityCreate = Schemas["PriorityCreate"];
export type PriorityUpdate = Schemas["PriorityUpdate"];
export type CustomerUserAdminOut = Schemas["CustomerUserAdminOut"];
export type CustomerUserAdminCreate = Schemas["CustomerUserAdminCreate"];
export type CustomerUserAdminUpdate = Schemas["CustomerUserAdminUpdate"];
export type CustomerCompanyOut = Schemas["CustomerCompanyOut"];
export type CustomerCompanyCreate = Schemas["CustomerCompanyCreate"];
export type CustomerCompanyUpdate = Schemas["CustomerCompanyUpdate"];
export type SalutationOut = Schemas["SalutationOut"];
export type SalutationWrite = Schemas["SalutationWrite"];
export type SalutationUpdate = Schemas["SalutationUpdate"];
export type SignatureOut = Schemas["SignatureOut"];
export type SignatureWrite = Schemas["SignatureWrite"];
export type SignatureUpdate = Schemas["SignatureUpdate"];
export type StandardTemplateOut = Schemas["StandardTemplateOut"];
export type StandardTemplateCreate = Schemas["StandardTemplateCreate"];
export type StandardTemplateUpdate = Schemas["StandardTemplateUpdate"];
// Hand-written (do not regenerate schema.d.ts): standard_attachment master +
// template/attachment + customer-user/group assignment editors.
export type StandardAttachmentOut = {
  id: number;
  name: string;
  content_type: string;
  /** Base64-encoded blob body. */
  content: string;
  filename: string;
  comments: string | null;
  valid_id: number;
  create_time: string;
  change_time: string;
};
export type StandardAttachmentCreate = {
  name: string;
  content_type: string;
  /** Base64-encoded blob body. */
  content: string;
  filename: string;
  comments?: string | null;
  valid_id?: number;
};
export type StandardAttachmentUpdate = {
  name?: string | null;
  content_type?: string | null;
  content?: string | null;
  filename?: string | null;
  comments?: string | null;
  valid_id?: number | null;
};
/** Slim attachment row for the template↔attachments editor (no blob). */
export type AttachmentRefOut = {
  id: number;
  name: string;
  filename: string;
  content_type: string;
};
export type TemplateAttachmentsReplace = {
  attachment_ids: number[];
};
/** Customer-user ↔ group grant (group_customer_user; login string identity). */
export type CustomerUserGroupAssignment = {
  group_id: number;
  permission_key: "ro" | "rw";
  permission_value?: number;
};
export type AutoResponseOut = Schemas["AutoResponseOut"];
export type AutoResponseCreate = Schemas["AutoResponseCreate"];
export type AutoResponseUpdate = Schemas["AutoResponseUpdate"];
export type DynamicFieldOut = Schemas["DynamicFieldOut"];
export type DynamicFieldCreate = Schemas["DynamicFieldCreate"];
export type DynamicFieldUpdate = Schemas["DynamicFieldUpdate"];
export type WebhookOut = Schemas["WebhookOut"];
export type WebhookCreate = Schemas["WebhookCreate"];
export type WebhookUpdate = Schemas["WebhookUpdate"];
// Placeholder variables (regenerated into schema.d.ts from openapi.json).
export type QueueVariableOut = Schemas["QueueVariableOut"];
export type QueueVariableCreate = Schemas["QueueVariableCreate"];
export type QueueVariableUpdate = Schemas["QueueVariableUpdate"];
export type PhysicalQueueVariableOut = Schemas["PhysicalQueueVariableOut"];
export type PlaceholderFieldOut = Schemas["PlaceholderFieldOut"];
export type PlaceholderFieldCreate = Schemas["PlaceholderFieldCreate"];
export type PlaceholderFieldUpdate = Schemas["PlaceholderFieldUpdate"];
// Hand-written until openapi.json is regenerated (schemas also appear there).
export type MailSecurity = "none" | "starttls" | "ssl";
export type MailAuthType = "none" | "password";
export type MailOutboundOut = {
  enabled: boolean;
  host: string;
  port: number;
  security: MailSecurity;
  auth_type: MailAuthType;
  auth_user: string;
  has_password: boolean;
  from_default: string;
  timeout_seconds: number;
  change_time?: string | null;
  change_by?: number | null;
};
export type MailOutboundUpdate = {
  enabled?: boolean | null;
  host?: string | null;
  port?: number | null;
  security?: MailSecurity | null;
  auth_type?: MailAuthType | null;
  auth_user?: string | null;
  /** Write-only; omit or empty keeps the stored password. */
  auth_password?: string | null;
  from_default?: string | null;
  timeout_seconds?: number | null;
};
export type MailOutboundTestIn = {
  to_address?: string | null;
};
export type MailOutboundTestOut = {
  ok: boolean;
  message: string;
  detail?: string | null;
};
export type MailLogDirection = "in" | "out";
export type MailLogStatus = "queued" | "sent" | "failed" | "received" | "filtered";
export type MailLogOut = {
  id: number;
  created_at: string;
  direction: string;
  status: string;
  from_addr: string;
  to_addr: string;
  cc_addr?: string | null;
  subject: string;
  message_id?: string | null;
  ticket_id?: number | null;
  article_id?: number | null;
  queue?: string | null;
  smtp_code?: number | null;
  detail?: string | null;
  duration_ms?: number | null;
};
export type MailLogListParams = {
  page?: number;
  pageSize?: number;
  direction?: MailLogDirection | null;
  status?: MailLogStatus | null;
  q?: string | null;
  /** ISO datetime lower bound (query param ``from``). */
  from?: string | null;
  /** ISO datetime upper bound (query param ``to``). */
  to?: string | null;
};
export type PostmasterFilterOut = Schemas["PostmasterFilterOut"];
export type AclOut = Schemas["AclOut"];
export type GenericAgentJobOut = Schemas["GenericAgentJobOut"];

// ── Stats ────────────────────────────────────────────────────────────────
// Hand-written (not generated from schema.d.ts): openapi.json/schema.d.ts
// are not currently kept in sync with every backend subsystem in this
// monorepo, so these mirror tiqora/stats/schemas.py directly rather than
// forcing a full openapi.json regeneration (which would otherwise pull in
// every other in-flight backend feature's routes as an unrelated diff).
export type VolumePointOut = { bucket: string; created: number; closed: number };
export type TicketVolumeOut = { granularity: string; points: VolumePointOut[] };
export type DimensionCountOut = { id: number | null; label: string; count: number };
export type OpenSnapshotOut = { dimension: string; items: DimensionCountOut[] };
export type SlaStatsOut = {
  total: number;
  escalated: number;
  first_response_breached: number;
  update_breached: number;
  solution_breached: number;
  first_response_minutes: number[];
  solution_minutes: number[];
};
export type AgentWorkloadItemOut = {
  user_id: number;
  login: string;
  name: string;
  owned_open: number;
  closed_in_period: number;
};
export type BacklogPointOut = { bucket: string; open_count: number };
export type BacklogTrendOut = { granularity: string; points: BacklogPointOut[] };
export type StatsGranularity = "day" | "week" | "month";
export type StatsDimension = "queue" | "state" | "priority" | "owner";

export type StatsFilterParams = {
  date_from?: string;
  date_to?: string;
  queue_id?: number;
  state_id?: number;
  priority_id?: number;
  type_id?: number;
  customer_id?: string;
};

// ── Calendar ─────────────────────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/calendar/schemas.py directly rather than requiring an openapi.json
// regeneration.
export type CalendarOut = {
  id: number;
  group_id: number;
  name: string;
  color: string;
  valid: boolean;
};

export type RecurrenceIn = {
  type: "Daily" | "Weekly" | "Monthly" | "Yearly";
  interval?: number;
  count?: number | null;
  until?: string | null;
};

export type AppointmentIn = {
  calendar_id: number;
  title: string;
  description?: string | null;
  location?: string | null;
  start_time: string;
  end_time: string;
  all_day?: boolean;
  team_id?: string | null;
  resource_id?: string | null;
  recurrence?: RecurrenceIn | null;
};

export type AppointmentUpdateIn = {
  title?: string | null;
  description?: string | null;
  location?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  all_day?: boolean | null;
  team_id?: string | null;
  resource_id?: string | null;
  recurrence?: RecurrenceIn | null;
  clear_recurrence?: boolean;
};

export type AppointmentOut = {
  id: number;
  parent_id: number | null;
  calendar_id: number;
  unique_id: string;
  title: string;
  description: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  all_day: boolean;
  team_id: string | null;
  resource_id: string | null;
  recurring: boolean;
  recur_type: string | null;
  recur_interval: number | null;
  recur_count: number | null;
  recur_until: string | null;
  create_time: string | null;
  change_time: string | null;
};

export type OccurrenceOut = {
  appointment_id: number;
  calendar_id: number;
  title: string;
  description: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  all_day: boolean;
  is_recurring: boolean;
};

export type TicketLinkOut = {
  appointment_id: number;
  calendar_id: number;
  ticket_id: number;
  rule_id: string;
};

// ── ProcessManagement (BPM) ─────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/process/schemas.py directly rather than requiring an openapi.json
// regeneration.
export type ProcessSummaryOut = {
  id: number;
  entity_id: string;
  name: string;
  state_entity_id: string;
};

export type ActivityDialogSummaryOut = {
  entity_id: string;
  name: string;
  description_short: string;
};

export type ActivityDialogRefOut = {
  entity_id: string;
  name: string;
};

export type ProcessActivityOut = {
  entity_id: string;
  name: string;
  activity_dialogs: ActivityDialogRefOut[];
};

export type ProcessDetailOut = {
  id: number;
  entity_id: string;
  name: string;
  state_entity_id: string;
  start_activity_entity_id: string | null;
  activities: ProcessActivityOut[];
};

export type TicketProcessStateOut = {
  process_entity_id: string | null;
  process_name: string | null;
  activity_entity_id: string | null;
  activity_name: string | null;
  available_dialogs: ActivityDialogSummaryOut[];
  available_transitions_count: number;
};

export type ActivityDialogFieldOut = {
  display: string;
  default_value: unknown;
  description_short: string;
  description_long: string;
  config: Record<string, unknown>;
};

export type ActivityDialogDetailOut = {
  entity_id: string;
  name: string;
  description_short: string;
  description_long: string;
  field_order: string[];
  fields: Record<string, ActivityDialogFieldOut>;
  submit_advice_text: string;
  submit_button_text: string;
};

export type ProcessStartIn = {
  process_entity_id: string;
};

export type ActivityDialogSubmitIn = {
  activity_dialog_entity_id: string;
  field_values: Record<string, unknown>;
};

export type ActivityDialogSubmitOut = {
  activity_changed: boolean;
  new_activity_entity_id: string | null;
  transition_entity_id: string | null;
  unsupported_actions: string[];
  state: TicketProcessStateOut;
};

// ── Reference (agent pickers) ────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/api/v1/reference.py directly rather than requiring an openapi.json
// regeneration (which would pull in every other in-flight backend route as an
// unrelated diff).
export type PriorityRef = { id: number; name: string };
export type StateRef = { id: number; name: string; type_name: string };
export type AgentRef = { id: number; login: string; full_name: string };
export type CustomerRef = {
  login: string;
  email: string;
  customer_id: string;
  full_name: string;
};
export type QueueRef = { id: number; name: string };

/** Compact ticket hit for agent link/merge pickers (GET /api/v1/tickets/search). */
export type TicketSearchHit = {
  ticket_id: number;
  tn: string;
  title: string;
  queue?: string | null;
  state?: string | null;
  state_type?: string | null;
};

/** Agent-side customer-user create body (POST /api/v1/customers). */
export type AgentCustomerCreateInput = {
  login: string;
  email: string;
  first_name: string;
  last_name: string;
  customer_id: string;
  phone?: string | null;
};

export type AgentCustomerCreateOut = {
  login: string;
  email: string;
  customer_id: string;
  first_name: string;
  last_name: string;
};

// Hand-written to match tiqora/api/v1/tickets.py's TicketCreateRequest, which
// the generated schema for this route does not yet reflect (agent create needs
// queue/state/priority/owner, not the portal-style title/body). The initial
// message is added as a separate article after creation. See the Stats/Ref
// blocks above for why we hand-write instead of regenerating openapi.json.
export type AgentTicketCreateInput = {
  title: string;
  queue_id: number;
  state_id: number;
  priority_id: number;
  owner_id: number;
  customer_user_id?: string | null;
};

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly path: string;

  constructor(status: number, detail: unknown, path: string) {
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "detail" in detail
          ? String((detail as { detail: unknown }).detail)
          : `HTTP ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export type ApiClientOptions = {
  /** API origin, e.g. "" for same-origin / Vite proxy, or "http://localhost:8000" */
  baseUrl?: string;
  /** Called on 401 before the error is thrown (except for login itself). */
  onUnauthorized?: () => void;
  /** Paths that must not trigger onUnauthorized (default: login). */
  skipAuthRedirectPaths?: string[];
  fetch?: typeof fetch;
};

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

function joinUrl(base: string, path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const b = base.replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${b}${p}`;
}

export class ApiClient {
  readonly baseUrl: string;
  private readonly onUnauthorized?: () => void;
  private readonly skipAuthRedirectPaths: string[];
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? "";
    this.onUnauthorized = options.onUnauthorized;
    this.skipAuthRedirectPaths = options.skipAuthRedirectPaths ?? [
      "/api/v1/auth/login",
      "/api/v1/auth/me",
      "/api/portal/auth/login",
      "/api/portal/auth/me",
    ];
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async request<T>(
    method: HttpMethod,
    path: string,
    init?: {
      body?: unknown;
      query?: Record<string, string | number | boolean | null | undefined>;
      headers?: Record<string, string>;
      signal?: AbortSignal;
    },
  ): Promise<T> {
    let url = joinUrl(this.baseUrl, path);
    if (init?.query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(init.query)) {
        if (v === undefined || v === null || v === "") continue;
        qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += (url.includes("?") ? "&" : "?") + s;
    }

    const headers: Record<string, string> = {
      Accept: "application/json",
      ...init?.headers,
    };
    let body: BodyInit | undefined;
    if (init?.body instanceof FormData) {
      body = init.body;
    } else if (init?.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }

    const res = await this.fetchImpl(url, {
      method,
      headers,
      body,
      credentials: "include",
      signal: init?.signal,
    });

    if (res.status === 204) {
      return undefined as T;
    }

    const contentType = res.headers.get("content-type") ?? "";
    const isJson = contentType.includes("application/json");
    const payload = isJson
      ? await res.json().catch(() => null)
      : await res.text().catch(() => null);

    if (!res.ok) {
      if (
        res.status === 401 &&
        this.onUnauthorized &&
        !this.skipAuthRedirectPaths.some((p) => path.startsWith(p))
      ) {
        this.onUnauthorized();
      }
      throw new ApiError(res.status, payload ?? res.statusText, path);
    }

    return payload as T;
  }

  // ── Auth ──────────────────────────────────────────────────────────────

  login(body: LoginRequest, signal?: AbortSignal) {
    return this.request<LoginResponse>("POST", "/api/v1/auth/login", {
      body,
      signal,
    });
  }

  me(signal?: AbortSignal) {
    return this.request<UserMe>("GET", "/api/v1/auth/me", { signal });
  }

  logout(signal?: AbortSignal) {
    return this.request<void>("POST", "/api/v1/auth/logout", { signal });
  }

  authMethods(signal?: AbortSignal) {
    return this.request<AuthMethodsOut>("GET", "/api/v1/auth/methods", { signal });
  }

  totpVerify(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<LoginResponse>("POST", "/api/v1/auth/totp/verify", {
      body,
      signal,
    });
  }

  totpEnroll(signal?: AbortSignal) {
    return this.request<TOTPEnrollOut>("POST", "/api/v1/auth/totp/enroll", { signal });
  }

  totpConfirm(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("POST", "/api/v1/auth/totp/confirm", {
      body,
      signal,
    });
  }

  totpDisable(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("DELETE", "/api/v1/auth/totp", {
      body,
      signal,
    });
  }

  totpStatus(signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("GET", "/api/v1/auth/totp/status", { signal });
  }

  /** Browser-navigates to the OIDC provider; not a fetch (redirect flow). */
  oidcLoginUrl(): string {
    return "/api/v1/auth/oidc/login";
  }

  // ── Queues ────────────────────────────────────────────────────────────

  listQueues(signal?: AbortSignal) {
    return this.request<QueueNode[]>("GET", "/api/v1/queues", { signal });
  }

  // ── Tickets ───────────────────────────────────────────────────────────

  listTickets(
    params: {
      queue_id?: number;
      state_id?: number;
      state_type?: string;
      owner_id?: number;
      offset?: number;
      limit?: number;
      sort?: string;
      order?: string;
    } = {},
    signal?: AbortSignal,
  ) {
    return this.request<PaginatedTickets>("GET", "/api/v1/tickets", {
      query: params,
      signal,
    });
  }

  /** Open/new counts for tickets owned by the current agent (nav badges). */
  myTicketCounts(signal?: AbortSignal) {
    return this.request<MyTicketCounts>("GET", "/api/v1/tickets/my-counts", {
      signal,
    });
  }

  /** KPI-tile counts for the agent dashboard: owned open/new, unclaimed new, escalated. */
  dashboardSummary(signal?: AbortSignal) {
    return this.request<DashboardSummary>("GET", "/api/v1/tickets/dashboard-summary", {
      signal,
    });
  }

  /**
   * Permission-scoped ticket search for link/merge pickers.
   * Matches tn + title; only queues the agent has at least `ro` on.
   */
  searchTickets(
    params: { q: string; limit?: number },
    signal?: AbortSignal,
  ) {
    return this.request<TicketSearchHit[]>("GET", "/api/v1/tickets/search", {
      query: params,
      signal,
    });
  }

  getTicket(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketDetail>(
      "GET",
      `/api/v1/tickets/${ticketId}`,
      { signal },
    );
  }

  listArticles(ticketId: number, signal?: AbortSignal) {
    return this.request<ArticleListItem[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles`,
      { signal },
    );
  }

  getArticleBody(ticketId: number, articleId: number, signal?: AbortSignal) {
    return this.request<ArticleBody>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/body`,
      { signal },
    );
  }

  listAttachments(ticketId: number, articleId: number, signal?: AbortSignal) {
    return this.request<AttachmentMetaOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/attachments`,
      { signal },
    );
  }

  attachmentDownloadUrl(
    ticketId: number,
    articleId: number,
    attachmentId: number,
    download = true,
  ): string {
    const q = download ? "?download=true" : "";
    return joinUrl(
      this.baseUrl,
      `/api/v1/tickets/${ticketId}/articles/${articleId}/attachments/${attachmentId}${q}`,
    );
  }

  listHistory(
    ticketId: number,
    order: "asc" | "desc" = "desc",
    signal?: AbortSignal,
  ) {
    return this.request<HistoryEntry[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/history?order=${order}`,
      { signal },
    );
  }

  createArticle(ticketId: number, body: ArticleCreateRequest, signal?: AbortSignal) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles`,
      { body, signal },
    );
  }

  getReplyDraft(
    ticketId: number,
    articleId: number,
    replyAll = false,
    signal?: AbortSignal,
  ) {
    return this.request<ReplyDraftOut>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/reply-draft?reply_all=${replyAll}`,
      { signal },
    );
  }

  listTemplates(ticketId: number, signal?: AbortSignal) {
    return this.request<TemplateOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/templates`,
      { signal },
    );
  }

  patchTicket(ticketId: number, body: MutationRequest, signal?: AbortSignal) {
    return this.request<void>("PATCH", `/api/v1/tickets/${ticketId}`, {
      body,
      signal,
    });
  }

  mergeTicket(ticketId: number, body: MergeRequest, signal?: AbortSignal) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/merge`, {
      body,
      signal,
    });
  }

  createTicket(body: AgentTicketCreateInput, signal?: AbortSignal) {
    return this.request<TicketCreateResponse>("POST", "/api/v1/tickets", {
      body,
      signal,
    });
  }

  forwardArticle(
    ticketId: number,
    articleId: number,
    body: ForwardRequest,
    signal?: AbortSignal,
  ) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/forward`,
      { body, signal },
    );
  }

  bounceArticle(
    ticketId: number,
    articleId: number,
    body: BounceRequest,
    signal?: AbortSignal,
  ) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/bounce`,
      { body, signal },
    );
  }

  splitArticle(
    ticketId: number,
    articleId: number,
    body: SplitRequest,
    signal?: AbortSignal,
  ) {
    return this.request<TicketCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/split`,
      { body, signal },
    );
  }

  listTicketLinks(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketLinkTargetOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/links`,
      { signal },
    );
  }

  createTicketLink(
    ticketId: number,
    body: TicketLinkCreateRequest,
    signal?: AbortSignal,
  ) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/links`, {
      body,
      signal,
    });
  }

  postPresence(ticketId: number, body: PresenceIn, signal?: AbortSignal) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/presence`, {
      body,
      signal,
    });
  }

  getPresence(ticketId: number, signal?: AbortSignal) {
    return this.request<PresenceEntry[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/presence`,
      { signal },
    );
  }

  /**
   * Build the CSV export URL for the current ticket-list filters. Consumed
   * via a plain navigation/anchor (cookie-authenticated download), not a
   * fetch call — mirrors {@link ApiClient.eventStreamUrl}.
   */
  exportTicketsCsvUrl(
    params: {
      queue_id?: number;
      state_id?: number;
      state_type?: string;
      owner_id?: number;
      sort?: string;
      order?: string;
    } = {},
  ): string {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const suffix = qs.toString();
    return joinUrl(
      this.baseUrl,
      `/api/v1/tickets/export.csv${suffix ? `?${suffix}` : ""}`,
    );
  }

  // ── Realtime events (SSE) ────────────────────────────────────────────────
  // Consumed via the browser EventSource API directly, not a fetch call —
  // this just builds the URL against the configured base URL/credentials.

  eventStreamUrl(): string {
    return joinUrl(this.baseUrl, "/api/v1/events/stream");
  }

  // ── Customers ─────────────────────────────────────────────────────────

  getCustomer(login: string, signal?: AbortSignal) {
    return this.request<CustomerUserOut>(
      "GET",
      `/api/v1/customers/${encodeURIComponent(login)}`,
      { signal },
    );
  }

  /**
   * Create a customer_user as any authenticated agent (ticket "Kunde anlegen").
   * No password — contact record only; portal auth is separate.
   */
  createCustomer(body: AgentCustomerCreateInput, signal?: AbortSignal) {
    return this.request<AgentCustomerCreateOut>("POST", "/api/v1/customers", {
      body,
      signal,
    });
  }

  // ── Reference (agent pickers, /api/v1/reference) ─────────────────────────

  listReferencePriorities(signal?: AbortSignal) {
    return this.request<PriorityRef[]>("GET", "/api/v1/reference/priorities", { signal });
  }

  listReferenceStates(signal?: AbortSignal) {
    return this.request<StateRef[]>("GET", "/api/v1/reference/states", { signal });
  }

  listReferenceAgents(signal?: AbortSignal) {
    return this.request<AgentRef[]>("GET", "/api/v1/reference/agents", { signal });
  }

  searchReferenceCustomers(
    params: { q?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<CustomerRef[]>("GET", "/api/v1/reference/customers", {
      query: params,
      signal,
    });
  }

  /**
   * Queues for agent pickers. Pass `movable: true` for the Verschieben
   * (move) picker — only valid queues the agent has `rw` on.
   */
  listReferenceQueues(
    params: { movable?: boolean } = {},
    signal?: AbortSignal,
  ) {
    return this.request<QueueRef[]>("GET", "/api/v1/reference/queues", {
      query: params,
      signal,
    });
  }

  // ── Search ────────────────────────────────────────────────────────────

  search(
    params: { q: string; offset?: number; limit?: number },
    signal?: AbortSignal,
  ) {
    return this.request<SearchResponse>("GET", "/api/v1/search", {
      query: params,
      signal,
    });
  }

  // ── Knowledge base (agent, /api/v1/kb) ──────────────────────────────────

  listKbCategories(signal?: AbortSignal) {
    return this.request<CategoryOut[]>("GET", "/api/v1/kb/categories", { signal });
  }

  createKbCategory(body: CategoryIn, signal?: AbortSignal) {
    return this.request<CategoryOut>("POST", "/api/v1/kb/categories", { body, signal });
  }

  updateKbCategory(categoryId: number, body: CategoryUpdateIn, signal?: AbortSignal) {
    return this.request<CategoryOut>("PATCH", `/api/v1/kb/categories/${categoryId}`, {
      body,
      signal,
    });
  }

  deleteKbCategory(categoryId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/kb/categories/${categoryId}`, { signal });
  }

  listAssignableGroups(signal?: AbortSignal) {
    return this.request<AssignableGroup[]>("GET", "/api/v1/kb/assignable-groups", { signal });
  }

  listKbArticles(
    params: { category_id?: number; state?: string; tag?: string } = {},
    signal?: AbortSignal,
  ) {
    return this.request<ArticleSummary[]>("GET", "/api/v1/kb/articles", {
      query: params,
      signal,
    });
  }

  /** ACL-filtered agent-knowledge bundle selected by tag(s) and/or category. */
  getKbKnowledge(
    params: {
      tags?: string;
      category_id?: number;
      state?: string;
      include_content?: boolean;
    } = {},
    signal?: AbortSignal,
  ) {
    return this.request<KnowledgeBundle>("GET", "/api/v1/kb/knowledge", {
      query: params,
      signal,
    });
  }

  createKbArticle(body: KbArticleIn, signal?: AbortSignal) {
    return this.request<KbArticleOut>("POST", "/api/v1/kb/articles", { body, signal });
  }

  getKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<KbArticleOut>("GET", `/api/v1/kb/articles/${articleId}`, { signal });
  }

  updateKbArticle(articleId: number, body: KbArticleUpdateIn, signal?: AbortSignal) {
    return this.request<KbArticleOut>("PATCH", `/api/v1/kb/articles/${articleId}`, {
      body,
      signal,
    });
  }

  deleteKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/kb/articles/${articleId}`, { signal });
  }

  publishKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<KbArticleOut>("POST", `/api/v1/kb/articles/${articleId}/publish`, {
      signal,
    });
  }

  listKbArticleVersions(articleId: number, signal?: AbortSignal) {
    return this.request<ArticleVersionOut[]>(
      "GET",
      `/api/v1/kb/articles/${articleId}/versions`,
      { signal },
    );
  }

  // KB article attachments (multipart field name `file`).
  listKbAttachments(articleId: number, signal?: AbortSignal) {
    return this.request<KbAttachmentOut[]>(
      "GET",
      `/api/v1/kb/articles/${articleId}/attachments`,
      { signal },
    );
  }

  uploadKbAttachment(articleId: number, file: File, signal?: AbortSignal) {
    const form = new FormData();
    form.append("file", file);
    return this.request<KbAttachmentOut>(
      "POST",
      `/api/v1/kb/articles/${articleId}/attachments`,
      { body: form, signal },
    );
  }

  kbAttachmentDownloadUrl(articleId: number, attachmentId: number): string {
    return joinUrl(
      this.baseUrl,
      `/api/v1/kb/articles/${articleId}/attachments/${attachmentId}`,
    );
  }

  deleteKbAttachment(articleId: number, attachmentId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/kb/articles/${articleId}/attachments/${attachmentId}`,
      { signal },
    );
  }

  searchKb(params: { q: string; limit?: number }, signal?: AbortSignal) {
    return this.request<KbSearchResponse>("GET", "/api/v1/kb/search", {
      query: params,
      signal,
    });
  }

  // ── Admin ────────────────────────────────────────────────────────────────

  private adminCrud<Out, Create, Update>(base: string) {
    return {
      list: (params?: AdminListParams, signal?: AbortSignal) =>
        this.request<AdminPage<Out>>("GET", base, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            valid: params?.valid,
            search: params?.search,
          },
          signal,
        }),
      create: (body: Create, signal?: AbortSignal) =>
        this.request<Out>("POST", base, { body, signal }),
      get: (id: number | string, signal?: AbortSignal) =>
        this.request<Out>("GET", `${base}/${id}`, { signal }),
      update: (id: number | string, body: Update, signal?: AbortSignal) =>
        this.request<Out>("PATCH", `${base}/${id}`, { body, signal }),
      deactivate: (id: number | string, signal?: AbortSignal) =>
        this.request<void>("DELETE", `${base}/${id}`, { signal }),
    };
  }

  get adminUsers() {
    return this.adminCrud<UserOut, UserCreate, UserUpdate>("/api/v1/admin/users");
  }

  assignUserGroup(userId: number, body: GroupAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/users/${userId}/groups`, { body, signal });
  }

  revokeUserGroup(userId: number, groupId: number, permissionKey: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/users/${userId}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  assignUserRole(userId: number, body: RoleAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/users/${userId}/roles`, { body, signal });
  }

  revokeUserRole(userId: number, roleId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/users/${userId}/roles/${roleId}`, {
      signal,
    });
  }

  get adminGroups() {
    return this.adminCrud<GroupOut, GroupCreate, GroupUpdate>("/api/v1/admin/groups");
  }

  listGroupUsers(groupId: number, signal?: AbortSignal) {
    return this.request<UserOut[]>("GET", `/api/v1/admin/groups/${groupId}/users`, { signal });
  }

  listGroupRoles(groupId: number, signal?: AbortSignal) {
    return this.request<RoleOut[]>("GET", `/api/v1/admin/groups/${groupId}/roles`, { signal });
  }

  listGroupCustomerUsers(groupId: number, signal?: AbortSignal) {
    return this.request<CustomerUserAdminOut[]>(
      "GET",
      `/api/v1/admin/groups/${groupId}/customer-users`,
      { signal },
    );
  }

  get adminRoles() {
    return this.adminCrud<RoleOut, RoleCreate, RoleUpdate>("/api/v1/admin/roles");
  }

  listRoleUsers(roleId: number, signal?: AbortSignal) {
    return this.request<UserOut[]>("GET", `/api/v1/admin/roles/${roleId}/users`, { signal });
  }

  assignRoleGroup(roleId: number, body: GroupRoleAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/roles/${roleId}/groups`, { body, signal });
  }

  revokeRoleGroup(roleId: number, groupId: number, permissionKey: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/roles/${roleId}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  get adminQueues() {
    return this.adminCrud<QueueOut, QueueCreate, QueueUpdate>("/api/v1/admin/queues");
  }

  get adminStates() {
    return this.adminCrud<StateOut, StateCreate, StateUpdate>("/api/v1/admin/states");
  }

  get adminPriorities() {
    return this.adminCrud<PriorityOut, PriorityCreate, PriorityUpdate>(
      "/api/v1/admin/priorities",
    );
  }

  get adminCustomerUsers() {
    return this.adminCrud<CustomerUserAdminOut, CustomerUserAdminCreate, CustomerUserAdminUpdate>(
      "/api/v1/admin/customer-users",
    );
  }

  get adminCustomerCompanies() {
    return this.adminCrud<CustomerCompanyOut, CustomerCompanyCreate, CustomerCompanyUpdate>(
      "/api/v1/admin/customer-companies",
    );
  }

  listCustomerCompanyUsers(customerId: string, signal?: AbortSignal) {
    return this.request<CustomerUserAdminOut[]>(
      "GET",
      `/api/v1/admin/customer-companies/${encodeURIComponent(customerId)}/customer-users`,
      { signal },
    );
  }

  assignCustomerCompany(login: string, customerId: string, signal?: AbortSignal) {
    return this.request<void>(
      "PUT",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/companies`,
      { body: { customer_id: customerId }, signal },
    );
  }

  revokeCustomerCompany(login: string, customerId: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/companies/${encodeURIComponent(customerId)}`,
      { signal },
    );
  }

  get adminSalutations() {
    return this.adminCrud<SalutationOut, SalutationWrite, SalutationUpdate>(
      "/api/v1/admin/salutations",
    );
  }

  get adminSignatures() {
    return this.adminCrud<SignatureOut, SignatureWrite, SignatureUpdate>(
      "/api/v1/admin/signatures",
    );
  }

  get adminTemplates() {
    return this.adminCrud<StandardTemplateOut, StandardTemplateCreate, StandardTemplateUpdate>(
      "/api/v1/admin/templates",
    );
  }

  listQueueTemplates(queueId: number, signal?: AbortSignal) {
    return this.request<StandardTemplateOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/templates`,
      { signal },
    );
  }

  listTemplateQueues(templateId: number, signal?: AbortSignal) {
    return this.request<QueueOut[]>("GET", `/api/v1/admin/templates/${templateId}/queues`, {
      signal,
    });
  }

  assignQueueTemplate(queueId: number, standardTemplateId: number, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/queues/${queueId}/templates`, {
      body: { standard_template_id: standardTemplateId },
      signal,
    });
  }

  revokeQueueTemplate(queueId: number, standardTemplateId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/queues/${queueId}/templates/${standardTemplateId}`,
      { signal },
    );
  }

  get adminAttachments() {
    return this.adminCrud<
      StandardAttachmentOut,
      StandardAttachmentCreate,
      StandardAttachmentUpdate
    >("/api/v1/admin/attachments");
  }

  listTemplateAttachments(templateId: number, signal?: AbortSignal) {
    return this.request<AttachmentRefOut[]>(
      "GET",
      `/api/v1/admin/templates/${templateId}/attachments`,
      { signal },
    );
  }

  listAttachmentTemplates(attachmentId: number, signal?: AbortSignal) {
    return this.request<StandardTemplateOut[]>(
      "GET",
      `/api/v1/admin/attachments/${attachmentId}/templates`,
      { signal },
    );
  }

  /** Replace the full set of attachments linked to a standard template. */
  replaceTemplateAttachments(
    templateId: number,
    body: TemplateAttachmentsReplace,
    signal?: AbortSignal,
  ) {
    return this.request<void>("PUT", `/api/v1/admin/templates/${templateId}/attachments`, {
      body,
      signal,
    });
  }

  listCustomerUserGroups(login: string, signal?: AbortSignal) {
    return this.request<GroupOut[]>(
      "GET",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups`,
      { signal },
    );
  }

  assignCustomerUserGroup(
    login: string,
    body: CustomerUserGroupAssignment,
    signal?: AbortSignal,
  ) {
    return this.request<void>(
      "PUT",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups`,
      { body, signal },
    );
  }

  revokeCustomerUserGroup(
    login: string,
    groupId: number,
    permissionKey: string,
    signal?: AbortSignal,
  ) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  get adminAutoResponses() {
    return this.adminCrud<AutoResponseOut, AutoResponseCreate, AutoResponseUpdate>(
      "/api/v1/admin/auto-responses",
    );
  }

  listQueueAutoResponses(queueId: number, signal?: AbortSignal) {
    return this.request<AutoResponseOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/auto-responses`,
      { signal },
    );
  }

  listAutoResponseQueues(autoResponseId: number, signal?: AbortSignal) {
    return this.request<QueueOut[]>(
      "GET",
      `/api/v1/admin/auto-responses/${autoResponseId}/queues`,
      { signal },
    );
  }

  assignQueueAutoResponse(queueId: number, autoResponseId: number, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/queues/${queueId}/auto-responses`, {
      body: { auto_response_id: autoResponseId },
      signal,
    });
  }

  revokeQueueAutoResponse(queueId: number, autoResponseId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/queues/${queueId}/auto-responses/${autoResponseId}`,
      { signal },
    );
  }

  get adminDynamicFields() {
    return this.adminCrud<DynamicFieldOut, DynamicFieldCreate, DynamicFieldUpdate>(
      "/api/v1/admin/dynamic-fields",
    );
  }

  get adminWebhooks() {
    return this.adminCrud<WebhookOut, WebhookCreate, WebhookUpdate>("/api/v1/admin/webhooks");
  }

  get adminQueueVariables() {
    return this.adminCrud<QueueVariableOut, QueueVariableCreate, QueueVariableUpdate>(
      "/api/v1/admin/queue-variables",
    );
  }

  /** Site-specific physical columns on the Znuny ``queue`` table (read-only). */
  listQueuePhysicalVariables(queueId: number, signal?: AbortSignal) {
    return this.request<PhysicalQueueVariableOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/physical-variables`,
      { signal },
    );
  }

  get adminCustomerFields() {
    return this.adminCrud<PlaceholderFieldOut, PlaceholderFieldCreate, PlaceholderFieldUpdate>(
      "/api/v1/admin/customer-fields",
    );
  }

  listAvailableCustomerColumns(source: "customer_user" | "customer_company", signal?: AbortSignal) {
    return this.request<string[]>("GET", "/api/v1/admin/customer-fields/available-columns", {
      query: { source },
      signal,
    });
  }

  getMailOutbound(signal?: AbortSignal) {
    return this.request<MailOutboundOut>("GET", "/api/v1/admin/mail/outbound", { signal });
  }

  putMailOutbound(body: MailOutboundUpdate, signal?: AbortSignal) {
    return this.request<MailOutboundOut>("PUT", "/api/v1/admin/mail/outbound", { body, signal });
  }

  testMailOutbound(body: MailOutboundTestIn = {}, signal?: AbortSignal) {
    return this.request<MailOutboundTestOut>("POST", "/api/v1/admin/mail/outbound/test", {
      body,
      signal,
    });
  }

  listMailLog(params?: MailLogListParams, signal?: AbortSignal) {
    return this.request<AdminPage<MailLogOut>>("GET", "/api/v1/admin/mail/log", {
      query: {
        page: params?.page,
        page_size: params?.pageSize,
        direction: params?.direction ?? undefined,
        status: params?.status ?? undefined,
        q: params?.q ?? undefined,
        from: params?.from ?? undefined,
        to: params?.to ?? undefined,
      },
      signal,
    });
  }

  getMailLog(id: number, signal?: AbortSignal) {
    return this.request<MailLogOut>("GET", `/api/v1/admin/mail/log/${id}`, { signal });
  }

  // Read-only automation config.
  listPostmasterFilters(signal?: AbortSignal) {
    return this.request<PostmasterFilterOut[]>("GET", "/api/v1/admin/postmaster-filters", {
      signal,
    });
  }

  getPostmasterFilter(filterName: string, signal?: AbortSignal) {
    return this.request<PostmasterFilterOut>(
      "GET",
      `/api/v1/admin/postmaster-filters/${encodeURIComponent(filterName)}`,
      { signal },
    );
  }

  listAcls(signal?: AbortSignal) {
    return this.request<AclOut[]>("GET", "/api/v1/admin/acl", { signal });
  }

  getAcl(aclId: number, signal?: AbortSignal) {
    return this.request<AclOut>("GET", `/api/v1/admin/acl/${aclId}`, { signal });
  }

  listGenericAgentJobs(signal?: AbortSignal) {
    return this.request<GenericAgentJobOut[]>("GET", "/api/v1/admin/generic-agent-jobs", {
      signal,
    });
  }

  getGenericAgentJob(jobName: string, signal?: AbortSignal) {
    return this.request<GenericAgentJobOut>(
      "GET",
      `/api/v1/admin/generic-agent-jobs/${encodeURIComponent(jobName)}`,
      { signal },
    );
  }

  // ── Customer portal (/api/portal) ────────────────────────────────────────

  portalLogin(body: LoginRequest, signal?: AbortSignal) {
    return this.request<CustomerLoginResponse>("POST", "/api/portal/auth/login", {
      body,
      signal,
    });
  }

  portalMe(signal?: AbortSignal) {
    return this.request<CustomerMe>("GET", "/api/portal/auth/me", { signal });
  }

  portalLogout(signal?: AbortSignal) {
    return this.request<void>("POST", "/api/portal/auth/logout", { signal });
  }

  portalListTickets(
    params: { state?: number; offset?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<PaginatedTickets>("GET", "/api/portal/tickets", {
      query: params,
      signal,
    });
  }

  portalCreateTicket(body: PortalTicketCreateRequest, signal?: AbortSignal) {
    return this.request<PortalTicketCreateResponse>("POST", "/api/portal/tickets", {
      body,
      signal,
    });
  }

  portalGetTicket(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketDetail>("GET", `/api/portal/tickets/${ticketId}`, { signal });
  }

  portalListArticles(ticketId: number, signal?: AbortSignal) {
    return this.request<ArticleListItem[]>(
      "GET",
      `/api/portal/tickets/${ticketId}/articles`,
      { signal },
    );
  }

  portalReply(ticketId: number, body: PortalReplyRequest, signal?: AbortSignal) {
    return this.request<PortalReplyResponse>("POST", `/api/portal/tickets/${ticketId}/reply`, {
      body,
      signal,
    });
  }

  portalUploadAttachment(
    ticketId: number,
    file: File,
    note = "",
    signal?: AbortSignal,
  ) {
    const form = new FormData();
    form.append("file", file);
    form.append("note", note);
    return this.request<PortalAttachmentUploadResponse>(
      "POST",
      `/api/portal/tickets/${ticketId}/attachments`,
      { body: form, signal },
    );
  }

  portalAttachmentDownloadUrl(ticketId: number, attachmentId: number): string {
    return joinUrl(
      this.baseUrl,
      `/api/portal/tickets/${ticketId}/attachments/${attachmentId}`,
    );
  }

  portalSearchKb(params: { q: string; offset?: number; limit?: number }, signal?: AbortSignal) {
    return this.request<KbSearchResponse>("GET", "/api/portal/kb/search", {
      query: params,
      signal,
    });
  }

  portalGetKbArticle(slugOrId: string, signal?: AbortSignal) {
    return this.request<PortalArticleOut>(
      "GET",
      `/api/portal/kb/articles/${encodeURIComponent(slugOrId)}`,
      { signal },
    );
  }

  // ── Stats (/api/v1/stats) ────────────────────────────────────────────────

  statsVolume(
    params: StatsFilterParams & { granularity?: StatsGranularity } = {},
    signal?: AbortSignal,
  ) {
    return this.request<TicketVolumeOut>("GET", "/api/v1/stats/volume", {
      query: params,
      signal,
    });
  }

  statsVolumeCsvUrl(params: StatsFilterParams & { granularity?: StatsGranularity } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/volume.csv", params);
  }

  statsOpenSnapshot(
    params: StatsFilterParams & { dimension?: StatsDimension } = {},
    signal?: AbortSignal,
  ) {
    return this.request<OpenSnapshotOut>("GET", "/api/v1/stats/open-snapshot", {
      query: params,
      signal,
    });
  }

  statsOpenSnapshotCsvUrl(params: StatsFilterParams & { dimension?: StatsDimension } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/open-snapshot.csv", params);
  }

  statsSla(params: StatsFilterParams = {}, signal?: AbortSignal) {
    return this.request<SlaStatsOut>("GET", "/api/v1/stats/sla", {
      query: params,
      signal,
    });
  }

  statsSlaCsvUrl(params: StatsFilterParams = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/sla.csv", params);
  }

  statsAgentWorkload(params: StatsFilterParams = {}, signal?: AbortSignal) {
    return this.request<AgentWorkloadItemOut[]>("GET", "/api/v1/stats/agent-workload", {
      query: params,
      signal,
    });
  }

  statsAgentWorkloadCsvUrl(params: StatsFilterParams = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/agent-workload.csv", params);
  }

  statsBacklog(
    params: StatsFilterParams & { granularity?: StatsGranularity } = {},
    signal?: AbortSignal,
  ) {
    return this.request<BacklogTrendOut>("GET", "/api/v1/stats/backlog", {
      query: params,
      signal,
    });
  }

  statsBacklogCsvUrl(params: StatsFilterParams & { granularity?: StatsGranularity } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/backlog.csv", params);
  }

  /**
   * Build a CSV export URL for a stats report. Consumed via a plain
   * navigation/anchor (cookie-authenticated download), not a fetch call —
   * mirrors {@link ApiClient.exportTicketsCsvUrl}.
   */
  private buildStatsCsvUrl(path: string, params: Record<string, unknown>): string {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const suffix = qs.toString();
    return joinUrl(this.baseUrl, `${path}${suffix ? `?${suffix}` : ""}`);
  }

  // ── Calendar (/api/v1/calendar) ──────────────────────────────────────────

  listCalendars(signal?: AbortSignal) {
    return this.request<CalendarOut[]>("GET", "/api/v1/calendar/calendars", { signal });
  }

  listAppointments(
    params: { start: string; end: string; calendar_id?: number[] },
    signal?: AbortSignal,
  ) {
    const qs = new URLSearchParams();
    qs.set("start", params.start);
    qs.set("end", params.end);
    for (const id of params.calendar_id ?? []) qs.append("calendar_id", String(id));
    return this.request<OccurrenceOut[]>(
      "GET",
      `/api/v1/calendar/appointments?${qs.toString()}`,
      { signal },
    );
  }

  createAppointment(body: AppointmentIn, signal?: AbortSignal) {
    return this.request<AppointmentOut>("POST", "/api/v1/calendar/appointments", {
      body,
      signal,
    });
  }

  getAppointment(appointmentId: number, signal?: AbortSignal) {
    return this.request<AppointmentOut>(
      "GET",
      `/api/v1/calendar/appointments/${appointmentId}`,
      { signal },
    );
  }

  updateAppointment(appointmentId: number, body: AppointmentUpdateIn, signal?: AbortSignal) {
    return this.request<AppointmentOut>(
      "PATCH",
      `/api/v1/calendar/appointments/${appointmentId}`,
      { body, signal },
    );
  }

  deleteAppointment(
    appointmentId: number,
    params: { occurrence?: string } = {},
    signal?: AbortSignal,
  ) {
    return this.request<void>("DELETE", `/api/v1/calendar/appointments/${appointmentId}`, {
      query: params,
      signal,
    });
  }

  linkAppointmentTicket(appointmentId: number, ticketId: number, signal?: AbortSignal) {
    return this.request<TicketLinkOut>(
      "POST",
      `/api/v1/calendar/appointments/${appointmentId}/tickets/${ticketId}`,
      { signal },
    );
  }

  unlinkAppointmentTicket(appointmentId: number, ticketId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/calendar/appointments/${appointmentId}/tickets/${ticketId}`,
      { signal },
    );
  }

  listAppointmentTicketLinks(appointmentId: number, signal?: AbortSignal) {
    return this.request<TicketLinkOut[]>(
      "GET",
      `/api/v1/calendar/appointments/${appointmentId}/tickets`,
      { signal },
    );
  }

  calendarExportIcsUrl(calendarId: number): string {
    return joinUrl(this.baseUrl, `/api/v1/calendar/calendars/${calendarId}/export.ics`);
  }

  // ── ProcessManagement (BPM) (/api/v1/process) ────────────────────────────

  listProcesses(signal?: AbortSignal) {
    return this.request<ProcessSummaryOut[]>("GET", "/api/v1/process/", { signal });
  }

  getProcess(processEntityId: string, signal?: AbortSignal) {
    return this.request<ProcessDetailOut>(
      "GET",
      `/api/v1/process/${encodeURIComponent(processEntityId)}`,
      { signal },
    );
  }

  getActivityDialog(activityDialogEntityId: string, signal?: AbortSignal) {
    return this.request<ActivityDialogDetailOut>(
      "GET",
      `/api/v1/process/activity-dialog/${encodeURIComponent(activityDialogEntityId)}`,
      { signal },
    );
  }

  getTicketProcessState(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "GET",
      `/api/v1/process/ticket/${ticketId}/state`,
      { signal },
    );
  }

  startTicketProcess(ticketId: number, body: ProcessStartIn, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "POST",
      `/api/v1/process/ticket/${ticketId}/start`,
      { body, signal },
    );
  }

  submitActivityDialog(ticketId: number, body: ActivityDialogSubmitIn, signal?: AbortSignal) {
    return this.request<ActivityDialogSubmitOut>(
      "POST",
      `/api/v1/process/ticket/${ticketId}/submit`,
      { body, signal },
    );
  }
}

export type { paths };
