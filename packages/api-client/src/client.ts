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
export type TicketDetail = Schemas["TicketDetail"];
export type ArticleListItem = Schemas["ArticleListItem"];
export type ArticleBody = Schemas["ArticleBody"];
export type AttachmentMetaOut = Schemas["AttachmentMetaOut"];
export type HistoryEntry = Schemas["HistoryEntry"];
export type CustomerUserOut = Schemas["CustomerUserOut"];
export type SearchHit = Schemas["SearchHit"];
export type SearchResponse = Schemas["SearchResponse"];
export type DynamicFieldValueOut = Schemas["DynamicFieldValueOut"];

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

// ── Admin ─────────────────────────────────────────────────────────────────
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
export type AutoResponseOut = Schemas["AutoResponseOut"];
export type AutoResponseCreate = Schemas["AutoResponseCreate"];
export type AutoResponseUpdate = Schemas["AutoResponseUpdate"];
export type DynamicFieldOut = Schemas["DynamicFieldOut"];
export type DynamicFieldCreate = Schemas["DynamicFieldCreate"];
export type DynamicFieldUpdate = Schemas["DynamicFieldUpdate"];
export type WebhookOut = Schemas["WebhookOut"];
export type WebhookCreate = Schemas["WebhookCreate"];
export type WebhookUpdate = Schemas["WebhookUpdate"];
export type PostmasterFilterOut = Schemas["PostmasterFilterOut"];
export type AclOut = Schemas["AclOut"];
export type GenericAgentJobOut = Schemas["GenericAgentJobOut"];

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

  listHistory(ticketId: number, signal?: AbortSignal) {
    return this.request<HistoryEntry[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/history`,
      { signal },
    );
  }

  // ── Customers ─────────────────────────────────────────────────────────

  getCustomer(login: string, signal?: AbortSignal) {
    return this.request<CustomerUserOut>(
      "GET",
      `/api/v1/customers/${encodeURIComponent(login)}`,
      { signal },
    );
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

  listKbArticles(
    params: { category_id?: number; state?: string } = {},
    signal?: AbortSignal,
  ) {
    return this.request<ArticleSummary[]>("GET", "/api/v1/kb/articles", {
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

  searchKb(params: { q: string; limit?: number }, signal?: AbortSignal) {
    return this.request<KbSearchResponse>("GET", "/api/v1/kb/search", {
      query: params,
      signal,
    });
  }

  // ── Admin ────────────────────────────────────────────────────────────────

  private adminCrud<Out, Create, Update>(base: string) {
    return {
      list: (signal?: AbortSignal) => this.request<Out[]>("GET", base, { signal }),
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

  get adminRoles() {
    return this.adminCrud<RoleOut, RoleCreate, RoleUpdate>("/api/v1/admin/roles");
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

  get adminAutoResponses() {
    return this.adminCrud<AutoResponseOut, AutoResponseCreate, AutoResponseUpdate>(
      "/api/v1/admin/auto-responses",
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
}

export type { paths };
