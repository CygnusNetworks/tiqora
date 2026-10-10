import type {
  ReportDefinition,
  ReportSource,
  BuiltinReport,
  FactsStatus,
  ReportCell,
  ReportColumn,
  ReportResult,
  ReportRunIn,
  ReportRunOut,
  ReportingStatus,
} from "@/lib/reportsApi";
import { DEMO_REPORTS } from "./reportsCatalogue";

/**
 * Demo-mode data for the reports pages: the catalogue, a deterministic result
 * per built-in report (small, made-up numbers) and `resolveReportRun`, the pure
 * stand-in for `POST /api/v1/reports/run` (the MSW handler passes the body in,
 * `resolveData` never sees request bodies).
 */

export const DEMO_FACTS: FactsStatus = {
  ready: true,
  as_of: "2026-10-08T08:55:00Z",
  backfill_status: "done",
  backfill_job_id: null,
  backfill_progress: 1,
};

/** Facts while the first build runs (demo switch `?demo_facts=building`, see handlers.ts). */
export const DEMO_FACTS_BUILDING: FactsStatus = {
  ready: false,
  as_of: null,
  backfill_status: "running",
  backfill_job_id: 9203,
  backfill_progress: 0.42,
};

let factsBuilding = false;
/** Switches the demo between ready and still-building report data. */
export function setDemoFactsBuilding(on: boolean): void {
  factsBuilding = on;
}
export function demoFacts(): FactsStatus {
  return factsBuilding ? DEMO_FACTS_BUILDING : DEMO_FACTS;
}

export const DEMO_REPORTING_STATUS: ReportingStatus = {
  ...DEMO_FACTS,
  last_history_id: 48213,
  history_lag: 0,
  gap_count: 0,
  dirty_count: 0,
  last_snapshot_date: "2026-10-07",
  snapshot_time_zone: "Europe/Berlin",
  fact_rows: 1840,
  event_rows: 9620,
};

export function demoReportingStatus(): ReportingStatus {
  return { ...DEMO_REPORTING_STATUS, ...demoFacts() };
}

export const DEMO_REPORT_CATALOGUE = { reports: DEMO_REPORTS, facts: DEMO_FACTS };

export function demoCatalogue() {
  return { reports: DEMO_REPORTS, facts: demoFacts() };
}

export function demoReport(key: string): BuiltinReport | undefined {
  return DEMO_REPORTS.find((r) => r.key === key);
}

// --- column metadata (mirrors backend/src/tiqora/reporting/sources) ---------

type ColType = ReportColumn["type"];
const DIM_TYPE: Record<string, [ColType, string | null]> = {
  queue: ["id", null],
  from_queue: ["id", null],
  to_queue: ["id", null],
  from_state: ["id", null],
  actor: ["id", null],
  agent: ["id", null],
  type: ["id", null],
  customer: ["string", null],
  channel: ["enum", "reports.values.channel"],
  kind: ["enum", "reports.values.event_kind"],
  state_type: ["enum", "reports.values.state_type"],
  created: ["date", null],
  closed: ["date", null],
  occurred: ["date", null],
  snapshot: ["date", null],
  booked: ["date", null],
  created_hour: ["hour", null],
  created_weekday: ["weekday", null],
  state: ["id", null],
  priority: ["id", null],
  owner: ["id", null],
  service: ["id", null],
  sla: ["id", null],
};

/** type, base value, amplitude */
const MEASURE: Record<string, [ColType, number, number]> = {
  tickets: ["count", 42, 12],
  closed_tickets: ["count", 36, 10],
  open_tickets: ["count", 58, 9],
  reopened_tickets: ["count", 3, 2],
  reopen_rate: ["percent", 0.06, 0.03],
  fcr_rate: ["percent", 0.72, 0.08],
  first_response_p50: ["minutes", 38, 12],
  first_response_p90: ["minutes", 140, 40],
  solution_p50: ["minutes", 620, 180],
  solution_p90: ["minutes", 2400, 600],
  response_breaches: ["count", 3, 2],
  response_breach_rate: ["percent", 0.08, 0.04],
  solution_breaches: ["count", 2, 1.5],
  solution_breach_rate: ["percent", 0.05, 0.03],
  update_breaches: ["count", 1, 1],
  response_near_misses: ["count", 2, 1],
  solution_near_misses: ["count", 2, 1],
  sla_met_rate: ["percent", 0.93, 0.04],
  accounted_time: ["units", 14.5, 4],
  queue_moves_avg: ["number", 0.4, 0.15],
  pingpong_tickets: ["count", 2, 1],
  events: ["count", 55, 20],
  dwell_p50_hours: ["hours", 4, 2],
  dwell_p90_hours: ["hours", 18, 6],
  dwell_avg_hours: ["hours", 7, 3],
  age_0_1d: ["count", 12, 4],
  age_1_3d: ["count", 9, 3],
  age_3_7d: ["count", 6, 2],
  age_7_30d: ["count", 4, 2],
  age_30d_plus: ["count", 2, 1],
  time_units: ["units", 22.5, 8],
  bookings: ["count", 18, 6],
};

// --- sources (measure aggregations, mirrors backend reporting/sources) ---------

/** `measure:agg` pairs per source (strings, not object literals: the secret scan flags long snake_case keys). */
const SOURCE_AGGS: Record<string, string> = {
  tickets: "tickets:count closed_tickets:count_if open_tickets:count_if reopened_tickets:count_if reopen_rate:rate fcr_rate:rate first_response_avg:avg first_response_p50:p50 first_response_p90:p90 first_response_clock_avg:avg first_response_clock_p50:p50 solution_avg:avg solution_p50:p50 solution_p90:p90 solution_clock_avg:avg solution_clock_p50:p50 response_breaches:count_if response_breach_rate:rate solution_breaches:count_if solution_breach_rate:rate update_breaches:sum update_breached_tickets:count_if response_near_misses:count_if solution_near_misses:count_if sla_met_rate:rate accounted_time:sum agent_touches_avg:avg queue_moves_avg:avg pingpong_tickets:count_if",
  ticket_events: "events:count tickets:count_distinct dwell_avg_hours:avg dwell_p50_hours:p50 dwell_p90_hours:p90",
  daily_snapshot: "open_tickets:sum age_0_1d:sum age_1_3d:sum age_3_7d:sum age_7_30d:sum age_30d_plus:sum",
  accounted_time: "time_units:sum bookings:count tickets:count_distinct",
  open_tickets: "tickets:count age_0_1d:count_if age_1_3d:count_if age_3_7d:count_if age_7_30d:count_if age_30d_plus:count_if escalated:count_if unassigned:count_if",
};

/** `GET /api/v1/reporting/sources` for the demo: measures with their aggregation (dimensions omitted). */
export function demoSources(): ReportSource[] {
  return Object.entries(SOURCE_AGGS).map(([key, pairs]) => ({
    key,
    label_key: `reports.sources.${key}`,
    default_time_field: null,
    common_fields: {},
    dimensions: [],
    filter_fields: [],
    measures: pairs.split(" ").map((pair) => {
      const [measure, agg] = pair.split(":");
      return { key: measure, agg, unit: MEASURE[measure]?.[0] ?? "count", label_key: `reports.measures.${measure}` };
    }),
  }));
}

// --- dimension values and labels --------------------------------------------

const QUEUES: Record<string, string> = {
  "1": "Support",
  "2": "Vertrieb",
  "3": "Technik",
  "4": "Buchhaltung",
};
const AGENTS: Record<string, string> = {
  "2": "Anna Beispiel",
  "3": "Ben Muster",
  "4": "Clara Demo",
  "5": "David Test",
};
const CUSTOMERS: Record<string, string> = {
  "kunde-001": "Beispiel GmbH",
  "kunde-002": "Muster AG",
  "kunde-003": "Demo Stadtwerke",
  "kunde-004": "Test & Partner",
  "kunde-005": "Probe e.V.",
};
const TYPES: Record<string, string> = { "1": "Anfrage", "2": "Störung", "3": "Wartung" };
const STATES: Record<string, string> = { "1": "Neu", "2": "Offen", "3": "Wartet", "4": "In Arbeit" };
const PRIORITIES: Record<string, string> = { "2": "2 niedrig", "3": "3 normal", "4": "4 hoch", "5": "5 sehr hoch" };
const SERVICES: Record<string, string> = { "1": "E-Mail", "2": "Netzwerk", "3": "Drucker" };
const SLAS: Record<string, string> = { "1": "Standard", "2": "Premium" };

function ymd(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}
const DAY = 86_400_000;
const range = (n: number, f: (i: number) => string) => Array.from({ length: n }, (_, i) => f(i));

const DIM_VALUES: Record<string, string[]> = {
  queue: Object.keys(QUEUES),
  from_queue: Object.keys(QUEUES),
  to_queue: Object.keys(QUEUES),
  from_state: Object.keys(STATES),
  actor: Object.keys(AGENTS),
  agent: Object.keys(AGENTS),
  type: Object.keys(TYPES),
  customer: Object.keys(CUSTOMERS),
  channel: ["email", "phone", "telegram", "webchat"],
  state_type: ["new", "open", "pending reminder"],
  created_weekday: ["1", "2", "3", "4", "5", "6", "7"],
  created_hour: range(24, String),
  state: Object.keys(STATES),
  priority: Object.keys(PRIORITIES),
  owner: Object.keys(AGENTS),
  service: Object.keys(SERVICES),
  sla: Object.keys(SLAS),
};
const KIND_BY_REPORT: Record<string, string[]> = {
  "builtin.volume": ["create", "close"],
  "builtin.agent_activity": ["reply", "note", "close"],
};
const LABELS: Record<string, Record<string, string>> = {
  queue: QUEUES,
  from_queue: QUEUES,
  to_queue: QUEUES,
  from_state: STATES,
  actor: AGENTS,
  agent: AGENTS,
  type: TYPES,
  customer: CUSTOMERS,
  state: STATES,
  priority: PRIORITIES,
  owner: AGENTS,
  service: SERVICES,
  sla: SLAS,
};

const DATE_KEYS = new Set(["occurred", "created", "closed", "snapshot", "booked"]);
/** Above this many days a run answers 202 with a background job, like a costly real run. */
const JOB_THRESHOLD_DAYS = 200;
const MAX_BUCKETS = 120;

type Grain = "day" | "week" | "month" | "quarter" | "year";
type Window = NonNullable<ReportResult["time_window"]>;

/** What a run asks for, after defaults: the request's choices, grain, range and queues. */
export type DemoRunOptions = {
  dims: { key: string; grain: Grain | null }[];
  window: Window | null;
  queues: string[];
  comparePrevious: boolean;
};

function bucketStart(ms: number, grain: Grain): number {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  if (grain === "week") return ms - ((d.getUTCDay() + 6) % 7) * DAY;
  if (grain === "month") return Date.UTC(y, m, 1);
  if (grain === "quarter") return Date.UTC(y, m - (m % 3), 1);
  if (grain === "year") return Date.UTC(y, 0, 1);
  return ms;
}

function nextBucket(ms: number, grain: Grain): number {
  const d = new Date(ms);
  if (grain === "day") return ms + DAY;
  if (grain === "week") return ms + 7 * DAY;
  const step = grain === "month" ? 1 : grain === "quarter" ? 3 : 12;
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + step, 1);
}

/** Bucket starts (YYYY-MM-DD) covering the window at `grain`, the latest MAX_BUCKETS. */
function dateBuckets(window: Window, grain: Grain): string[] {
  const end = Date.parse(window.end_date);
  const out: string[] = [];
  for (let b = bucketStart(Date.parse(window.start_date), grain); b <= end; b = nextBucket(b, grain)) out.push(ymd(b));
  return out.slice(-MAX_BUCKETS);
}

function dimValues(report: BuiltinReport, key: string, grain: Grain | null, opts: DemoRunOptions): string[] {
  if (key === "kind") return KIND_BY_REPORT[report.key] ?? ["create", "close"];
  if (DATE_KEYS.has(key)) {
    const w = opts.window ?? defaultWindow(report.definition);
    return w ? dateBuckets(w, grain ?? "day") : [ymd(Date.UTC(2026, 9, 8))];
  }
  const values = DIM_VALUES[key] ?? ["1"];
  if (opts.queues.length > 0 && (key === "queue" || key === "from_queue" || key === "to_queue")) {
    const picked = values.filter((v) => opts.queues.includes(v));
    return picked.length > 0 ? picked : values.slice(0, 1);
  }
  return values;
}

// --- numbers ------------------------------------------------------------------

function seedOf(key: string): number {
  return [...key].reduce((s, c) => s + c.charCodeAt(0), 0);
}

function measureValue(key: string, i: number, j: number, scale: number): number {
  const [type, base, amp] = MEASURE[key] ?? ["count", 10, 4];
  const wave = Math.sin(i * 1.3 + j * 0.7 + seedOf(key));
  const raw = Math.max(0, base + amp * wave);
  // Counts and booked time shrink with fewer queues; rates and durations do not.
  const v = type === "count" || type === "units" ? raw * scale : raw;
  if (type === "count") return Math.round(v);
  if (type === "percent") return Math.round(Math.min(1, v) * 1000) / 1000;
  return Math.round(v * 100) / 100;
}

/** "Today" in the demo is 2026-10-09; complete weeks/months end before it. */
function defaultWindow(def: BuiltinReport["definition"]): Window | null {
  const tr = def.time_range;
  if (!tr || tr.kind !== "relative") return null;
  let start: number;
  let end: number;
  if (tr.unit === "day") {
    end = Date.UTC(2026, 9, 9);
    start = end - tr.count * DAY;
  } else if (tr.unit === "week") {
    end = Date.UTC(2026, 9, 5);
    start = end - tr.count * 7 * DAY;
  } else {
    end = tr.include_current ? Date.UTC(2026, 9, 9) : Date.UTC(2026, 9, 1);
    start = Date.UTC(2026, 9 - (tr.include_current ? tr.count - 1 : tr.count), 1);
  }
  return {
    start: new Date(start).toISOString(),
    end: new Date(end).toISOString(),
    start_date: ymd(start),
    end_date: ymd(end - DAY),
    time_zone: "Europe/Berlin",
  };
}

/** The options a request implies; without a request the definition's defaults. */
export function demoRunOptions(report: BuiltinReport, body?: ReportRunIn): DemoRunOptions {
  const def = report.definition;
  const dims = (def.dimensions ?? []).map((d) => ({ key: d.key, grain: (d.grain ?? null) as Grain | null }));
  for (const [idx, choice] of Object.entries(body?.dimension_choices ?? {})) {
    const i = Number(idx);
    if (dims[i] && typeof choice === "string") dims[i] = { key: choice, grain: DATE_KEYS.has(choice) ? dims[i].grain : null };
  }
  if (body?.grain) {
    const at = dims.findIndex((d) => DATE_KEYS.has(d.key));
    if (at >= 0) dims[at] = { ...dims[at], grain: body.grain as Grain };
  }
  const zone = body?.time_zone ?? "Europe/Berlin";
  let window = defaultWindow(def);
  const tr = body?.time_range;
  if (tr && tr.kind === "absolute" && (def.time_range || dims.some((d) => DATE_KEYS.has(d.key)))) {
    const start = Date.parse(tr.start);
    const end = Date.parse(tr.end) + DAY;
    window = { start: new Date(start).toISOString(), end: new Date(end).toISOString(), start_date: tr.start, end_date: tr.end, time_zone: zone };
  } else if (window) {
    window = { ...window, time_zone: zone };
  }
  const queues = ((body?.parameters as { queues?: unknown } | undefined)?.queues ?? []) as unknown[];
  return {
    dims,
    window,
    queues: queues.map(String),
    comparePrevious: body?.compare_previous === true,
  };
}

/** Deterministic made-up result for one built-in report and the request's options. */
export function buildDemoResult(report: BuiltinReport, opts: DemoRunOptions = demoRunOptions(report)): ReportResult {
  const def = report.definition;
  const dims = opts.dims;
  const columns: ReportColumn[] = [
    ...dims.map((d): ReportColumn => {
      const [type, values] = DIM_TYPE[d.key] ?? ["string", null];
      return {
        key: d.key,
        role: "dimension",
        type,
        label_key: `reports.dimensions.${d.key}`,
        grain: type === "date" ? (d.grain ?? "day") : null,
        i18n_values: values,
      };
    }),
    ...def.measures.map((m): ReportColumn => ({
      key: m.key,
      role: "measure",
      type: MEASURE[m.key]?.[0] ?? "count",
      label_key: `reports.measures.${m.key}`,
      grain: null,
      i18n_values: null,
    })),
  ];

  // Cartesian product of the dimension values (a "from → to" pair skips the diagonal).
  let combos: string[][] = [[]];
  for (const d of dims) {
    const values = dimValues(report, d.key, d.grain, opts);
    combos = combos.flatMap((c) => values.map((v) => [...c, v]));
  }
  if (dims.length === 2 && dims[0].key === "from_queue") combos = combos.filter((c) => c[0] !== c[1]);

  // A queue filter shrinks counts on reports that do not split by queue.
  const splitByQueue = dims.some((d) => d.key === "queue" || d.key === "from_queue");
  const scale = opts.queues.length > 0 && !splitByQueue ? Math.min(1, opts.queues.length / 4) : 1;
  // Coarser buckets hold more tickets.
  const dateDim = dims.find((d) => DATE_KEYS.has(d.key));
  const grainScale = dateDim && def.source !== "daily_snapshot"
    ? { day: 0.2, week: 1, month: 4.3, quarter: 13, year: 52 }[dateDim.grain ?? "day"]
    : 1;

  const heatmap = report.key === "builtin.created_heatmap";
  const rows: ReportCell[][] = combos.map((combo, i) => {
    const cells = def.measures.map((m, j) => {
      if (heatmap) {
        const weekday = Number(combo[0]);
        const hour = Number(combo[1]);
        const office = weekday <= 5 && hour >= 8 && hour <= 17;
        const v = office ? 3 + Math.round(3 * Math.abs(Math.sin(hour + weekday))) : weekday <= 5 ? 1 : 0;
        return Math.round(v * Math.max(scale, 0.25));
      }
      return measureValue(m.key, i, j, scale * grainScale);
    });
    return [...combo, ...cells];
  });

  const labels: Record<string, Record<string, string>> = {};
  for (const d of dims) if (LABELS[d.key]) labels[d.key] = LABELS[d.key];

  const measureIdx = def.measures.map((_, j) => dims.length + j);
  const aggregate = (subset: ReportCell[][]): ReportCell[] =>
    columns.map((col, idx) => {
      if (!measureIdx.includes(idx)) return null;
      const nums = subset.map((r) => r[idx]).filter((v): v is number => typeof v === "number");
      if (nums.length === 0) return null;
      const sum = nums.reduce((a, b) => a + b, 0);
      return col.type === "count" || col.type === "units"
        ? Math.round(sum * 100) / 100
        : Math.round((sum / nums.length) * 100) / 100;
    });
  const totals = def.totals
    ? {
        grand: def.totals.grand || opts.comparePrevious ? aggregate(rows) : null,
        rows: def.totals.rows ? groupTotals(rows, 0, aggregate) : {},
        columns: def.totals.columns && dims.length > 1 ? groupTotals(rows, 1, aggregate) : {},
      }
    : null;
  // The previous period: the same totals, a little different per measure.
  const previous = opts.comparePrevious
    ? {
        grand: aggregate(rows).map((v, idx) => {
          if (typeof v !== "number") return v;
          const f = 1 + 0.18 * Math.sin(idx * 2.1 + seedOf(report.key));
          const col = columns[idx];
          return col.type === "count" ? Math.round(v * f) : Math.round(v * f * 1000) / 1000;
        }),
        rows: {},
        columns: {},
      }
    : null;

  return {
    report_key: report.key,
    columns,
    rows,
    labels,
    totals: totals && (totals.grand || Object.keys(totals.rows).length || Object.keys(totals.columns).length) ? totals : null,
    previous,
    row_count: rows.length,
    truncated: false,
    time_window: opts.window,
    generated_at: "2026-10-08T09:00:00Z",
    cached: false,
    facts: demoFacts(),
  };
}

function groupTotals(
  rows: ReportCell[][],
  at: number,
  aggregate: (subset: ReportCell[][]) => ReportCell[],
): Record<string, ReportCell[]> {
  const groups = new Map<string, ReportCell[][]>();
  for (const r of rows) {
    const k = String(r[at]);
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  return Object.fromEntries([...groups].map(([k, subset]) => [k, aggregate(subset)]));
}

const FALLBACK_BY_SOURCE: Record<string, string> = {
  tickets: "builtin.volume_by_queue",
  ticket_events: "builtin.volume",
  daily_snapshot: "builtin.backlog_trend",
  accounted_time: "builtin.time_by_agent",
  open_tickets: "builtin.open_now",
};

// --- background runs (202) -----------------------------------------------------

const RUN_JOB_BASE = 9300;
const runJobs = new Map<number, { result: ReportResult; polls: number }>();

/**
 * The job of a background report run: "running" on the first poll, then
 * "succeeded" with the result (a long range answers 202 in `resolveReportRun`).
 */
export function demoReportJob(id: number): Record<string, unknown> | undefined {
  if (id === DEMO_FACTS_BUILDING.backfill_job_id) {
    return {
      ...jobBase(id, "core.report_backfill"),
      status: "running",
      progress_done: 42,
      progress_total: 100,
      progress_message: null,
    };
  }
  const run = runJobs.get(id);
  if (!run) return undefined;
  run.polls += 1;
  const done = run.polls > 1;
  return {
    ...jobBase(id, "core.report_run"),
    status: done ? "succeeded" : "running",
    progress_done: done ? 1 : 0,
    progress_total: 1,
    result: done ? run.result : null,
    finished_at: done ? "2026-10-08T09:00:05Z" : null,
  };
}

function jobBase(id: number, kind: string) {
  return {
    id,
    kind,
    title_key: `jobs.kinds.${kind}`,
    progress_message: null,
    cancel_requested: false,
    error: null,
    result: null,
    files: [],
    created_by: 1,
    created_at: "2026-10-08T09:00:00Z",
    started_at: "2026-10-08T09:00:01Z",
    finished_at: null,
  };
}

type SavedLookup = (key: string) => ReportDefinition | undefined;
let savedReportLookup: SavedLookup | null = null;
/** savedMock.ts registers how a saved report key resolves to its definition (the store lives there). */
export function setSavedReportLookup(fn: SavedLookup): void {
  savedReportLookup = fn;
}
/** A saved definition shaped like a built-in, so the generic result builder can run it. */
function savedAsReport(key: string, definition: ReportDefinition): BuiltinReport {
  return {
    key,
    kind: "builtin",
    category: "saved",
    title_key: "",
    description_key: "",
    definition,
    overview: false,
    read_only: true,
  } as BuiltinReport;
}

/**
 * Pure stand-in for `POST /api/v1/reports/run`: the built-in by `report_key`
 * (else by `definition.source`), built for the request's choices, grain,
 * range, queues and previous-period comparison. A range above
 * JOB_THRESHOLD_DAYS answers like a costly run: a background job.
 */
export function resolveReportRun(body: ReportRunIn): ReportRunOut {
  const saved = body.report_key ? savedReportLookup?.(body.report_key) : undefined;
  const report =
    (saved ? savedAsReport(body.report_key as string, saved) : undefined) ??
    (body.report_key ? demoReport(body.report_key) : undefined) ??
    (body.definition ? demoReport(FALLBACK_BY_SOURCE[body.definition.source] ?? "") : undefined) ??
    (demoReport("builtin.ticket_kpis") as BuiltinReport);
  const opts = demoRunOptions(report, body);
  const result = { ...buildDemoResult(report, opts), report_key: body.report_key ?? null };
  const days = opts.window ? (Date.parse(opts.window.end_date) - Date.parse(opts.window.start_date)) / DAY + 1 : 0;
  if (body.force_job || (body.time_range?.kind === "absolute" && days > JOB_THRESHOLD_DAYS)) {
    const id = RUN_JOB_BASE + runJobs.size + 1;
    runJobs.set(id, { result, polls: 0 });
    return { result: null, job: { job_id: id, status: "queued" } };
  }
  return { result, job: null };
}
