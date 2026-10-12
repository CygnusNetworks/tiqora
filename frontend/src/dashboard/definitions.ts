/**
 * Core widget wiring onto the shared widget contract (P13, `@/plugins/dashboardWidgets`).
 *
 * `defineCoreWidget` takes the spec's shape (`title`, `settings`, `data.endpoint`) and maps it onto
 * the one shared `WidgetDefinition` through the plugin plan's `defineWidget` — no field is added
 * to the shared type (R-DASH-WIDGET-CONTRACT). The pure helpers here (defaults, gallery group,
 * permission gate) are used by the host page, the gallery and the settings drawer.
 */
import type { ComponentType } from "react";
import {
  defineWidget,
  type WidgetCategory,
  type WidgetDefinition,
  type WidgetProps,
  type WidgetSize,
} from "@/plugins/dashboardWidgets";

/** The ten phase-1 core widget types (D9), in gallery order. */
export const CORE_WIDGET_KEYS: readonly string[] = [
  "core.ticket-list",
  "core.counter",
  "core.queue-overview",
  "core.escalations",
  "core.reminders",
  "core.recent-views",
  "core.calendar-agenda",
  "core.team-presence",
  "core.notes",
  "core.links",
];

/**
 * Widget types that honour the dashboard-wide filters (the backend providers read
 * `ProviderContext.filters`); only these get the "Dashboard-Filter anwenden" switch.
 */
export const FILTER_AWARE_KEYS: readonly string[] = [
  "core.ticket-list",
  "core.counter",
  "core.escalations",
  "core.reminders",
  "core.queue-overview",
  "core.ai-status",
  "core.report",
  "core.sla-gauge",
  "core.backlog-aging",
  "core.heatmap",
];

/** Ticket-list columns — the same literal list as the backend's `TicketColumn`. */
export const COLUMN_KEYS = [
  "tn",
  "title",
  "customer",
  "state",
  "due",
  "queue",
  "owner",
  "priority",
  "changed",
  "created",
] as const;
export type ColumnKey = (typeof COLUMN_KEYS)[number];

/** Ticket presets of the counter and ticket-list sources (backend `TICKET_PRESETS`). */
export const TICKET_PRESETS = ["mine_open", "mine_new", "unowned_new", "escalated", "ai_escalated"] as const;
export type TicketPreset = (typeof TICKET_PRESETS)[number];

/** The spec's widget shape; `defineCoreWidget` maps it onto `WidgetDefinition`. */
export interface CoreWidgetSpec<S> {
  key: string;
  version: number;
  /** i18n key -> `titleKey` */
  title: string;
  /** i18n key -> `descriptionKey` */
  description?: string;
  category: WidgetCategory;
  icon: string;
  size: WidgetSize;
  /** JSON schema -> `settingsSchema` */
  settings: Record<string, unknown> | null;
  /** provider key -> `dataEndpoint` */
  data: { endpoint: string } | null;
  /** SSE event types and `interval:<seconds>` */
  refreshOn: string[];
  component: ComponentType<WidgetProps<S>>;
  permission?: string | null;
  migrate?: (old: unknown, fromVersion: number) => S;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function defineCoreWidget<S>(d: CoreWidgetSpec<S>): WidgetDefinition<any> {
  return defineWidget<S>({
    key: d.key,
    owner: "core",
    version: d.version,
    titleKey: d.title,
    descriptionKey: d.description,
    category: d.category,
    icon: d.icon,
    size: d.size,
    settingsSchema: d.settings,
    dataEndpoint: d.data?.endpoint ?? null,
    refreshOn: d.refreshOn,
    permission: d.permission ?? null,
    component: d.component,
    migrate: d.migrate,
  });
}

/** Gallery groups: Tickets / Für mich / Team / Berichte (the AI status sits with the team). */
export function galleryGroupOf(def: Pick<WidgetDefinition, "category">): "tickets" | "me" | "team" | "reports" {
  if (def.category === "tickets") return "tickets";
  if (def.category === "team" || def.category === "ai") return "team";
  if (def.category === "reports") return "reports";
  return "me";
}

/**
 * Whether `user` may add/see this widget type (R-DASH-WIDGET-PERMISSION). A UI gate only — the
 * providers protect the data. Unknown permission values fail closed.
 */
export function canUseWidget(
  def: Pick<WidgetDefinition, "permission">,
  user: { is_admin: boolean; can_view_reports: boolean },
): boolean {
  if (def.permission == null) return true;
  if (def.permission === "admin") return user.is_admin;
  if (def.permission === "stats") return user.can_view_reports;
  return false;
}

// ---------------------------------------------------------------- settings defaults

type Schema = Record<string, unknown>;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

/** Whether `value` satisfies the (flat) property schema `prop`. */
function fits(value: unknown, prop: Schema): boolean {
  if (Array.isArray(prop.enum) && !Array.isArray(value)) return prop.enum.includes(value);
  const min = typeof prop.minimum === "number" ? prop.minimum : -Infinity;
  const max = typeof prop.maximum === "number" ? prop.maximum : Infinity;
  switch (prop.type) {
    case "integer":
      return Number.isInteger(value) && (value as number) >= min && (value as number) <= max;
    case "number":
      return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
    case "string":
      return (
        typeof value === "string" &&
        (typeof prop.maxLength !== "number" || value.length <= prop.maxLength) &&
        (typeof prop.minLength !== "number" || value.length >= prop.minLength)
      );
    case "boolean":
      return typeof value === "boolean";
    case "object": {
      if (!isPlainObject(value)) return false;
      // Declared nested properties that are present must fit too (e.g. a preset name).
      const nested = isPlainObject(prop.properties) ? prop.properties : {};
      return Object.entries(nested).every(
        ([k, sub]) => !(k in value) || !isPlainObject(sub) || fits(value[k], sub),
      );
    }
    case "array": {
      if (!Array.isArray(value)) return false;
      if (typeof prop.minItems === "number" && value.length < prop.minItems) return false;
      if (typeof prop.maxItems === "number" && value.length > prop.maxItems) return false;
      const items = isPlainObject(prop.items) ? prop.items : null;
      return items === null || value.every((item) => fits(item, items));
    }
    default:
      return true;
  }
}

/**
 * The settings a widget component receives: `def.migrate` runs when the stored version is older,
 * then every schema property with a value of the wrong type (or out of range) is replaced by its
 * default and missing ones are filled. Anything that is not an object becomes `{}` first; keys the
 * schema does not know are kept (plugin widgets may store more than they declare).
 */
export function settingsWithDefaults(
  def: Pick<WidgetDefinition, "version" | "settingsSchema" | "migrate">,
  settings: unknown,
  storedVersion: number,
): Record<string, unknown> {
  let out: Record<string, unknown> = isPlainObject(settings) ? { ...settings } : {};
  if (def.migrate && storedVersion < def.version) {
    try {
      const migrated: unknown = def.migrate(out, storedVersion);
      out = isPlainObject(migrated) ? { ...migrated } : {};
    } catch {
      out = {};
    }
  }
  const declared = def.settingsSchema?.properties;
  const props = isPlainObject(declared) ? declared : {};
  for (const [key, raw] of Object.entries(props)) {
    if (!isPlainObject(raw)) continue;
    if (key in out && fits(out[key], raw)) continue;
    if ("default" in raw) out[key] = clone(raw.default);
    else delete out[key];
  }
  return out;
}
