/**
 * GenericInterface webservices admin API
 * (`backend/src/tiqora/api/v1/admin/webservices.py`). Hand-written wrappers
 * like `./phoneApi.ts`; the shapes come from the generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type WebserviceList = Schemas["WebserviceListOut"];
export type WebserviceListItem = Schemas["WebserviceListItemOut"];
export type WebserviceDetail = Schemas["WebserviceDetailOut"];
export type WebserviceInvoker = Schemas["InvokerOut"];
export type WebserviceEgressIn = Schemas["WebserviceEgressIn"];
export type WebserviceEgress = Schemas["WebserviceEgressOut"];
export type DryRunIn = Schemas["DryRunIn"];
export type JobRef = Schemas["JobRefOut"];
export type DebugEntry = Schemas["DebugEntryOut"];
export type DebugEntryList = Schemas["DebugEntryListOut"];
export type DebugEntryDetail = Schemas["DebugEntryDetailOut"];
export type DebugContent = Schemas["DebugContentOut"];

/** Filters of the debugger list; `before` is the opaque `next_before` of the previous page. */
export type DebuggerQuery = {
  type?: "Provider" | "Requester";
  source?: "tiqora" | "znuny";
  from?: string;
  to?: string;
  remote_ip?: string;
  before?: string;
  limit?: number;
};

/** Notice keys the Daemons API returns on the `gi_requester` row. */
export const GI_NOTICE_OFF = "admin.daemons.notices.giRequesterOff";
export const GI_NOTICE_DOUBLE = "admin.daemons.notices.giRequesterDouble";

/** What a finished dry-run job holds (`GiInvoke` result): the masked request. */
export type DryRunResult = {
  communication_id?: string;
  request?: {
    method?: string;
    url?: string;
    headers?: Record<string, string>;
    body?: string;
    body_bytes?: number;
  };
};

export const webservicesApi = {
  list(signal?: AbortSignal) {
    return api.request<WebserviceList>("GET", "/api/v1/admin/webservices", { signal });
  },
  get(wsId: number, signal?: AbortSignal) {
    return api.request<WebserviceDetail>("GET", `/api/v1/admin/webservices/${wsId}`, { signal });
  },
  putEgress(wsId: number, body: WebserviceEgressIn) {
    return api.request<WebserviceEgress>("PUT", `/api/v1/admin/webservices/${wsId}/egress`, {
      body,
    });
  },
  debuggerList(wsId: number, query: DebuggerQuery, signal?: AbortSignal) {
    return api.request<DebugEntryList>("GET", `/api/v1/admin/webservices/${wsId}/debugger`, {
      query,
      signal,
    });
  },
  debuggerEntry(wsId: number, source: DebugEntry["source"], entryId: number, signal?: AbortSignal) {
    return api.request<DebugEntryDetail>(
      "GET",
      `/api/v1/admin/webservices/${wsId}/debugger/${source}/${entryId}`,
      { signal },
    );
  },
  resend(wsId: number, entryId: number) {
    return api.request<JobRef>(
      "POST",
      `/api/v1/admin/webservices/${wsId}/debugger/tiqora/${entryId}/resend`,
    );
  },
  dryRun(wsId: number, invoker: string, body: DryRunIn) {
    return api.request<JobRef>(
      "POST",
      `/api/v1/admin/webservices/${wsId}/invokers/${encodeURIComponent(invoker)}/dry-run`,
      { body },
    );
  },
};

export type WebserviceTab = "overview" | "invokers" | "debugger" | "settings";
export const WEBSERVICE_TABS: readonly WebserviceTab[] = ["overview", "invokers", "debugger", "settings"];
