/** Customer-portal change schedule (`/api/portal/change-schedule`, `backend/src/tiqora/api/portal/changes.py`); 404 while the feature is off. */
import type { Schemas } from "@tiqora/api-client";
import { portalApi } from "./portalApi";

export type PortalChange = Schemas["PortalChangeOut"];

/** The visible changes of a window; `truncated` is the `X-Schedule-Truncated` header (more changes than answered). */
export type PortalSchedule = { items: PortalChange[]; truncated: boolean };

const BASE = "/api/portal/change-schedule";

export const portalChangeKeys = {
  all: () => ["portal", "change-schedule"] as const,
  /** The nav probe: the schedule answering 200 shows the "Wartungsfenster" entry. */
  available: () => ["portal", "change-schedule", "available"] as const,
  list: (start: string, end: string) => ["portal", "change-schedule", "list", start, end] as const,
};

export const portalChangeApi = {
  async list(start: string, end: string, signal?: AbortSignal): Promise<PortalSchedule> {
    const { data, headers } = await portalApi.requestWithHeaders<PortalChange[]>("GET", BASE, { query: { start, end }, signal });
    return { items: data, truncated: (headers.get("X-Schedule-Truncated") ?? "").toLowerCase() === "true" };
  },
};
