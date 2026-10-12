/**
 * Visitor API client (`/api/chat/v1`, same origin as the frame). The visitor token goes
 * in the `X-Chat-Token` header, never in a URL. Types only from the api-client
 * (R-FRAME-CONNECT: no runtime import).
 */
import type { Schemas } from "@tiqora/api-client";

export type ChatPublicConfig = Schemas["ChatPublicConfigOut"];
export type ChatMessage = Schemas["ChatMessageOut"];
export type ChatSession = Schemas["ChatSessionOut"];
export type ChatSessionCreated = Schemas["ChatSessionCreatedOut"];
export type ChatPortalIdentity = Schemas["ChatPortalIdentityOut"];

/**
 * `code` is the server's `detail` string (`rate_limited`, `not_found`, `disabled`,
 * `body_too_large`, `upload_rejected`, `offline`, `chat_unavailable`, …), or a status
 * based fallback (`network` for status 0). `errors` carries field errors of 422s, e.g.
 * `upload_rejected` with `{file: "type" | "empty" | "disabled" | "size"}`.
 * `disabled` (404) means the channel was switched off: writes fail, reads and `end` work.
 */
export class ChatApiError extends Error {
  status: number;
  code: string;
  errors: Record<string, string>;
  retryAfter: number | null;

  constructor(status: number, code: string, errors: Record<string, string> = {}, retryAfter: number | null = null) {
    super(`${status} ${code}`);
    this.name = "ChatApiError";
    this.status = status;
    this.code = code;
    this.errors = errors;
    this.retryAfter = retryAfter;
  }
}

const STATUS_CODES: Record<number, string> = {
  403: "forbidden",
  404: "not_found",
  413: "body_too_large",
  429: "rate_limited",
  503: "chat_unavailable",
};

function stringMap(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (v && typeof v === "object" && !Array.isArray(v)) {
    for (const [k, val] of Object.entries(v)) if (typeof val === "string") out[k] = val;
  }
  return out;
}

async function toError(res: Response): Promise<ChatApiError> {
  let code = STATUS_CODES[res.status] ?? `http_${res.status}`;
  let errors: Record<string, string> = {};
  try {
    const j = (await res.json()) as { detail?: unknown; errors?: unknown } | null;
    const d = j?.detail;
    if (typeof d === "string" && d) code = d;
    else if (Array.isArray(d)) code = "invalid"; // framework validation error
    else if (d && typeof d === "object") {
      const o = d as { code?: unknown; errors?: unknown };
      if (typeof o.code === "string") code = o.code;
      errors = stringMap(o.errors);
    }
    if (!Object.keys(errors).length) errors = stringMap(j?.errors);
  } catch {
    // non-JSON body (proxy error page): keep the status based code
  }
  const ra = Number(res.headers.get("Retry-After"));
  return new ChatApiError(res.status, code, errors, Number.isFinite(ra) && ra > 0 ? ra : null);
}

export function createChatApi(opts: { base?: string; fetchImpl?: typeof fetch } = {}) {
  const base = (opts.base ?? "/api/chat/v1").replace(/\/+$/, "");
  const doFetch = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));

  // `creds` is "omit" for every call except the portal identity (own origin, reads the portal cookie).
  async function call(path: string, init: RequestInit = {}, token?: string, creds: RequestCredentials = "omit"): Promise<Response> {
    const headers: Record<string, string> = {};
    if (token) headers["X-Chat-Token"] = token;
    if (typeof init.body === "string") headers["Content-Type"] = "application/json";
    let res: Response;
    try {
      res = await doFetch(base + path, { ...init, headers, cache: "no-store", credentials: creds });
    } catch {
      throw new ChatApiError(0, "network");
    }
    if (!res.ok) throw await toError(res);
    return res;
  }
  const json = async <T>(path: string, init?: RequestInit, token?: string) =>
    (await (await call(path, init, token)).json()) as T;
  const none = async (path: string, init?: RequestInit, token?: string) => {
    await call(path, init, token);
  };
  const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
  const sid = (id: string) => `/sessions/${encodeURIComponent(id)}`;

  return {
    streamUrl: (id: string) => `${base}${sid(id)}/stream`,
    config: (key: string, origin: string) =>
      json<ChatPublicConfig>(`/config?k=${encodeURIComponent(key)}&origin=${encodeURIComponent(origin)}`),
    createSession: (body: Schemas["ChatSessionCreateIn"]) => json<ChatSessionCreated>("/sessions", post(body)),
    session: (id: string, token: string) => json<ChatSession>(sid(id), undefined, token),
    messages: (id: string, token: string, after: number) =>
      json<ChatMessage[]>(`${sid(id)}/messages?after=${Number.isFinite(after) ? Math.max(0, Math.trunc(after)) : 0}&limit=200`, undefined, token),
    send: (id: string, token: string, body: string, clientId: string) =>
      json<ChatMessage>(`${sid(id)}/messages`, post({ body, client_id: clientId }), token),
    typing: (id: string, token: string) => none(`${sid(id)}/typing`, { method: "POST" }, token),
    upload: (id: string, token: string, file: File, clientId: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("client_id", clientId);
      return json<ChatMessage>(`${sid(id)}/attachments`, { method: "POST", body: form }, token);
    },
    download: async (id: string, token: string, seq: number, index: number) =>
      (await call(`${sid(id)}/messages/${Math.trunc(seq)}/attachments/${Math.trunc(index)}`, undefined, token)).blob(),
    contact: (id: string, token: string, email: string, name: string | null) =>
      none(`${sid(id)}/contact`, post({ email, name }), token),
    end: (id: string, token: string) => none(`${sid(id)}/end`, { method: "POST" }, token),
    offline: (body: Schemas["ChatOfflineIn"]) => none("/offline", post(body)),
    identify: (id: string, token: string, identity: string) => none(`${sid(id)}/identify`, post({ identity }), token),
    handoff: (id: string, token: string) => none(`${sid(id)}/handoff`, { method: "POST" }, token),
    portalIdentity: async (body: Schemas["ChatPortalIdentityIn"]) =>
      (await (await call("/identity/portal", post(body), undefined, "same-origin")).json()) as ChatPortalIdentity,
  };
}
