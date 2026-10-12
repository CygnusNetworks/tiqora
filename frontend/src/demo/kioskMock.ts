/**
 * Demo / e2e mock of the public wallboard API (`/api/v1/kiosk/board`, `/kiosk/dashboards/{id}/data`).
 * Pure and framework-free like `dashboardsMock.ts`, which it reads the boards from. It mirrors the
 * backend's redaction in shape only: widget settings are cut to the per-type whitelist (plus the
 * derived preset name of counters and ticket lists), widgets
 * without a wallboard rule answer `forbidden`, and the secret travels in `X-Tiqora-Kiosk`.
 */
import type { KioskBoard, KioskWidget } from "@/lib/kioskApi";
import type { DemoDashboards, DemoResponse } from "./dashboardsMock";
import { demoWidgetData } from "./dashboardsMock";

/** Mirrors `KIOSK_SETTINGS_WHITELIST` of `backend/src/tiqora/dashboard/kiosk.py`. */
export const KIOSK_SETTINGS_WHITELIST: Record<string, readonly string[]> = {
  "core.counter": ["tone"],
  "core.ticket-list": ["columns", "limit", "density", "sort"],
  "core.queue-overview": ["limit", "only_with_work"],
  "core.escalations": ["limit", "within_hours"],
  "core.reminders": ["limit", "window_hours"],
  "core.calendar-agenda": ["days", "limit"],
  "core.team-presence": ["limit"],
  "core.report": ["display", "max_rows"],
  "core.sla-gauge": ["target_percent"],
  "core.backlog-aging": ["max_rows"],
  "core.heatmap": [],
  "core.chat-queue": ["limit"],
  "core.ai-status": [],
};

/** Mirrors `KIOSK_DERIVED_SETTINGS` of the backend: settings the server computes for the wallboard. */
export const KIOSK_DERIVED_SETTINGS: Record<string, readonly string[]> = {
  "core.counter": ["preset"],
  "core.ticket-list": ["preset"],
};
const PRESET_NAMES: readonly string[] = ["mine_open", "mine_new", "unowned_new", "escalated", "ai_escalated"];

/** The preset name of a preset source (the default source is "mine_open"), like the backend's `redact_settings`. */
function derivedPreset(typeKey: string, settings: Record<string, unknown>): { preset?: string } {
  if (!KIOSK_DERIVED_SETTINGS[typeKey]?.includes("preset")) return {};
  const src = settings.source;
  if (!src || typeof src !== "object") return {};
  const { kind = "preset", preset = "mine_open" } = src as { kind?: unknown; preset?: unknown };
  return kind === "preset" && typeof preset === "string" && PRESET_NAMES.includes(preset) ? { preset } : {};
}

/** Token the demo and the e2e fixture treat as a revoked or unknown link. */
export const INVALID_KIOSK_TOKEN = "invalid-token";
export const MSG_INVALID_LINK = "Wallboard-Link nicht gefunden.";
export const MSG_HIDDEN = "Auf dem Wallboard ausgeblendet.";

export interface DemoKioskOptions {
  name?: string;
  rotateSeconds?: number;
  showDetails?: boolean;
}

function widgetView(
  row: { id: string; type_key: string; type_version: number; layout: KioskWidget["layout"]; settings?: Record<string, unknown>; title_override?: string | null },
  showDetails = false,
): KioskWidget {
  const allowed = KIOSK_SETTINGS_WHITELIST[row.type_key] ?? [];
  const settings = {
    ...Object.fromEntries(Object.entries(row.settings ?? {}).filter(([k]) => allowed.includes(k))),
    ...derivedPreset(row.type_key, row.settings ?? {}),
  };
  // A custom title may name a customer, so it only leaves with details (like the backend's `kiosk_title`).
  const title = showDetails && row.title_override ? { title_override: row.title_override } : {};
  return { id: row.id, type_key: row.type_key, type_version: row.type_version, layout: row.layout, settings, ...title };
}

/** The board of a demo link: every dashboard of the store, widgets reduced to what renders. */
export function demoKioskBoard(store: DemoDashboards, options: DemoKioskOptions = {}): KioskBoard {
  return {
    name: options.name ?? "Wallboard Demo",
    rotate_seconds: options.rotateSeconds ?? 60,
    refresh_seconds: 30,
    show_details: options.showDetails ?? false,
    server_time: new Date().toISOString(),
    dashboards: store.list().map((tab) => {
      const d = store.get(tab.id);
      return { id: d.id, name: d.name, filters: d.filters, widgets: d.widgets.map((w) => widgetView(w, options.showDetails)) };
    }),
  };
}

/**
 * Routes one kiosk request. `token` is the `X-Tiqora-Kiosk` header: missing or
 * `INVALID_KIOSK_TOKEN` answers the same 404 for every failure like the real API.
 * Returns `undefined` for paths that are not ours.
 */
export function resolveKiosk(
  store: DemoDashboards,
  method: string,
  path: string,
  token: string | null | undefined,
  options: DemoKioskOptions = {},
): DemoResponse | undefined {
  const i = path.indexOf("/api/v1/kiosk/");
  if (i < 0 || method !== "GET") return undefined;
  const p = path.slice(i + "/api/v1/kiosk".length).replace(/\/$/, "");
  if (!token || token === INVALID_KIOSK_TOKEN) return { status: 404, body: { detail: { message: MSG_INVALID_LINK } } };
  if (p === "/board") return { status: 200, body: demoKioskBoard(store, options) };
  const m = /^\/dashboards\/(\d+)\/data$/.exec(p);
  if (!m) return undefined;
  const board = demoKioskBoard(store, options).dashboards.find((d) => d.id === Number(m[1]));
  if (!board) return { status: 404, body: { detail: { message: MSG_INVALID_LINK } } };
  const widgets = Object.fromEntries(
    board.widgets.map((w) => {
      const full = store.get(board.id).widgets.find((x) => x.id === w.id);
      const data = KIOSK_SETTINGS_WHITELIST[w.type_key] && full ? demoWidgetData(w.type_key, full.settings ?? {}, board.filters?.queue_ids) : undefined;
      return [w.id, data ?? { status: "forbidden" as const, error: MSG_HIDDEN }];
    }),
  );
  return { status: 200, body: { generated_at: new Date().toISOString(), widgets } };
}
