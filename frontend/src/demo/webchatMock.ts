/**
 * Demo / screenshot data for the agent web chat. Path-scoped on purpose: it
 * answers only the chat paths and returns `undefined` for everything else, so
 * `resolveData` falls through to the other features' branches untouched.
 */

const WAITING_SESSION = "9f3c2a40-5b1e-4d7a-8c2e-0a1b2c3d4e51";
const MINE_SESSION = "5d8e7b12-3a4c-4f60-9b1d-6e7f8a9b0c22";
const BOT_SESSION = "c4a1f0d2-7e63-4b85-9a2c-1d3e5f708b93";
const HANDED_SESSION = "e2b7a9c1-4d58-4a36-8f0e-9c1b2a3d4e64";

const TICKET_BY_SESSION: Record<string, number> = {
  [WAITING_SESSION]: 117, [MINE_SESSION]: 116, [BOT_SESSION]: 118, [HANDED_SESSION]: 119,
};

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

const botItem = {
  session_id: BOT_SESSION, ticket_id: 118, tn: "20260701018", title: "Chat: Rechnung nicht erhalten",
  status: "bot" as const, visitor_name: "Jana Beispiel", topic_label: "Support",
  waiting_since: null, accepted_at: null,
};
const handedItem = {
  session_id: HANDED_SESSION, ticket_id: 119, tn: "20260701019", title: "Chat: Vertrag kündigen",
  status: "waiting" as const, visitor_name: "Paul Muster", topic_label: "Support",
  waiting_since: new Date(Date.now() - 30_000).toISOString(), accepted_at: null,
};
const ticketChat118 = {
  session_id: BOT_SESSION, status: "bot", visitor_name: "Jana Beispiel",
  visitor_email: "jana@customer.example.org", visitor_email_verified: true, identity_source: "website",
  bot_name: "Mia", handoff_at: null, handoff_reason: null,
  topic_label: "Support", language: "de", page_url: "https://www.example.org/rechnungen",
  assigned_user_id: null, assigned_name: null, waiting_since: null,
  accepted_at: null, ended_at: null, end_reason: null,
  visitor_online: true, visitor_typing: false,
};
const ticketChat119 = {
  ...ticketChat118, session_id: HANDED_SESSION, status: "waiting", visitor_name: "Paul Muster",
  visitor_email: "paul@example.org", visitor_email_verified: false, identity_source: null,
  handoff_at: "2026-07-21T11:10:00Z", handoff_reason: "ai",
  waiting_since: new Date(Date.now() - 30_000).toISOString(),
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
  allowed_origins: ["https://www.example.org"],
  topics: [
    { key: "support", label: "Support", queue_id: 1, priority_id: null, ai_mode: "first_line" },
    { key: "billing", label: "Rechnung & Vertrag", queue_id: 2, priority_id: 3, ai_mode: "off" },
  ],
  greeting: "Hallo! Wie können wir Ihnen helfen?",
  privacy_url: "https://www.example.org/datenschutz",
  offline_mode: "form",
  business_calendar: null,
  default_customer_user: null,
  retention_days: 30,
  show_agent_names: true,
  bot_name: "KI-Assistent",
  bot_tone_prompt: null,
  show_on_portal: false,
  identity_create_customers: false,
  wait_timeout_seconds: 180,
  prechat_fields: [
    { key: "name", kind: "text", label: "Name", required: false, maps_to: "name", options: [], dynamic_field: null },
    { key: "email", kind: "email", label: "E-Mail", required: false, maps_to: "email", options: [], dynamic_field: null },
  ],
  theme: { primary_color: "#2563eb", position: "right", mode: "auto", launcher_label: "Chat" },
  upload_policy: {
    enabled: true, max_bytes: 10 * 1024 * 1024,
    types: ["image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"],
  },
  rate_limits: {
    sessions_per_ip_10min: 5, sessions_per_config_minute: 30, messages_per_session_minute: 30,
    messages_per_ip_minute: 60, uploads_per_session_10min: 10, offline_per_ip_hour: 3,
    offline_per_config_hour: 60,
  },
};
const config = {
  id: 1, name: "Website", valid: true, public_key: "demo0000000000000000000000000000",
  embed_snippet: '<script src="https://tickets.example.org/chat/v1/loader.js" data-key="demo0000000000000000000000000000" async></script>',
  identity_secret_set: false,
  session_count: 12, created: "2026-07-01T08:00:00Z", changed: "2026-07-20T09:00:00Z",
  values: configValues,
};

const aiStatus = [
  { topic_key: "support", label: "Support", queue_id: 1, queue_name: "Support", ai_mode: "first_line", ok: true, reason: null },
  { topic_key: "billing", label: "Rechnung & Vertrag", queue_id: 2, queue_name: "Buchhaltung", ai_mode: "off", ok: false, reason: "ai_mode_off" },
];
let identitySecretSet = false;

/** `undefined` = not a web chat path (the caller continues with its own branches). */
export function resolveWebchatData(p: string, method: string): unknown | undefined {
  const ticketChat = p.match(/\/api\/v1\/tickets\/(\d+)\/chat(\/typing)?$/);
  if (ticketChat) {
    if (ticketChat[2]) return {};
    if (method !== "GET") return {};
    const id = Number(ticketChat[1]);
    return id === 116 ? ticketChat116 : id === 118 ? ticketChat118 : id === 119 ? ticketChat119 : null;
  }
  if (p.includes("/api/v1/chat/")) {
    if (p.endsWith("/agent-state")) return { available: true, max_concurrent: 3, active_count: 1, topics: [] };
    if (p.endsWith("/topics")) return topics;
    if (p.endsWith("/inbox")) return { waiting: [handedItem, waitingItem], bot: [botItem], mine: [mineItem] };
    if (p.endsWith("/accept")) {
      const sid = p.match(/\/sessions\/([^/]+)\/accept$/)?.[1];
      return { ticket_id: (sid && TICKET_BY_SESSION[sid]) || 117 };
    }
    return {};
  }
  if (p.endsWith("/api/v1/admin/channels/webchat")) return { channel: "webchat", enabled: true, config: {} };
  if (p.includes("/api/v1/admin/chat-configs")) {
    if (p.endsWith("/chat-configs")) return method === "GET" ? [config] : config;
    if (p.endsWith("/ai-status")) return aiStatus;
    if (p.endsWith("/identity-secret")) {
      identitySecretSet = method !== "DELETE";
      return method === "DELETE" ? {} : { secret: "demo-" + Math.random().toString(36).slice(2, 30).padEnd(28, "0") };
    }
    if (p.endsWith("/rotate-key")) {
      const key = "demo" + Math.random().toString(36).slice(2, 30).padEnd(28, "0");
      return { ...config, public_key: key, embed_snippet: config.embed_snippet.replace(config.public_key, key) };
    }
    return method === "DELETE" ? {} : { ...config, identity_secret_set: identitySecretSet };
  }
  return undefined;
}
