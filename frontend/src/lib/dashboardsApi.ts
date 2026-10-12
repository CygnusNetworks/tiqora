/**
 * Dashboard endpoints (`backend/src/tiqora/api/v1/dashboards.py`, role templates in
 * `api/v1/admin/dashboard_templates.py`). Hand-written wrappers like `./reportsApi.ts`; the shapes
 * come from the generated schema.
 *
 * Saving a dashboard (and a template's properties) is optimistically locked by `version`: a stale
 * version answers 409 `{detail: {message, current_version}}`, thrown as `DashboardConflictError`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api, ApiError } from "./api";

export type DashboardTab = Schemas["DashboardTabOut"];
export type Dashboard = Schemas["DashboardOut"];
export type DashboardWidgetRow = Schemas["WidgetOut"];
export type WidgetResult = Schemas["WidgetDataOut"];
export type DashboardTemplate = Schemas["DashboardTemplateOut"];
export type DashboardTemplateIn = Schemas["DashboardTemplateIn"];
export type DashboardSaveIn = Schemas["DashboardSaveIn"];
export type WidgetType = Schemas["WidgetTypeOut"];
export type DashboardShareIn = Schemas["DashboardShareIn"];
export type ShareGroup = Schemas["ShareGroupOut"];
export type DashboardData = { widgets: Record<string, WidgetResult> };
export type DashboardFilters = Schemas["DashboardFilters"];
export type DashboardFiltersIn = Schemas["DashboardFiltersIn"];
export type DrillPage = Schemas["DrillPageOut"];
export type TemplateUpdate = Schemas["TemplateUpdateOut"];
export type TemplateUpdateHint = Schemas["TemplateUpdateHint"];
export type TemplateUpdateApplyIn = Schemas["TemplateUpdateApplyIn"];
export type KioskLink = Schemas["KioskLinkOut"];
export type KioskLinkCreated = Schemas["KioskLinkCreatedOut"];
export type KioskLinkIn = Schemas["KioskLinkIn"];
export type JobAccepted = Schemas["JobAccepted"];
export type DrillQuery = { offset?: number; limit?: number; sort?: string; order?: "asc" | "desc"; point?: string };

const BASE = "/api/v1/dashboards";
const KIOSK_LINKS = "/api/v1/admin/dashboard-kiosk-links";

/** The dashboard was changed by someone else (or in another tab) since it was read. */
export class DashboardConflictError extends Error {
  readonly currentVersion: number;

  constructor(message: string, currentVersion: number) {
    super(message);
    this.name = "DashboardConflictError";
    this.currentVersion = currentVersion;
  }
}

/** Reads `{detail: {message, current_version}}` (or the unwrapped form) off a 409. */
function conflictFrom(err: unknown): DashboardConflictError | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.detail as Record<string, unknown> | null;
  const inner = (body && typeof body === "object" && "detail" in body ? body.detail : body) as
    | { message?: unknown; current_version?: unknown }
    | null;
  const version = Number(inner?.current_version);
  if (!inner || typeof inner !== "object" || !Number.isFinite(version)) return null;
  const message = typeof inner.message === "string" ? inner.message : "Konflikt.";
  return new DashboardConflictError(message, version);
}

async function guarded<T>(call: Promise<T>): Promise<T> {
  try {
    return await call;
  } catch (err) {
    throw conflictFrom(err) ?? err;
  }
}

export const dashboardsApi = {
  list(signal?: AbortSignal) {
    return api.request<DashboardTab[]>("GET", BASE, { signal });
  },
  /** First visit: creates "Mein Start" from the built-in default; answers 200 afterwards. */
  ensureStart() {
    return api.request<Dashboard>("POST", `${BASE}/ensure-start`);
  },
  get(id: number, signal?: AbortSignal) {
    return api.request<Dashboard>("GET", `${BASE}/${id}`, { signal });
  },
  create(body: { name: string; icon?: string | null; copy_from?: number | null }) {
    return api.request<Dashboard>("POST", BASE, { body });
  },
  /** Whole-dashboard save; a stale `version` throws `DashboardConflictError`. */
  save(id: number, body: DashboardSaveIn) {
    return guarded(api.request<Dashboard>("PUT", `${BASE}/${id}`, { body }));
  },
  patchWidgetSettings(
    id: number,
    widgetId: string,
    body: { settings: Record<string, unknown>; version: number },
  ) {
    return guarded(
      api.request<DashboardWidgetRow>(
        "PATCH",
        `${BASE}/${id}/widgets/${encodeURIComponent(widgetId)}/settings`,
        { body },
      ),
    );
  },
  remove(id: number) {
    return api.request<void>("DELETE", `${BASE}/${id}`);
  },
  /** Dashboard-wide filters; a stale `version` throws `DashboardConflictError`. */
  patchFilters(id: number, body: DashboardFiltersIn) {
    return guarded(api.request<Dashboard>("PATCH", `${BASE}/${id}/filters`, { body }));
  },
  /** Widgets a merge from the source role template would add (template copies only). */
  templateUpdate(id: number, signal?: AbortSignal) {
    return api.request<TemplateUpdate>("GET", `${BASE}/${id}/template-update`, { signal });
  },
  /** `merge` adds the new widgets, `dismiss` hides the hint; both answer the dashboard. */
  applyTemplateUpdate(id: number, body: TemplateUpdateApplyIn) {
    return guarded(api.request<Dashboard>("POST", `${BASE}/${id}/template-update`, { body }));
  },
  /** The tickets behind a widget (or one chart `point`), paged. */
  drillTickets(id: number, widgetId: string, query: DrillQuery = {}, signal?: AbortSignal) {
    return api.request<DrillPage>(
      "GET",
      `${BASE}/${id}/widgets/${encodeURIComponent(widgetId)}/tickets`,
      { query, signal },
    );
  },
  /** Runs a report widget's report in the background after it answered `deferred` (202). */
  startReportJob(id: number, widgetId: string) {
    return api.request<JobAccepted>(
      "POST",
      `${BASE}/${id}/widgets/${encodeURIComponent(widgetId)}/report-job`,
    );
  },
  /** Admin wallboard links; the secret is in the create answer only. */
  kioskLinks: {
    list(signal?: AbortSignal) {
      return api.request<KioskLink[]>("GET", KIOSK_LINKS, { signal });
    },
    create(body: KioskLinkIn) {
      return api.request<KioskLinkCreated>("POST", KIOSK_LINKS, { body });
    },
    revoke(id: number) {
      return api.request<KioskLink>("POST", `${KIOSK_LINKS}/${id}/revoke`);
    },
    remove(id: number) {
      return api.request<void>("DELETE", `${KIOSK_LINKS}/${id}`);
    },
  },
  order(ids: number[]) {
    return api.request<void>("PUT", `${BASE}/order`, { body: { ids } });
  },
  /** Owner only; `[]` makes the dashboard personal again. 422 `path: group_ids` = group not allowed. */
  share(id: number, groupIds: number[]) {
    const body: DashboardShareIn = { group_ids: groupIds };
    return api.request<Dashboard>("PUT", `${BASE}/${id}/share`, { body });
  },
  /** Groups the agent may share with (the same list the report builder offers). */
  shareGroups(signal?: AbortSignal) {
    return api.request<ShareGroup[]>("GET", "/api/v1/reference/share-groups", { signal });
  },
  /** All widgets of a dashboard, or only `widgetIds` (one comma-joined `widgets` parameter). */
  data(id: number, widgetIds?: string[], signal?: AbortSignal) {
    const query =
      widgetIds && widgetIds.length > 0
        ? `?widgets=${widgetIds.map(encodeURIComponent).join(",")}`
        : "";
    return api.request<DashboardData>("GET", `${BASE}/${id}/data${query}`, { signal });
  },
  widgetData(id: number, widgetId: string, signal?: AbortSignal) {
    return api.request<WidgetResult>(
      "GET",
      `${BASE}/${id}/widgets/${encodeURIComponent(widgetId)}/data`,
      { signal },
    );
  },
  /** Data for unsaved settings (settings drawer, edit mode); writes no history. */
  previewData(key: string, settings: Record<string, unknown>, signal?: AbortSignal, filters?: DashboardFilters) {
    return api.request<WidgetResult>(
      "POST",
      `/api/v1/dashboard-widget-types/${encodeURIComponent(key)}/data`,
      { body: filters ? { settings, filters } : { settings }, signal },
    );
  },
  widgetTypes(signal?: AbortSignal) {
    return api.request<WidgetType[]>("GET", "/api/v1/dashboard-widget-types", { signal });
  },
  /** Admin role templates; layout is saved through `save` like any dashboard. */
  templates: {
    list(signal?: AbortSignal) {
      return api.request<DashboardTemplate[]>("GET", "/api/v1/admin/dashboard-templates", {
        signal,
      });
    },
    create(body: DashboardTemplateIn) {
      return api.request<DashboardTemplate>("POST", "/api/v1/admin/dashboard-templates", { body });
    },
    /** `version` is the optimistic lock (query parameter); stale -> `DashboardConflictError`. */
    update(id: number, body: DashboardTemplateIn, version: number) {
      return guarded(
        api.request<DashboardTemplate>("PUT", `/api/v1/admin/dashboard-templates/${id}`, {
          body,
          query: { version },
        }),
      );
    },
    remove(id: number) {
      return api.request<void>("DELETE", `/api/v1/admin/dashboard-templates/${id}`);
    },
  },
};

/** Stable hash of widget settings for query keys and the edit-mode data reuse. */
export function settingsHash(settings: unknown): string {
  const canon = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(canon)
      : v && typeof v === "object"
        ? Object.fromEntries(
            Object.entries(v as Record<string, unknown>)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([k, x]) => [k, canon(x)]),
          )
        : v;
  return JSON.stringify(canon(settings));
}

export const dashboardKeys = {
  all: ["dashboards"] as const,
  list: () => [...dashboardKeys.all, "list"] as const,
  one: (id: number) => [...dashboardKeys.all, "one", id] as const,
  data: (id: number) => [...dashboardKeys.all, "data", id] as const,
  widget: (id: number, widgetId: string) => [...dashboardKeys.all, "widget", id, widgetId] as const,
  preview: (key: string, hash: string) => [...dashboardKeys.all, "preview", key, hash] as const,
  types: () => [...dashboardKeys.all, "types"] as const,
  templates: () => [...dashboardKeys.all, "templates"] as const,
};
