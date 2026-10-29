/**
 * Change-management endpoints for agents (`backend/src/tiqora/api/v1/itsm_change.py`): changes,
 * transitions, risk, tasks, calendar, CAB meetings and the read-only Znuny change archive. Hand-written
 * wrappers like `./itsmApi.ts`; the shapes come from the generated schema. Everything below
 * `/itsm/changes` answers 404 `change_unavailable` while ITSM is off and 409 `change_mode_a` in Mode A;
 * only `/itsm/changes/status` and `/itsm/legacy-changes` work in every mode.
 */
import type { Schemas } from "@tiqora/api-client";
import { api, ApiError } from "./api";

export type ChangeStatus = Schemas["ChangeStatusOut"];
export type ChangePage = Schemas["ChangePageOut"];
export type ChangeListItem = Schemas["ChangeListItemOut"];
export type Change = Schemas["ChangeOut"];
/**
 * Request models whose fields have server-side defaults are generated with those fields required;
 * the API accepts them left out, so the wrappers take them as optional.
 */
export type WithDefaults<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

export type ChangeCreateIn = WithDefaults<Schemas["ChangeCreateIn"], "body">;
export type ChangeCreated = Schemas["ChangeCreatedOut"];
export type ChangeUpdateIn = WithDefaults<Schemas["ChangeUpdateIn"], "clear_window">;
export type ChangeTransitionIn = WithDefaults<Schemas["TransitionIn"], "force">;
export type ChangeEvent = Schemas["ChangeEventOut"];
export type ChangeImpact = Schemas["ChangeImpactOut"];
export type ChangeConflict = Schemas["ConflictOut"];
export type ChangeRisk = Schemas["ChangeRiskOut"];
export type ChangeRiskFinding = Schemas["ChangeRiskFindingOut"];
export type ChangeRiskQuestion = Schemas["ChangeRiskQuestionOut"];
export type ChangeRiskOverrideIn = Schemas["ChangeRiskOverrideIn"];
export type ChangeApproval = Schemas["ChangeApprovalOut"];
export type ChangeApprovalSnapshot = Schemas["ApprovalSnapshotOut"];
export type ChangeTask = Schemas["TaskOut"];
export type ChangeTaskIn = WithDefaults<Schemas["TaskIn"], "body" | "task_kind">;
export type ChangeTaskUpdateIn = Schemas["TaskUpdateIn"];
export type ChangeTaskListItem = Schemas["TaskListItemOut"];
export type ChangeRelatedTicket = Schemas["RelatedTicketOut"];
export type ChangeCalendar = Schemas["ChangeCalendarOut"];
export type CalendarChange = Schemas["CalendarChangeOut"];
export type ServiceAvailability = Schemas["ServiceAvailabilityOut"];
export type ChangeTemplateSummary = Schemas["ChangeTemplateSummaryOut"];
export type FreezeOccurrences = Schemas["FreezeOccurrencesOut"];
export type FreezeOccurrence = Schemas["FreezeOccurrenceOut"];
export type Cab = Schemas["CabOut"];
export type CabMeeting = Schemas["MeetingOut"];
export type CabMeetingIn = Schemas["MeetingIn"];
export type CabMeetingUpdateIn = Schemas["MeetingUpdateIn"];
export type CabAgendaIn = Schemas["AgendaIn"];
export type CabChangeItem = Schemas["CabChangeItemOut"];
export type LegacyChangePage = Schemas["LegacyChangePageOut"];
export type LegacyChangeSummary = Schemas["LegacyChangeSummaryOut"];
export type LegacyChange = Schemas["LegacyChangeOut"];
export type JobAccepted = Schemas["JobAccepted"];

export type ChangePhase = Change["phase"];
export type ChangeType = Change["change_type"];
export type ChangeOutcome = NonNullable<ChangeTransitionIn["outcome"]>;
export type ChangeTaskKind = ChangeTask["task_kind"];
export type RiskLevel = "low" | "medium" | "high" | "very_high";
export type ChangeOrder = "planned_start" | "updated";

/** Filters of the change list; `phase`/`change_type` repeat in the query string. */
export type ChangeListQuery = {
  q?: string;
  phase?: ChangePhase[];
  change_type?: ChangeType[];
  service_id?: number;
  ci_id?: number;
  window_start?: string;
  window_end?: string;
  mine?: "owned" | "created";
  order?: ChangeOrder;
  limit?: number;
  offset?: number;
};

export type ChangeCalendarQuery = {
  start: string;
  end: string;
  service_id?: number[];
  include_tasks?: boolean;
};

export type LegacyChangeQuery = {
  q?: string;
  ticket_id?: number;
  ci?: string;
  limit?: number;
  offset?: number;
};

const BASE = "/api/v1/itsm";
const changePath = (ticketId: number) => `${BASE}/changes/${ticketId}`;

/** React-query keys (I19); all start with `["itsm", "changes"]` so one prefix invalidates the change screens. */
export const changeKeys = {
  all: () => ["itsm", "changes"] as const,
  status: () => ["itsm", "changes", "status"] as const,
  list: (query: ChangeListQuery) => ["itsm", "changes", "list", query] as const,
  detail: (ticketId: number) => ["itsm", "changes", ticketId] as const,
  events: (ticketId: number) => ["itsm", "changes", ticketId, "events"] as const,
  impact: (ticketId: number) => ["itsm", "changes", ticketId, "impact"] as const,
  conflicts: (ticketId: number) => ["itsm", "changes", ticketId, "conflicts"] as const,
  risk: (ticketId: number) => ["itsm", "changes", ticketId, "risk"] as const,
  tasks: (ticketId: number) => ["itsm", "changes", ticketId, "tasks"] as const,
  related: (ticketId: number) => ["itsm", "changes", ticketId, "related"] as const,
  approval: (ticketId: number) => ["itsm", "changes", ticketId, "approval"] as const,
  myTasks: (openOnly: boolean) => ["itsm", "changes", "my-tasks", openOnly] as const,
  calendar: (query: ChangeCalendarQuery) => ["itsm", "changes", "calendar", query] as const,
  calendarServices: (start: string, end: string) => ["itsm", "changes", "calendar-services", start, end] as const,
  templates: () => ["itsm", "changes", "templates"] as const,
  freezeWindows: (start: string, end: string) => ["itsm", "changes", "freeze-windows", start, end] as const,
  cabs: () => ["itsm", "changes", "cabs"] as const,
  meetings: (start: string, end: string) => ["itsm", "changes", "meetings", "range", start, end] as const,
  meeting: (id: number) => ["itsm", "changes", "meetings", id] as const,
  meetingSuggestions: (id: number) => ["itsm", "changes", "meetings", id, "suggestions"] as const,
  legacyList: (query: LegacyChangeQuery) => ["itsm", "changes", "legacy", "list", query] as const,
  legacy: (id: number) => ["itsm", "changes", "legacy", id] as const,
};

/** The `{detail: {...}}` of a failed change call (code plus the extra fields, e.g. `failures`, `engine_code`), or null. */
export function changeErrorDetail(err: unknown): Record<string, unknown> | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
  return inner && typeof inner === "object" && !Array.isArray(inner) ? (inner as Record<string, unknown>) : null;
}

/** The API's `{detail: {code}}` of a failed change call, or null (e.g. `change_mode_a`, `transition_refused`). */
export function changeErrorCode(err: unknown): string | null {
  const code = changeErrorDetail(err)?.code;
  return typeof code === "string" ? code : null;
}

/**
 * Whether the sidebar shows "Changes" and where it points. With `available` (Mode B, setup complete)
 * it opens the change list; otherwise, when Znuny changes can be read (Mode A, or an archive while
 * the setup is incomplete) and the user may see them, it opens the read-only Znuny list, labelled
 * "Changes (Znuny)". Mode "off" has no legacy source and shows nothing.
 */
export function changeNav(
  status: Pick<ChangeStatus, "available" | "legacy" | "can_see_legacy"> | null | undefined,
): { to: "/agent/itsm/changes" | "/agent/itsm/legacy-changes"; legacy: boolean } | null {
  if (!status) return null;
  if (status.available) return { to: "/agent/itsm/changes", legacy: false };
  if (status.legacy !== "none" && status.can_see_legacy) return { to: "/agent/itsm/legacy-changes", legacy: true };
  return null;
}

export const changeApi = {
  status(signal?: AbortSignal) {
    return api.request<ChangeStatus>("GET", `${BASE}/changes/status`, { signal });
  },
  list(query: ChangeListQuery = {}, signal?: AbortSignal) {
    return api.request<ChangePage>("GET", `${BASE}/changes`, {
      query: {
        q: query.q,
        phase: query.phase,
        change_type: query.change_type,
        service_id: query.service_id,
        ci_id: query.ci_id,
        window_start: query.window_start,
        window_end: query.window_end,
        mine: query.mine,
        order: query.order,
        limit: query.limit,
        offset: query.offset,
      },
      signal,
    });
  },
  create(body: ChangeCreateIn) {
    return api.request<ChangeCreated>("POST", `${BASE}/changes`, { body });
  },
  get(ticketId: number, signal?: AbortSignal) {
    return api.request<Change>("GET", changePath(ticketId), { signal });
  },
  update(ticketId: number, body: ChangeUpdateIn) {
    return api.request<Change>("PATCH", changePath(ticketId), { body });
  },
  transition(ticketId: number, body: ChangeTransitionIn) {
    return api.request<Change>("POST", `${changePath(ticketId)}/transition`, { body });
  },
  events(ticketId: number, page: { limit?: number; before_id?: number } = {}, signal?: AbortSignal) {
    return api.request<ChangeEvent[]>("GET", `${changePath(ticketId)}/events`, { query: page, signal });
  },
  impact(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeImpact>("GET", `${changePath(ticketId)}/impact`, { signal });
  },
  recomputeImpact(ticketId: number) {
    return api.request<JobAccepted>("POST", `${changePath(ticketId)}/impact/recompute`);
  },
  conflicts(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeConflict[]>("GET", `${changePath(ticketId)}/conflicts`, { signal });
  },
  risk(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeRisk>("GET", `${changePath(ticketId)}/risk`, { signal });
  },
  saveRiskAnswers(ticketId: number, answers: Record<string, string>) {
    return api.request<ChangeRisk>("PUT", `${changePath(ticketId)}/risk/answers`, { body: { answers } });
  },
  evaluateRisk(ticketId: number) {
    return api.request<ChangeRisk>("POST", `${changePath(ticketId)}/risk/evaluate`);
  },
  overrideRisk(ticketId: number, body: ChangeRiskOverrideIn) {
    return api.request<ChangeRisk>("PUT", `${changePath(ticketId)}/risk/override`, { body });
  },
  tasks(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeTask[]>("GET", `${changePath(ticketId)}/tasks`, { signal });
  },
  createTask(ticketId: number, body: ChangeTaskIn) {
    return api.request<ChangeTask>("POST", `${changePath(ticketId)}/tasks`, { body });
  },
  updateTask(ticketId: number, taskTicketId: number, body: ChangeTaskUpdateIn) {
    return api.request<ChangeTask>("PATCH", `${changePath(ticketId)}/tasks/${taskTicketId}`, { body });
  },
  orderTasks(ticketId: number, order: number[]) {
    return api.request<ChangeTask[]>("PUT", `${changePath(ticketId)}/tasks/order`, { body: { order } });
  },
  related(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeRelatedTicket[]>("GET", `${changePath(ticketId)}/related`, { signal });
  },
  addRelated(ticketId: number, otherTicketId: number) {
    return api.request<ChangeRelatedTicket[]>("POST", `${changePath(ticketId)}/related`, { body: { ticket_id: otherTicketId } });
  },
  removeRelated(ticketId: number, otherTicketId: number) {
    return api.request<ChangeRelatedTicket[]>("DELETE", `${changePath(ticketId)}/related/${otherTicketId}`);
  },
  approval(ticketId: number, signal?: AbortSignal) {
    return api.request<ChangeApproval>("GET", `${changePath(ticketId)}/approval`, { signal });
  },
  myTasks(openOnly = true, signal?: AbortSignal) {
    return api.request<ChangeTaskListItem[]>("GET", `${BASE}/change-tasks/mine`, { query: { open_only: openOnly }, signal });
  },
  calendar(query: ChangeCalendarQuery, signal?: AbortSignal) {
    return api.request<ChangeCalendar>("GET", `${BASE}/change-calendar`, {
      query: { start: query.start, end: query.end, service_id: query.service_id, include_tasks: query.include_tasks },
      signal,
    });
  },
  calendarServices(start: string, end: string, signal?: AbortSignal) {
    return api.request<ServiceAvailability[]>("GET", `${BASE}/change-calendar/services`, { query: { start, end }, signal });
  },
  templates(signal?: AbortSignal) {
    return api.request<ChangeTemplateSummary[]>("GET", `${BASE}/change-templates`, { signal });
  },
  freezeWindows(start: string, end: string, signal?: AbortSignal) {
    return api.request<FreezeOccurrences>("GET", `${BASE}/freeze-windows`, { query: { start, end }, signal });
  },
  cabs(signal?: AbortSignal) {
    return api.request<Cab[]>("GET", `${BASE}/cabs`, { signal });
  },
  meetings(start: string, end: string, signal?: AbortSignal) {
    return api.request<CabMeeting[]>("GET", `${BASE}/cab-meetings`, { query: { start, end }, signal });
  },
  createMeeting(body: CabMeetingIn) {
    return api.request<CabMeeting>("POST", `${BASE}/cab-meetings`, { body });
  },
  meeting(meetingId: number, signal?: AbortSignal) {
    return api.request<CabMeeting>("GET", `${BASE}/cab-meetings/${meetingId}`, { signal });
  },
  updateMeeting(meetingId: number, body: CabMeetingUpdateIn) {
    return api.request<CabMeeting>("PATCH", `${BASE}/cab-meetings/${meetingId}`, { body });
  },
  saveAgenda(meetingId: number, body: CabAgendaIn) {
    return api.request<CabMeeting>("PUT", `${BASE}/cab-meetings/${meetingId}/agenda`, { body });
  },
  meetingSuggestions(meetingId: number, signal?: AbortSignal) {
    return api.request<CabChangeItem[]>("GET", `${BASE}/cab-meetings/${meetingId}/suggestions`, { signal });
  },
  legacyList(query: LegacyChangeQuery = {}, signal?: AbortSignal) {
    return api.request<LegacyChangePage>("GET", `${BASE}/legacy-changes`, {
      query: { q: query.q, ticket_id: query.ticket_id, ci: query.ci, limit: query.limit, offset: query.offset },
      signal,
    });
  },
  legacy(znunyChangeId: number, signal?: AbortSignal) {
    return api.request<LegacyChange>("GET", `${BASE}/legacy-changes/${znunyChangeId}`, { signal });
  },

  // ---- ticket-scoped (zoom) -------------------------------------------------------------------
  /** "Als Change führen": makes an existing ticket a change record. */
  adopt(ticketId: number, changeType: ChangeType) {
    return api.request<Change>("POST", `/api/v1/tickets/${ticketId}/change`, { body: { change_type: changeType } });
  },
  /** "Change dazu anlegen": a new change that starts from this ticket. */
  createFrom(ticketId: number, body: ChangeCreateIn) {
    return api.request<ChangeCreated>("POST", `/api/v1/tickets/${ticketId}/change/create-from`, { body });
  },
};
