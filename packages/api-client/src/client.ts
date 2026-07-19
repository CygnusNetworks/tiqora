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
    if (init?.body !== undefined) {
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
}

export type { paths };
