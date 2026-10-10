/**
 * Demo / e2e mocks for the dashboards API (`/api/v1/dashboards*`, `/dashboard-widget-types*`,
 * `/admin/dashboard-templates*`). Pure and framework-free so the MSW demo (`handlers.ts`, via
 * `mockData.ts`) and the Playwright fixture (`e2e/fixtures/mock-api.ts`) share it.
 *
 * `createDemoDashboards()` is a small stateful store: saves bump the version, create/delete/order
 * work, a stale version answers 409 like the real API. The default dashboard is a TS copy of the
 * backend's built-in default (`backend/src/tiqora/dashboard/defaults.py`). Counters carry fixed
 * `spark`/`delta` so screenshots are stable; times in lists are relative to "now".
 */
import type { Schemas } from "@tiqora/api-client";
import type {
  Dashboard,
  DashboardSaveIn,
  DashboardTab,
  DashboardTemplate,
  DashboardTemplateIn,
  DashboardWidgetRow,
  WidgetResult,
  WidgetType,
} from "@/lib/dashboardsApi";

type Settings = Record<string, unknown>;

// ---------------------------------------------------------------- typed factories (no `as` casts)

/** A complete tab; overrides win. */
export function demoTab(over: Partial<DashboardTab> = {}): DashboardTab {
  return {
    id: 1,
    name: "Mein Start",
    icon: null,
    kind: "personal",
    locked: false,
    is_owner: true,
    can_edit: true,
    can_share: true,
    owner_name: null,
    version: 1,
    sort: 0,
    group_ids: [],
    source_template_id: null,
    ...over,
  };
}

/** A complete dashboard; overrides win (the tab fields plus `widgets`). */
export function demoDashboard(over: Partial<Dashboard> = {}): Dashboard {
  return { ...demoTab(), widgets: [], ...over };
}

/** A complete role template; overrides win. */
export function demoTemplate(over: Partial<DashboardTemplate> = {}): DashboardTemplate {
  return {
    ...demoTab({ id: 100, name: "Support-Team", kind: "template", is_owner: false, can_share: false }),
    role_ids: [],
    widget_count: 0,
    ...over,
  };
}

// ---------------------------------------------------------------- the default dashboard (D5)

function widget(
  id: string,
  type_key: string,
  [x, y, w, h]: [number, number, number, number],
  settings: Settings,
  title_override: string | null = null,
): DashboardWidgetRow {
  return { id, type_key, type_version: 1, title_override, settings, layout: { lg: { x, y, w, h } } };
}

const preset = (name: string) => ({ kind: "preset", preset: name });

/** The eleven widgets of today's home page, in phone order. Ids are fixed so demo/e2e are stable. */
export function demoDefaultWidgets(): DashboardWidgetRow[] {
  return [
    widget("demo-w-open", "core.counter", [0, 0, 3, 2], { source: preset("mine_open") }),
    widget("demo-w-new", "core.counter", [3, 0, 3, 2], { source: preset("mine_new") }),
    widget("demo-w-unowned", "core.counter", [6, 0, 3, 2], { source: preset("unowned_new") }),
    widget("demo-w-escalated", "core.counter", [9, 0, 3, 2], { source: preset("escalated"), tone: "danger" }),
    widget("demo-w-escalations", "core.escalations", [0, 2, 12, 3], { limit: 8, within_hours: 0 }),
    widget("demo-w-list-new", "core.ticket-list", [0, 5, 6, 5], { source: preset("mine_new"), sort: "age", order: "asc", limit: 8 }),
    widget("demo-w-list-open", "core.ticket-list", [6, 5, 6, 5], { source: preset("mine_open"), sort: "age", order: "desc", limit: 8 }),
    widget(
      "demo-w-list-inbox",
      "core.ticket-list",
      [0, 10, 6, 5],
      { source: preset("unowned_new"), sort: "age", order: "asc", limit: 8 },
      "Team-Eingang (zum Übernehmen)",
    ),
    widget("demo-w-reminders", "core.reminders", [6, 10, 6, 5], { window_hours: 24, limit: 6 }),
    widget("demo-w-recent", "core.recent-views", [0, 15, 12, 2], { limit: 8 }),
    widget("demo-w-queues", "core.queue-overview", [0, 17, 12, 3], { limit: 8, only_with_work: true }),
  ];
}

const WIDGET_TYPES: WidgetType[] = [
  { key: "core.calendar-agenda", version: 1, has_data: true },
  { key: "core.counter", version: 1, has_data: true },
  { key: "core.escalations", version: 1, has_data: true },
  { key: "core.links", version: 1, has_data: false },
  { key: "core.notes", version: 1, has_data: false },
  { key: "core.queue-overview", version: 1, has_data: false },
  { key: "core.recent-views", version: 1, has_data: false },
  { key: "core.reminders", version: 1, has_data: true },
  { key: "core.team-presence", version: 1, has_data: true },
  { key: "core.ticket-list", version: 1, has_data: true },
];

// ---------------------------------------------------------------- demo widget data

type Item = Schemas["TicketListItem"];

const DEMO_TIME = "2026-10-09T07:30:00Z";

function ticket(id: number, tn: string, title: string, over: Partial<Item> = {}): Item {
  return {
    id,
    tn,
    title,
    age_seconds: 3600,
    ai_escalated: false,
    ai_paused: false,
    archive_flag: 0,
    attachment_count: 0,
    change_time: DEMO_TIME,
    channel: "email",
    create_time: DEMO_TIME,
    escalation_response_time: 0,
    escalation_solution_time: 0,
    escalation_time: 0,
    escalation_update_time: 0,
    has_ai_summary: false,
    lock_id: 1,
    owner_id: 1,
    owner_login: "aturner",
    owner_name: "Alex Turner",
    priority: "3 normal",
    priority_id: 3,
    queue_id: 2,
    queue_name: "Support",
    state: "open",
    state_id: 4,
    state_type: "open",
    until_time: 0,
    customer_id: "example-gmbh",
    customer_user_id: "mia.berg@example.org",
    customer_email: "mia.berg@example.org",
    first_from: "Mia Berg <mia.berg@example.org>",
    ...over,
  };
}

const MINE_OPEN: Item[] = [
  ticket(1101, "2026100901", "Drucker im 2. OG druckt nur schwarz", { age_seconds: 4 * 86400 }),
  ticket(1102, "2026100902", "VPN bricht nach 10 Minuten ab", { age_seconds: 3 * 86400, priority: "4 high", priority_id: 4 }),
  ticket(1103, "2026100903", "Zugriff auf Projektlaufwerk fehlt", { age_seconds: 2 * 86400 }),
  ticket(1104, "2026100904", "Rechnung 4711 doppelt versendet", { age_seconds: 30 * 3600, queue_id: 3, queue_name: "Buchhaltung" }),
  ticket(1105, "2026100905", "Neue Monitore für Team Vertrieb", { age_seconds: 20 * 3600 }),
  ticket(1106, "2026100906", "Passwort-Reset funktioniert nicht", { age_seconds: 9 * 3600 }),
];
const MINE_NEW: Item[] = [
  ticket(1201, "2026101001", "Anfrage zu Wartungsvertrag", { age_seconds: 1500, state: "new", state_id: 1, state_type: "new" }),
  ticket(1202, "2026101002", "Webseite zeigt Fehler 502", { age_seconds: 2700, state: "new", state_id: 1, state_type: "new", priority: "5 very high", priority_id: 5 }),
  ticket(1203, "2026101003", "Terminänderung Schulung", { age_seconds: 5400, state: "new", state_id: 1, state_type: "new", queue_id: 4, queue_name: "Vertrieb" }),
];
const UNOWNED_NEW: Item[] = [
  ticket(1301, "2026101004", "Lieferung unvollständig", { age_seconds: 600, state: "new", state_id: 1, state_type: "new", owner_id: 1, owner_login: "root@localhost", owner_name: "Root", queue_id: 4, queue_name: "Vertrieb" }),
  ticket(1302, "2026101005", "Frage zur Kündigungsfrist", { age_seconds: 4000, state: "new", state_id: 1, state_type: "new", owner_id: 1, owner_login: "root@localhost", owner_name: "Root" }),
  ticket(1303, "2026101006", "Lizenzschlüssel abgelaufen", { age_seconds: 7200, state: "new", state_id: 1, state_type: "new", owner_id: 1, owner_login: "root@localhost", owner_name: "Root" }),
  ticket(1304, "2026101007", "Konto gesperrt nach Urlaub", { age_seconds: 9000, state: "new", state_id: 1, state_type: "new", owner_id: 1, owner_login: "root@localhost", owner_name: "Root" }),
];
const ESCALATED: Item[] = [MINE_OPEN[1], MINE_OPEN[3]];
const AI_ESCALATED: Item[] = [MINE_OPEN[2]];

const PRESET_ITEMS: Record<string, Item[]> = {
  mine_open: MINE_OPEN,
  mine_new: MINE_NEW,
  unowned_new: UNOWNED_NEW,
  escalated: ESCALATED,
  ai_escalated: AI_ESCALATED,
};

/** Fixed per preset so the home page looks the same on every screenshot. */
const COUNTERS: Record<string, { value: number; delta: number | null; spark: number[] }> = {
  mine_open: { value: 12, delta: 2, spark: [8, 9, 9, 10, 10, 11, 9, 9, 10, 11, 12, 11, 10, 12] },
  mine_new: { value: 3, delta: -1, spark: [2, 4, 3, 5, 4, 2, 1, 3, 4, 5, 4, 3, 4, 3] },
  unowned_new: { value: 4, delta: 1, spark: [6, 5, 4, 5, 6, 7, 5, 4, 3, 4, 5, 3, 3, 4] },
  escalated: { value: 2, delta: 0, spark: [1, 1, 2, 2, 3, 3, 2, 1, 1, 2, 2, 2, 2, 2] },
  ai_escalated: { value: 1, delta: null, spark: [0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 1] },
};

const presetOf = (settings: Settings): string => {
  const source = settings.source as { kind?: string; preset?: string } | undefined;
  return source?.kind === "preset" && source.preset && source.preset in PRESET_ITEMS ? source.preset : "mine_open";
};
const limitOf = (settings: Settings, fallback: number): number =>
  typeof settings.limit === "number" ? settings.limit : fallback;

const nowEpoch = () => Math.floor(Date.now() / 1000);
const MIN = 60;
const HOUR = 3600;

type EscalationKind = "first_response" | "update" | "solution" | "escalation";

function escalationItems(): { ticket: Item; epoch: number; kind: EscalationKind }[] {
  const now = nowEpoch();
  return [
    { ticket: MINE_OPEN[1], epoch: now - (3 * HOUR + 20 * MIN), kind: "solution" },
    { ticket: MINE_OPEN[3], epoch: now - 40 * MIN, kind: "update" },
    { ticket: MINE_NEW[1], epoch: now + 25 * MIN, kind: "first_response" },
    { ticket: MINE_OPEN[0], epoch: now + 2 * HOUR + 10 * MIN, kind: "escalation" },
  ];
}

/**
 * Plausible data for one widget type, or `undefined` when the type has no server side (recent
 * views, queues, notes, links, plugin widgets) - such widgets are absent from a batch like in
 * the real API.
 */
export function demoWidgetData(key: string, settings: Settings = {}): WidgetResult | undefined {
  switch (key) {
    case "core.counter": {
      const c = COUNTERS[presetOf(settings)];
      return { status: "ok", data: { value: c.value, delta: c.delta, spark: [...c.spark] } };
    }
    case "core.ticket-list": {
      const all = PRESET_ITEMS[presetOf(settings)];
      return { status: "ok", data: { items: all.slice(0, limitOf(settings, 8)), total: all.length } };
    }
    case "core.escalations": {
      const withinHours = typeof settings.within_hours === "number" ? settings.within_hours : 8;
      const horizon = nowEpoch() + withinHours * HOUR;
      const items = escalationItems()
        .filter((e) => withinHours === 0 || e.epoch <= horizon)
        .sort((a, b) => a.epoch - b.epoch);
      return {
        status: "ok",
        data: { items: items.slice(0, limitOf(settings, 8)), total: items.length, oldest_epoch: items[0]?.epoch ?? null },
      };
    }
    case "core.reminders": {
      const now = nowEpoch();
      const windowHours = typeof settings.window_hours === "number" ? settings.window_hours : 24;
      const items = [
        { ticket: MINE_OPEN[4], until: now - 90 * MIN, overdue: true },
        { ticket: MINE_OPEN[5], until: now + 3 * HOUR, overdue: false },
        { ticket: MINE_OPEN[2], until: now + 20 * HOUR, overdue: false },
        { ticket: MINE_OPEN[0], until: now + 40 * HOUR, overdue: false },
      ].filter((r) => r.until <= now + windowHours * HOUR);
      return { status: "ok", data: { items: items.slice(0, limitOf(settings, 6)), total: items.length } };
    }
    case "core.calendar-agenda": {
      const d = new Date();
      const at = (h: number, m: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m).toISOString();
      const items = [
        { appointment_id: 1, calendar_id: 1, title: "Team-Daily", description: null, location: "Besprechungsraum 2", start_time: at(9, 15), end_time: at(9, 30), all_day: false, is_recurring: true },
        { appointment_id: 2, calendar_id: 1, title: "Kundentermin Example GmbH", description: null, location: "Videocall", start_time: at(11, 0), end_time: at(12, 0), all_day: false, is_recurring: false },
        { appointment_id: 3, calendar_id: 1, title: "Wartungsfenster Mailserver", description: null, location: null, start_time: at(18, 0), end_time: at(19, 30), all_day: false, is_recurring: false },
      ];
      return { status: "ok", data: { items: items.slice(0, limitOf(settings, 6)) } };
    }
    case "core.team-presence": {
      const online = [
        { id: 1, login: "aturner", full_name: "Alex Turner", avatar_url: null },
        { id: 2, login: "mrivera", full_name: "Maya Rivera", avatar_url: null },
        { id: 3, login: "jkowalski", full_name: "Jonas Kowalski", avatar_url: null },
      ];
      return { status: "ok", data: { online: online.slice(0, limitOf(settings, 8)), total_online: online.length } };
    }
    default:
      return undefined;
  }
}

// ---------------------------------------------------------------- stateful store

/** A failure the real API would answer with a status; `resolve` turns it into a response. */
export class DemoHttpError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown) {
    super(typeof body === "string" ? body : `HTTP ${status}`);
    this.name = "DemoHttpError";
    this.status = status;
    this.body = body;
  }
}

const conflict = (kind: string, version: number) =>
  new DemoHttpError(409, { detail: { message: `${kind} wurde inzwischen geändert.`, current_version: version } });
const notFound = () => new DemoHttpError(404, { detail: "Nicht gefunden." });

export interface DemoDashboardsOptions {
  /** Extra personal tabs, e.g. `["Alt"]` for the e2e fixture. */
  extraTabs?: string[];
  /** Further complete dashboards (tab fields + widgets) after the personal ones. */
  extraDashboards?: Partial<Dashboard>[];
}

let widgetCounter = 0;
const freshId = () => `demo-n${(++widgetCounter).toString(36).padStart(5, "0")}`;

function tabOf(d: Dashboard): DashboardTab {
  const { widgets: _widgets, ...tab } = d;
  return tab;
}

export function createDemoDashboards(options: DemoDashboardsOptions = {}) {
  let nextId = 1;
  let nextTemplateId = 100;
  let dashboards: Dashboard[] = [];
  let templates: DashboardTemplate[] = [];

  const add = (name: string, widgets: DashboardWidgetRow[]): Dashboard => {
    const d = demoDashboard({ id: nextId++, name, sort: dashboards.length, widgets });
    dashboards.push(d);
    return d;
  };
  const find = (id: number): Dashboard => {
    const d = dashboards.find((x) => x.id === id);
    if (!d) throw notFound();
    return d;
  };
  const replace = (d: Dashboard) => {
    dashboards = dashboards.map((x) => (x.id === d.id ? d : x));
    return d;
  };
  const sorted = () => [...dashboards].sort((a, b) => a.sort - b.sort);

  add("Mein Start", demoDefaultWidgets());
  for (const name of options.extraTabs ?? []) add(name, []);
  for (const over of options.extraDashboards ?? []) {
    dashboards.push(demoDashboard({ ...over, id: nextId++, sort: dashboards.length }));
  }
  templates = [
    demoTemplate({ id: nextTemplateId++, name: "Support-Team", role_ids: [2], widget_count: 11, sort: 0 }),
  ];

  const store = {
    list: (): DashboardTab[] => sorted().map(tabOf),
    ensureStart: (): Dashboard => sorted()[0] ?? add("Mein Start", demoDefaultWidgets()),
    get: (id: number): Dashboard => find(id),
    create(body: { name: string; icon?: string | null; copy_from?: number | null }): Dashboard {
      const source = body.copy_from != null ? find(body.copy_from) : null;
      const d = add(body.name, source ? source.widgets.map((w) => ({ ...w, id: freshId() })) : []);
      d.icon = body.icon ?? null;
      return d;
    },
    save(id: number, body: DashboardSaveIn): Dashboard {
      const d = find(id);
      if (!d.can_edit) throw new DemoHttpError(403, { detail: "Dieses Dashboard ist schreibgeschützt." });
      if (body.version !== d.version) throw conflict("Dieses Dashboard", d.version);
      return replace({
        ...d,
        name: body.name,
        icon: body.icon ?? null,
        version: d.version + 1,
        widgets: body.widgets.map((w) => ({ ...w, settings: w.settings ?? {}, title_override: w.title_override ?? null })),
      });
    },
    patchWidgetSettings(id: number, widgetId: string, body: { settings: Settings; version: number }): DashboardWidgetRow {
      const d = find(id);
      if (body.version !== d.version) throw conflict("Dieses Dashboard", d.version);
      const row = d.widgets.find((w) => w.id === widgetId);
      if (!row) throw notFound();
      const updated = { ...row, settings: body.settings };
      replace({ ...d, version: d.version + 1, widgets: d.widgets.map((w) => (w.id === widgetId ? updated : w)) });
      return updated;
    },
    remove(id: number): void {
      find(id);
      if (dashboards.length <= 1) throw new DemoHttpError(422, { detail: { message: "Das letzte Dashboard bleibt bestehen.", path: "id" } });
      dashboards = dashboards.filter((d) => d.id !== id);
    },
    order(ids: number[]): void {
      const rest = sorted().map((d) => d.id).filter((id) => !ids.includes(id));
      const all = [...ids.filter((id) => dashboards.some((d) => d.id === id)), ...rest];
      dashboards = dashboards.map((d) => ({ ...d, sort: all.indexOf(d.id) }));
    },
    share(id: number, groupIds: number[]): Dashboard {
      const d = find(id);
      return replace({ ...d, group_ids: groupIds, kind: groupIds.length > 0 ? "shared" : "personal", version: d.version + 1 });
    },
    data(id: number, widgetIds?: string[]): { widgets: Record<string, WidgetResult> } {
      const d = find(id);
      const out: Record<string, WidgetResult> = {};
      for (const w of d.widgets) {
        if (widgetIds && widgetIds.length > 0 && !widgetIds.includes(w.id)) continue;
        const result = demoWidgetData(w.type_key, w.settings ?? {});
        if (result) out[w.id] = result;
      }
      return { widgets: out };
    },
    widgetData(id: number, widgetId: string): WidgetResult {
      const row = find(id).widgets.find((w) => w.id === widgetId);
      const result = row ? demoWidgetData(row.type_key, row.settings ?? {}) : undefined;
      if (!result) throw notFound();
      return result;
    },
    previewData(key: string, settings: Settings): WidgetResult {
      const result = demoWidgetData(key, settings);
      if (!result) throw notFound();
      return result;
    },
    widgetTypes: (): WidgetType[] => WIDGET_TYPES,
    templates: {
      list: (): DashboardTemplate[] => templates,
      create(body: DashboardTemplateIn): DashboardTemplate {
        const t = demoTemplate({ ...body, id: nextTemplateId++, icon: body.icon ?? null, widget_count: 11 });
        templates = [...templates, t];
        return t;
      },
      update(id: number, body: DashboardTemplateIn, version: number): DashboardTemplate {
        const t = templates.find((x) => x.id === id);
        if (!t) throw notFound();
        if (version !== t.version) throw conflict("Diese Vorlage", t.version);
        const next = { ...t, ...body, icon: body.icon ?? null, version: t.version + 1 };
        templates = templates.map((x) => (x.id === id ? next : x));
        return next;
      },
      remove(id: number): void {
        if (!templates.some((x) => x.id === id)) throw notFound();
        templates = templates.filter((x) => x.id !== id);
      },
    },
  };
  return store;
}

export type DemoDashboards = ReturnType<typeof createDemoDashboards>;

/**
 * A dashboard a colleague shared with the demo agent (mockup tab "Team Support"): gallery widgets
 * that are not on the default, plus the demo plugin's widget (`org.example.demo`, `demo/mockData.ts`),
 * which reaches the grid through the widget registry like any plugin widget.
 */
export function demoTeamDashboard(): Partial<Dashboard> {
  return {
    name: "Team Support",
    kind: "shared",
    is_owner: false,
    can_edit: false,
    can_share: false,
    owner_name: "Maya Rivera",
    group_ids: [3],
    widgets: [
      widget("demo-t-unowned", "core.counter", [0, 0, 3, 2], { source: preset("unowned_new") }),
      widget("demo-t-escalated", "core.counter", [3, 0, 3, 2], { source: preset("escalated"), tone: "danger" }),
      widget("demo-t-plugin", "org.example.demo.hello", [6, 0, 6, 2], {}),
      widget("demo-t-team", "core.team-presence", [0, 2, 4, 3], { limit: 8 }),
      widget("demo-t-agenda", "core.calendar-agenda", [4, 2, 4, 3], { days: 1, limit: 6 }),
      widget("demo-t-notes", "core.notes", [8, 2, 4, 3], {
        markdown: "**Rufbereitschaft** diese Woche: Jonas Kowalski.\n\nWartungsfenster Mailserver heute ab 18 Uhr.",
      }),
    ],
  };
}

/** The store of the public demo build (one per page load). */
export const demoDashboards: DemoDashboards = createDemoDashboards({ extraDashboards: [demoTeamDashboard()] });

// ---------------------------------------------------------------- HTTP routing

export interface DemoResponse {
  status: number;
  body: unknown;
}

const noContent: DemoResponse = { status: 204, body: null };
const ok = (body: unknown, status = 200): DemoResponse => ({ status, body });

/**
 * Routes one dashboards request to the store. `body` is the parsed JSON body (the MSW handler and
 * the e2e route read it, `resolveData` does not); `query` carries `widgets` / `version`.
 * Returns `undefined` for paths that are not ours.
 */
export function resolveDashboards(
  store: DemoDashboards,
  method: string,
  path: string,
  body?: unknown,
  query?: URLSearchParams,
): DemoResponse | undefined {
  const i = path.indexOf("/api/v1/");
  if (i < 0) return undefined;
  const p = path.slice(i + "/api/v1".length).replace(/\/$/, "");
  const isOurs = p === "/dashboards" || p.startsWith("/dashboards/") || p.startsWith("/dashboard-widget-types") || p.startsWith("/admin/dashboard-templates");
  if (!isOurs) return undefined;
  const b = (body ?? {}) as Record<string, unknown>;
  try {
    let m: RegExpExecArray | null;
    if (p === "/dashboards") {
      if (method === "GET") return ok(store.list());
      if (method === "POST") return ok(store.create(b as Parameters<DemoDashboards["create"]>[0]), 201);
    }
    if (p === "/dashboards/ensure-start" && method === "POST") return ok(store.ensureStart());
    if (p === "/dashboards/order" && method === "PUT") {
      store.order((b.ids as number[]) ?? []);
      return noContent;
    }
    if ((m = /^\/dashboards\/(\d+)$/.exec(p))) {
      const id = Number(m[1]);
      if (method === "GET") return ok(store.get(id));
      if (method === "PUT") return ok(store.save(id, b as unknown as DashboardSaveIn));
      if (method === "DELETE") {
        store.remove(id);
        return noContent;
      }
    }
    if ((m = /^\/dashboards\/(\d+)\/share$/.exec(p)) && method === "PUT")
      return ok(store.share(Number(m[1]), (b.group_ids as number[]) ?? []));
    if ((m = /^\/dashboards\/(\d+)\/data$/.exec(p)) && method === "GET") {
      const ids = query?.get("widgets")?.split(",").filter(Boolean);
      return ok(store.data(Number(m[1]), ids));
    }
    if ((m = /^\/dashboards\/(\d+)\/widgets\/([^/]+)\/data$/.exec(p)) && method === "GET")
      return ok(store.widgetData(Number(m[1]), decodeURIComponent(m[2])));
    if ((m = /^\/dashboards\/(\d+)\/widgets\/([^/]+)\/settings$/.exec(p)) && method === "PATCH")
      return ok(store.patchWidgetSettings(Number(m[1]), decodeURIComponent(m[2]), b as { settings: Settings; version: number }));
    if (p === "/dashboard-widget-types" && method === "GET") return ok(store.widgetTypes());
    if ((m = /^\/dashboard-widget-types\/([^/]+)\/data$/.exec(p)) && method === "POST")
      return ok(store.previewData(decodeURIComponent(m[1]), (b.settings as Settings) ?? {}));
    if (p === "/admin/dashboard-templates") {
      if (method === "GET") return ok(store.templates.list());
      if (method === "POST") return ok(store.templates.create(b as unknown as DashboardTemplateIn), 201);
    }
    if ((m = /^\/admin\/dashboard-templates\/(\d+)$/.exec(p))) {
      const id = Number(m[1]);
      if (method === "PUT") return ok(store.templates.update(id, b as unknown as DashboardTemplateIn, Number(query?.get("version"))));
      if (method === "DELETE") {
        store.templates.remove(id);
        return noContent;
      }
    }
  } catch (err) {
    if (err instanceof DemoHttpError) return { status: err.status, body: err.body };
    throw err;
  }
  return undefined;
}

/** `dashboardsMock.resolve(method, path, body)` of the MSW handlers (demo store). */
export function resolve(method: string, path: string, body?: unknown, query?: URLSearchParams): DemoResponse | undefined {
  return resolveDashboards(demoDashboards, method, path, body, query);
}
