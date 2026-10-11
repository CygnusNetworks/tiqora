/**
 * Demo / screenshot data for the agent web chat. Path-scoped on purpose: it
 * answers only the chat paths and returns `undefined` for everything else, so
 * `resolveData` falls through to the other features' branches untouched.
 */

const WAITING_SESSION = "9f3c2a40-5b1e-4d7a-8c2e-0a1b2c3d4e51";
const MINE_SESSION = "5d8e7b12-3a4c-4f60-9b1d-6e7f8a9b0c22";

const topics = [{ ref: "1:support", label: "Support", queue_id: 1, config_name: "Website" }];

const waitingItem = {
  session_id: WAITING_SESSION, ticket_id: 117, tn: "20260701017", title: "Chat: Erika Musterfrau",
  status: "waiting" as const, visitor_name: "Erika Musterfrau", topic_label: "Support",
  waiting_since: new Date(Date.now() - 45_000).toISOString(), accepted_at: null,
};
const mineItem = {
  session_id: MINE_SESSION, ticket_id: 116, tn: "20260701016", title: "Chat: Router blinkt rot",
  status: "active" as const, visitor_name: "Max Beispiel", topic_label: "Support",
  waiting_since: null, accepted_at: "2026-07-21T11:03:00Z",
};

const ticketChat116 = {
  session_id: MINE_SESSION, status: "active", visitor_name: "Max Beispiel",
  visitor_email: "m.beispiel@northwind.example", visitor_email_verified: false,
  topic_label: "Support", language: "de", page_url: "https://www.example.org/hilfe",
  assigned_user_id: 1, assigned_name: "Alex Turner", waiting_since: null,
  accepted_at: "2026-07-21T11:03:00Z", ended_at: null, end_reason: null,
  visitor_online: true, visitor_typing: false,
};

const configValues = {
  allowed_origins: ["https://www.example.org"], offline_mode: "form", retention_days: 30,
  show_agent_names: true, wait_timeout_seconds: 180, topics: [], prechat_fields: [],
};
const config = {
  id: 1, name: "Website", valid: true, public_key: "demo0000000000000000000000000000",
  embed_snippet: '<script src="https://tickets.example.org/chat/v1/loader.js" data-key="demo0000000000000000000000000000" async></script>',
  session_count: 12, created: "2026-07-01T08:00:00Z", changed: "2026-07-20T09:00:00Z",
  values: configValues,
};

/** `undefined` = not a web chat path (the caller continues with its own branches). */
export function resolveWebchatData(p: string, method: string): unknown | undefined {
  const ticketChat = p.match(/\/api\/v1\/tickets\/(\d+)\/chat(\/typing)?$/);
  if (ticketChat) {
    if (ticketChat[2]) return {};
    return method === "GET" ? (Number(ticketChat[1]) === 116 ? ticketChat116 : null) : {};
  }
  if (p.includes("/api/v1/chat/")) {
    if (p.endsWith("/agent-state")) return { available: true, max_concurrent: 3, active_count: 1, topics: [] };
    if (p.endsWith("/topics")) return topics;
    if (p.endsWith("/inbox")) return { waiting: [waitingItem], mine: [mineItem] };
    if (p.endsWith("/accept")) return { ticket_id: 117 };
    return {};
  }
  if (p.includes("/api/v1/admin/chat-configs")) {
    if (p.endsWith("/chat-configs")) return method === "GET" ? [config] : config;
    return method === "DELETE" ? {} : config;
  }
  return undefined;
}
