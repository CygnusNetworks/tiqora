/**
 * ITSM endpoints (`backend/src/tiqora/api/v1/itsm.py`, `api/v1/admin/itsm.py`, ticket-scoped
 * routes in `api/v1/tickets.py`). Hand-written wrappers like `./dashboardsApi.ts`; the shapes
 * come from the generated schema. `/itsm/*` answers 409 `itsm_off` while the ITSM is switched off.
 */
import type { Schemas } from "@tiqora/api-client";
import { api, ApiError } from "./api";

export type CiSource = "znuny" | "tiqora";

export type ItsmStatus = Schemas["ItsmStatusOut"];
export type ItsmCatalog = Schemas["CatalogOut"];
export type CiSummary = Schemas["CiSummaryOut"];
export type CiPage = Schemas["CiPageOut"];
export type CiDetail = Schemas["CiDetailOut"];
export type CiAttribute = Schemas["CiAttributeOut"];
export type CiVersion = Schemas["CiVersionOut"];
export type CiVersionPage = Schemas["CiVersionPageOut"];
export type CatalogValue = Schemas["CatalogValueOut"];
export type CiTicketLink = Schemas["CiTicketLinkOut"];
export type TicketLinkTarget = Schemas["TicketLinkTargetOut"];
export type LinkTypeOption = Schemas["LinkTypeOptionOut"];
export type PrioritySuggestion = Schemas["PrioritySuggestionOut"];
export type PriorityMatrix = Schemas["MatrixOut"];
export type ServiceItsm = Schemas["ServiceItsmOut"];
export type ServiceIncidentState = Schemas["ServiceIncidentStateOut"];
export type RecentIncident = Schemas["RecentIncidentOut"];
export type TicketItsm = Schemas["TicketItsmOut"];
export type LinkGraph = Schemas["LinkGraphOut"];
export type Problem = Schemas["ProblemOut"];
export type ProblemIn = Schemas["ProblemIn"];
export type MajorIncident = Schemas["MajorIncidentOut"];
export type MajorIncidentUpdateIn = Schemas["MajorIncidentUpdateIn"];
export type MajorIncidentUpdate = Schemas["MajorIncidentUpdateOut"];
export type LinkedArticlesIn = Schemas["LinkedArticlesIn"];
export type JobAccepted = Schemas["JobAccepted"];
export type ItsmAdmin = Schemas["ItsmAdminOut"];
export type ItsmMode = Schemas["ItsmModeIn"]["mode"];
export type SetupPlan = Schemas["SetupOut"];
export type SetupApplyIn = Schemas["SetupApplyIn"];
export type LinkTypeList = Schemas["LinkTypeListOut"];
export type LinkType = Schemas["LinkTypeOut"];
export type LinkTypeCreateIn = Schemas["LinkTypeCreateIn"];
export type LinkTypePatchIn = Schemas["LinkTypePatchIn"];
export type LinkTypeSync = Schemas["LinkTypeSyncOut"];
export type ServiceMetaIn = Schemas["ServiceMetaIn"];
export type LinkPair = "ticket_ticket" | "ticket_ci";

/** Filters of the CI list; `class`/`depl`/`inci` are catalogue keys. */
export type CiListQuery = {
  q?: string;
  class?: string[];
  depl?: string[];
  inci?: string[];
  limit?: number;
  offset?: number;
};

const BASE = "/api/v1/itsm";
const ADMIN = "/api/v1/admin/itsm";
const ticket = (id: number) => `/api/v1/tickets/${id}`;
const ciPath = (source: CiSource, ciId: number) => `${BASE}/cis/${source}/${ciId}`;

/** React-query keys (I14); ticket-scoped ones start with `["tickets", id]` like the zoom's own keys. */
export const itsmKeys = {
  status: () => ["itsm", "status"] as const,
  catalog: () => ["itsm", "catalog"] as const,
  cis: (query: CiListQuery) => ["itsm", "cis", query] as const,
  ci: (source: CiSource, id: number, versionId?: number | null) => ["itsm", "ci", source, id, versionId ?? null] as const,
  ciVersions: (source: CiSource, id: number) => ["itsm", "ci", source, id, "versions"] as const,
  ciTickets: (source: CiSource, id: number) => ["itsm", "ci", source, id, "tickets"] as const,
  linkTypes: (pair: LinkPair) => ["itsm", "link-types", pair] as const,
  matrix: () => ["itsm", "priority-matrix"] as const,
  service: (id: number) => ["itsm", "services", id] as const,
  serviceCis: (id: number) => ["itsm", "services", id, "cis"] as const,
  prioritySuggestion: (criticality: string | null, impact: string | null) =>
    ["itsm", "priority-suggestion", criticality, impact] as const,
  serviceStates: (ids: readonly number[]) => ["itsm", "services", "incident-states", ids] as const,
  recentIncident: (serviceId: number, excludeTicketId?: number | null) =>
    ["itsm", "services", serviceId, "recent-incident", excludeTicketId ?? null] as const,
  ticketItsm: (id: number) => ["tickets", id, "itsm"] as const,
  ticketCis: (id: number) => ["tickets", id, "cis"] as const,
  linkGraph: (id: number, depth?: number) => ["tickets", id, "link-graph", depth ?? null] as const,
  problem: (id: number) => ["tickets", id, "problem"] as const,
  majorIncident: (id: number) => ["tickets", id, "major-incident"] as const,
  admin: () => ["admin", "itsm"] as const,
  adminSetup: () => ["admin", "itsm", "setup"] as const,
  adminMatrix: () => ["admin", "itsm", "priority-matrix"] as const,
  adminLinkTypes: () => ["admin", "itsm", "link-types"] as const,
  adminServiceMeta: (id: number) => ["admin", "itsm", "services", id, "meta"] as const,
};

/** The API's `{detail: {code}}` of a failed call, or null (e.g. `itsm_off`, `ci_unavailable`). */
export function itsmErrorCode(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
  const code = (inner as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
}

/**
 * The sidebar's "CMDB" entry shows only for the Znuny read-through CIs (`/agent/itsm/cis`); in Mode B
 * the Tiqora CMDB registers its own entry, so exactly one shows per mode (cross ruling R7).
 */
export function cmdbNavVisible(status: Pick<ItsmStatus, "ci_available" | "ci_source"> | null | undefined): boolean {
  return !!status && status.ci_available && status.ci_source === "znuny";
}

const KNOWN_ERRORS = new Set([
  "itsm_off",
  "unsupported_znuny_itsm_version",
  "znuny_itsm_missing",
  "cmdb_not_installed",
  "search_too_broad",
]);

/** i18n key of the plain-language message for a failed ITSM call (`itsm.errors.*`). */
export function itsmErrorKey(err: unknown): string {
  const code = itsmErrorCode(err);
  return `itsm.errors.${code && KNOWN_ERRORS.has(code) ? code : "generic"}`;
}

export const itsmApi = {
  status(signal?: AbortSignal) {
    return api.request<ItsmStatus>("GET", `${BASE}/status`, { signal });
  },
  catalog(signal?: AbortSignal) {
    return api.request<ItsmCatalog>("GET", `${BASE}/catalog`, { signal });
  },
  cis(query: CiListQuery = {}, signal?: AbortSignal) {
    return api.request<CiPage>("GET", `${BASE}/cis`, {
      query: { q: query.q, class: query.class, depl: query.depl, inci: query.inci, limit: query.limit, offset: query.offset },
      signal,
    });
  },
  ci(source: CiSource, ciId: number, versionId?: number | null, signal?: AbortSignal) {
    return api.request<CiDetail>("GET", ciPath(source, ciId), { query: { version_id: versionId }, signal });
  },
  ciVersions(source: CiSource, ciId: number, page: { limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    return api.request<CiVersionPage>("GET", `${ciPath(source, ciId)}/versions`, { query: page, signal });
  },
  ciTickets(source: CiSource, ciId: number, signal?: AbortSignal) {
    return api.request<TicketLinkTarget[]>("GET", `${ciPath(source, ciId)}/tickets`, { signal });
  },
  linkTypes(pair: LinkPair = "ticket_ticket", signal?: AbortSignal) {
    return api.request<LinkTypeOption[]>("GET", `${BASE}/link-types`, { query: { pair }, signal });
  },
  priorityMatrix(signal?: AbortSignal) {
    return api.request<PriorityMatrix>("GET", `${BASE}/priority-matrix`, { signal });
  },
  prioritySuggestion(criticality: string | null, impact: string | null, signal?: AbortSignal) {
    return api.request<PrioritySuggestion>("GET", `${BASE}/priority-suggestion`, { query: { criticality, impact }, signal });
  },
  service(serviceId: number, signal?: AbortSignal) {
    return api.request<ServiceItsm>("GET", `${BASE}/services/${serviceId}`, { signal });
  },
  serviceCis(serviceId: number, limit?: number, signal?: AbortSignal) {
    return api.request<CiSummary[]>("GET", `${BASE}/services/${serviceId}/cis`, { query: { limit }, signal });
  },
  serviceIncidentStates(ids: readonly number[], signal?: AbortSignal) {
    return api.request<Record<string, ServiceIncidentState>>("GET", `${BASE}/services/incident-states`, {
      query: { ids: [...ids] },
      signal,
    });
  },
  recentIncident(serviceId: number, excludeTicketId?: number | null, signal?: AbortSignal) {
    return api.request<RecentIncident | null>("GET", `${BASE}/services/${serviceId}/recent-incident`, {
      query: { exclude_ticket_id: excludeTicketId },
      signal,
    });
  },

  // ---- ticket-scoped -------------------------------------------------------------------------
  ticketItsm(ticketId: number, signal?: AbortSignal) {
    return api.request<TicketItsm>("GET", `${ticket(ticketId)}/itsm`, { signal });
  },
  ticketCis(ticketId: number, signal?: AbortSignal) {
    return api.request<CiTicketLink[]>("GET", `${ticket(ticketId)}/cis`, { signal });
  },
  /** `ci` is the text form `"znuny:12"`. */
  linkCi(ticketId: number, body: { ci: string; link_type?: string | null }) {
    return api.request<CiTicketLink>("POST", `${ticket(ticketId)}/cis`, { body });
  },
  unlinkCi(ticketId: number, source: CiSource, ciId: number, linkType?: string | null) {
    return api.request<void>("DELETE", `${ticket(ticketId)}/cis/${source}/${ciId}`, { query: { link_type: linkType } });
  },
  unlinkTicket(ticketId: number, targetId: number, linkType?: string | null) {
    return api.request<void>("DELETE", `${ticket(ticketId)}/links/${targetId}`, { query: { link_type: linkType } });
  },
  linkGraph(ticketId: number, depth?: number, signal?: AbortSignal) {
    return api.request<LinkGraph>("GET", `${ticket(ticketId)}/link-graph`, { query: { depth }, signal });
  },
  linkedArticles(ticketId: number, body: LinkedArticlesIn) {
    return api.request<JobAccepted>("POST", `${ticket(ticketId)}/linked-articles`, { body });
  },
  problem: {
    get(ticketId: number, signal?: AbortSignal) {
      return api.request<Problem>("GET", `${ticket(ticketId)}/problem`, { signal });
    },
    save(ticketId: number, body: ProblemIn) {
      return api.request<Problem>("PUT", `${ticket(ticketId)}/problem`, { body });
    },
    addIncidents(ticketId: number, incidentTicketIds: number[]) {
      return api.request<Problem>("POST", `${ticket(ticketId)}/problem/incidents`, { body: { incident_ticket_ids: incidentTicketIds } });
    },
    markKnownError(ticketId: number) {
      return api.request<Problem>("POST", `${ticket(ticketId)}/problem/known-error`);
    },
    closeIncidents(ticketId: number, noteHtml?: string | null) {
      return api.request<JobAccepted>("POST", `${ticket(ticketId)}/problem/close-incidents`, { body: { note_html: noteHtml ?? null } });
    },
  },
  majorIncident: {
    get(ticketId: number, signal?: AbortSignal) {
      return api.request<MajorIncident>("GET", `${ticket(ticketId)}/major-incident`, { signal });
    },
    declare(ticketId: number, summary?: string | null) {
      return api.request<MajorIncident>("POST", `${ticket(ticketId)}/major-incident`, { body: { summary: summary ?? null } });
    },
    end(ticketId: number) {
      return api.request<MajorIncident>("DELETE", `${ticket(ticketId)}/major-incident`);
    },
    postUpdate(ticketId: number, body: MajorIncidentUpdateIn) {
      return api.request<MajorIncidentUpdate>("POST", `${ticket(ticketId)}/major-incident/updates`, { body });
    },
  },

  // ---- admin ---------------------------------------------------------------------------------
  admin: {
    get(signal?: AbortSignal) {
      return api.request<ItsmAdmin>("GET", ADMIN, { signal });
    },
    setMode(mode: ItsmMode) {
      return api.request<ItsmAdmin>("PUT", `${ADMIN}/mode`, { body: { mode } });
    },
    setup(signal?: AbortSignal) {
      return api.request<SetupPlan>("GET", `${ADMIN}/setup`, { signal });
    },
    applySetup(body: SetupApplyIn) {
      return api.request<SetupPlan>("POST", `${ADMIN}/setup`, { body });
    },
    priorityMatrix(signal?: AbortSignal) {
      return api.request<PriorityMatrix>("GET", `${ADMIN}/priority-matrix`, { signal });
    },
    savePriorityMatrix(body: Schemas["MatrixIn"]) {
      return api.request<PriorityMatrix>("PUT", `${ADMIN}/priority-matrix`, { body });
    },
    linkTypes(signal?: AbortSignal) {
      return api.request<LinkTypeList>("GET", `${ADMIN}/link-types`, { signal });
    },
    createLinkType(body: LinkTypeCreateIn) {
      return api.request<LinkType>("POST", `${ADMIN}/link-types`, { body });
    },
    patchLinkType(id: number, body: LinkTypePatchIn) {
      return api.request<LinkType>("PATCH", `${ADMIN}/link-types/${id}`, { body });
    },
    deleteLinkType(id: number) {
      return api.request<void>("DELETE", `${ADMIN}/link-types/${id}`);
    },
    /** "Aus Znuny aktualisieren": upserts the Znuny-owned rows. */
    syncLinkTypes() {
      return api.request<LinkTypeSync>("POST", `${ADMIN}/link-types/sync`);
    },
    serviceMeta(serviceId: number, signal?: AbortSignal) {
      return api.request<ServiceItsm>("GET", `${ADMIN}/services/${serviceId}/meta`, { signal });
    },
    saveServiceMeta(serviceId: number, body: ServiceMetaIn) {
      return api.request<ServiceItsm>("PUT", `${ADMIN}/services/${serviceId}/meta`, { body });
    },
  },
};
