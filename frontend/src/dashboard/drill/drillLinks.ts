import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { isTicketSortKey, type TicketSortKey } from "@/components/searches/searchSort";

/** A chart point: dimension key -> its raw value (an id, an enum value or a bucket's first day). */
export type DrillPoint = Record<string, string | number | boolean | null>;

/** Address of the drill page. Only ids and a chart point travel in the URL, never a filter. */
export interface DrillSearch {
  d: number;
  w: string;
  point?: DrillPoint;
  sort?: TicketSortKey;
  order?: "asc" | "desc";
  offset?: number;
}

export const DRILL_PATH = "/agent/tickets/drill" as const;

/** The `<Link to search>` pair for the tickets behind a widget (or one of its chart points). */
export function drillHref(
  dashboardId: number,
  widgetId: string,
  point?: DrillPoint,
): { to: typeof DRILL_PATH; search: DrillSearch } {
  return { to: DRILL_PATH, search: { d: dashboardId, w: widgetId, ...(point ? { point } : {}) } };
}

/** Drill links are live in view and TV mode; edit and preview render the widget inert. */
export function canDrill(mode: WidgetMode): boolean {
  return mode === "view" || mode === "wallboard";
}

const isScalar = (v: unknown): v is string | number | boolean | null =>
  v === null || typeof v === "string" || typeof v === "number" || typeof v === "boolean";

function parsePoint(raw: unknown): DrillPoint | undefined {
  let value = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return undefined;
    }
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0 || entries.some(([, v]) => !isScalar(v))) return undefined;
  return Object.fromEntries(entries) as DrillPoint;
}

/** Router `validateSearch` of `/agent/tickets/drill` (values arrive JSON-parsed: "7" is the number 7). */
export function validateDrillSearch(s: Record<string, unknown>): DrillSearch {
  const d = typeof s.d === "string" ? Number(s.d) : s.d;
  const w = typeof s.w === "number" ? String(s.w) : s.w;
  const offset = typeof s.offset === "string" ? Number(s.offset) : s.offset;
  return {
    d: typeof d === "number" && Number.isSafeInteger(d) && d > 0 ? d : 0,
    w: typeof w === "string" ? w : "",
    point: parsePoint(s.point),
    sort: isTicketSortKey(s.sort) ? s.sort : undefined,
    order: s.order === "asc" || s.order === "desc" ? s.order : undefined,
    offset: typeof offset === "number" && Number.isSafeInteger(offset) && offset > 0 ? offset : undefined,
  };
}
