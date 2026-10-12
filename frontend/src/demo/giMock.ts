/**
 * Demo / screenshot data for the GenericInterface webservices admin pages.
 * Path-scoped like `webchatMock.ts`: answers only its own paths and returns
 * `undefined` for everything else, so `resolveData` falls through.
 */
import type { DebugEntry, DebugEntryDetail, WebserviceDetail, WebserviceList } from "@/lib/webservicesApi";

const T0 = "2026-10-11T08:00:00Z";

export const demoWebserviceList: WebserviceList = {
  webservices: [
    {
      id: 1, name: "ExampleCRM", valid: true, has_provider: false, invoker_count: 2,
      event_invoker_count: 2, transport: "HTTP::REST", problem: null,
    },
    {
      id: 2, name: "LegacySOAP", valid: false, has_provider: true, invoker_count: 0,
      event_invoker_count: 0, transport: null,
      problem: "Die Konfiguration ist kein gültiges YAML (Zeile 14).",
    },
  ],
};

export const demoWebserviceDetails: Record<number, WebserviceDetail> = {
  1: {
    id: 1, name: "ExampleCRM", valid: true, problem: null,
    requester_transport: {
      type: "HTTP::REST", endpoint: "https://crm.example.org/api/v2", auth_type: "BasicAuth",
      has_proxy: false, uses_client_certificate: false, insecure_tls_requested: false,
    },
    invokers: [
      {
        name: "SyncTicket", type: "Ticket::Generic", supported: true,
        events: [
          { event: "TicketCreate", asynchronous: false, has_condition: false, fired_by_tiqora: true },
          { event: "TicketStateUpdate", asynchronous: true, has_condition: true, fired_by_tiqora: true },
        ],
        mapping_outbound: "Simple", mapping_inbound: "Simple", problem: null,
      },
      {
        name: "NotifyCrm", type: "Ticket::Generic", supported: true,
        events: [
          { event: "ArticleCreate", asynchronous: true, has_condition: false, fired_by_tiqora: false },
        ],
        mapping_outbound: "XSLT", mapping_inbound: null, problem: null,
      },
    ],
    error_handling: [
      {
        key: "RetryOnError", type: "RequestRetry", invoker_filter: ["SyncTicket"],
        error_stage_filter: ["RequesterRequestPrepare"], error_message_content_filter: null,
        stop_after_match: "0", retry_summary: "5 Versuche, alle 10 Minuten",
      },
    ],
    debug_threshold: "error",
    egress: { allowed_targets: ["crm.example.org"], allow_insecure_tls: false },
    warnings: [],
  },
  2: {
    id: 2, name: "LegacySOAP", valid: false,
    problem: "Die Konfiguration ist kein gültiges YAML (Zeile 14).",
    requester_transport: null, invokers: [], error_handling: [], debug_threshold: "error",
    egress: { allowed_targets: [], allow_insecure_tls: false }, warnings: [],
  },
};

export const demoDryRunJob = {
  id: 9390,
  kind: "core.gi_invoke",
  status: "succeeded",
  title_key: "jobs.kinds.core.gi_invoke",
  progress_done: 1,
  progress_total: 1,
  progress_message: null,
  cancel_requested: false,
  error: null,
  result: {
    communication_id: "demo-comm-1",
    request: {
      method: "POST",
      url: "https://crm.example.org/api/v2/tickets",
      headers: { Authorization: "***", "Content-Type": "application/json" },
      body: '{"TicketID":105,"Title":"Router blinkt rot","Queue":"Support"}',
    },
  },
  files: [],
  created_by: 1,
  created_at: T0,
  started_at: T0,
  finished_at: T0,
};

function demoEntry(
  id: number, minutesAgo: number, over: Partial<DebugEntry> = {},
): DebugEntry {
  const at = new Date(Date.parse(T0) - minutesAgo * 60_000).toISOString();
  return {
    source: "tiqora", id, communication_id: `demo-comm-${id}`, communication_type: "Requester",
    name: "SyncTicket", remote_ip: null, ticket_id: 105, created: at, max_level: "info",
    can_resend: false, ...over,
  };
}

/** Newest first; Tiqora and Znuny rows mixed, one error that can be sent again. */
export const demoDebugEntries: DebugEntry[] = [
  demoEntry(106, 2, { max_level: "error", can_resend: true }),
  demoEntry(105, 9, { source: "znuny", name: "NotifyCrm", ticket_id: 98 }),
  demoEntry(104, 20, { communication_type: "Provider", name: "TicketGet", remote_ip: "198.51.100.7", ticket_id: null }),
  demoEntry(103, 35, { max_level: "notice", can_resend: true }),
  demoEntry(102, 61, { source: "znuny", name: "SyncTicket", max_level: "error", ticket_id: 97 }),
  demoEntry(101, 95, { communication_type: "Provider", name: "TicketCreate", remote_ip: "203.0.113.20", ticket_id: 104 }),
];

/** Server-side filtering of the demo list (used by the e2e mock, which sees the query string). */
export function filterDebugEntries(params: URLSearchParams): { entries: DebugEntry[]; next_before: null } {
  const type = params.get("type");
  const source = params.get("source");
  const ip = params.get("remote_ip");
  return {
    entries: demoDebugEntries.filter(
      (e) =>
        (!type || e.communication_type === type) &&
        (!source || e.source === source) &&
        (!ip || e.remote_ip === ip),
    ),
    next_before: null,
  };
}

function demoDebugDetail(source: string, id: number): DebugEntryDetail | undefined {
  const entry = demoDebugEntries.find((e) => e.source === source && e.id === id);
  if (!entry) return undefined;
  return {
    entry,
    contents: [
      { id: id * 10 + 1, debug_level: "debug", subject: "Communication sequence started", masked: false,
        created: entry.created, content: '{"Event":"TicketCreate"}' },
      { id: id * 10 + 2, debug_level: "info", subject: "Outgoing data before mapping", masked: false,
        created: entry.created, content: '{"TicketID":105,"Title":"Router blinkt rot","Queue":"Support"}' },
      { id: id * 10 + 3, debug_level: "debug", subject: "Request headers", masked: true,
        created: entry.created, content: "Authorization: ***\nContent-Type: application/json" },
      ...(entry.max_level === "error"
        ? [{ id: id * 10 + 4, debug_level: "error", subject: "Request failed", masked: false,
            created: entry.created,
            content: "Error while performing REST 'POST' request to Controller '/tickets' on Host 'https://crm.example.org/api/v2'. Response code '503'." }]
        : []),
    ],
  };
}

export function resolveGiData(path: string, method: string): unknown | undefined {
  if (path.endsWith("/api/v1/admin/webservices") && method === "GET") return demoWebserviceList;
  if (method === "GET" && /\/api\/v1\/admin\/webservices\/\d+\/debugger(\?|$)/.test(path)) {
    // path carries no query string; the demo list is a single fixed page
    return { entries: demoDebugEntries, next_before: null };
  }
  const entry = path.match(/\/api\/v1\/admin\/webservices\/\d+\/debugger\/(tiqora|znuny)\/(\d+)$/);
  if (entry && method === "GET") return demoDebugDetail(entry[1], Number(entry[2]));
  if (/\/debugger\/tiqora\/\d+\/resend$/.test(path) && method === "POST")
    return { job_id: 9390, status: "queued" };
  const detail = path.match(/\/api\/v1\/admin\/webservices\/(\d+)$/);
  if (detail && method === "GET") return demoWebserviceDetails[Number(detail[1])] ?? undefined;
  if (path.match(/\/api\/v1\/admin\/webservices\/\d+\/egress$/) && method === "PUT")
    return demoWebserviceDetails[1].egress;
  if (path.match(/\/invokers\/[^/]+\/dry-run$/) && method === "POST")
    return { job_id: 9390, status: "queued" };
  if (path.endsWith("/api/v1/jobs/9390")) return demoDryRunJob;
  return undefined;
}
