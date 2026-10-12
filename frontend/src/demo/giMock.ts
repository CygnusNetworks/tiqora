/**
 * Demo / screenshot data for the GenericInterface webservices admin pages.
 * Path-scoped like `webchatMock.ts`: answers only its own paths and returns
 * `undefined` for everything else, so `resolveData` falls through.
 */
import type { WebserviceDetail, WebserviceList } from "@/lib/webservicesApi";

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

export function resolveGiData(path: string, method: string): unknown | undefined {
  if (path.endsWith("/api/v1/admin/webservices") && method === "GET") return demoWebserviceList;
  const detail = path.match(/\/api\/v1\/admin\/webservices\/(\d+)$/);
  if (detail && method === "GET") return demoWebserviceDetails[Number(detail[1])] ?? undefined;
  if (path.match(/\/api\/v1\/admin\/webservices\/\d+\/egress$/) && method === "PUT")
    return demoWebserviceDetails[1].egress;
  if (path.match(/\/invokers\/[^/]+\/dry-run$/) && method === "POST")
    return { job_id: 9390, status: "queued" };
  if (path.endsWith("/api/v1/jobs/9390")) return demoDryRunJob;
  return undefined;
}
