import type {
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

export const DEMO_REPORT_CATALOGUE = { reports: DEMO_REPORTS, facts: DEMO_FACTS };

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

function ymd(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}
const DAY = 86_400_000;
const range = (n: number, f: (i: number) => string) => Array.from({ length: n }, (_, i) => f(i));
const WEEKS = range(12, (i) => ymd(Date.UTC(2026, 6, 13) + i * 7 * DAY));
const DAYS_30 = range(30, (i) => ymd(Date.UTC(2026, 8, 9) + i * DAY));
const MONTHS_12 = range(12, (i) => ymd(Date.UTC(2025, 10 + i, 1)));

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
};

function dimValues(report: BuiltinReport, key: string): string[] {
  if (key === "kind") return KIND_BY_REPORT[report.key] ?? ["create", "close"];
  if (key === "occurred" || key === "created" || key === "closed") return WEEKS;
  if (key === "snapshot") return DAYS_30;
  if (key === "booked") return MONTHS_12;
  return DIM_VALUES[key] ?? ["1"];
}

// --- numbers ------------------------------------------------------------------

function seedOf(key: string): number {
  return [...key].reduce((s, c) => s + c.charCodeAt(0), 0);
}

function measureValue(key: string, i: number, j: number): number {
  const [type, base, amp] = MEASURE[key] ?? ["count", 10, 4];
  const wave = Math.sin(i * 1.3 + j * 0.7 + seedOf(key));
  const v = Math.max(0, base + amp * wave);
  if (type === "count") return Math.round(v);
  if (type === "percent") return Math.round(Math.min(1, v) * 1000) / 1000;
  return Math.round(v * 100) / 100;
}

/** "Today" in the demo is 2026-10-09; complete weeks/months end before it. */
function window(def: BuiltinReport["definition"]): ReportResult["time_window"] {
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

/** Deterministic made-up result for one built-in report. */
export function buildDemoResult(report: BuiltinReport): ReportResult {
  const def = report.definition;
  const dims = def.dimensions ?? [];
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
    const values = dimValues(report, d.key);
    combos = combos.flatMap((c) => values.map((v) => [...c, v]));
  }
  if (dims.length === 2 && dims[0].key === "from_queue") combos = combos.filter((c) => c[0] !== c[1]);

  const heatmap = report.key === "builtin.created_heatmap";
  const rows: ReportCell[][] = combos.map((combo, i) => {
    const cells = def.measures.map((m, j) => {
      if (heatmap) {
        const weekday = Number(combo[0]);
        const hour = Number(combo[1]);
        const office = weekday <= 5 && hour >= 8 && hour <= 17;
        return office ? 3 + Math.round(3 * Math.abs(Math.sin(hour + weekday))) : weekday <= 5 ? 1 : 0;
      }
      return measureValue(m.key, i, j);
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
        grand: def.totals.grand ? aggregate(rows) : null,
        rows: def.totals.rows ? groupTotals(rows, 0, aggregate) : {},
        columns: def.totals.columns && dims.length > 1 ? groupTotals(rows, 1, aggregate) : {},
      }
    : null;

  return {
    report_key: report.key,
    columns,
    rows,
    labels,
    totals: totals && (totals.grand || Object.keys(totals.rows).length || Object.keys(totals.columns).length) ? totals : null,
    previous: null,
    row_count: rows.length,
    truncated: false,
    time_window: window(def) ?? null,
    generated_at: "2026-10-08T09:00:00Z",
    cached: false,
    facts: DEMO_FACTS,
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

const RESULTS = new Map<string, ReportResult>();
function resultFor(key: string): ReportResult | undefined {
  const cached = RESULTS.get(key);
  if (cached) return cached;
  const report = demoReport(key);
  if (!report) return undefined;
  const built = buildDemoResult(report);
  RESULTS.set(key, built);
  return built;
}

const FALLBACK_BY_SOURCE: Record<string, string> = {
  tickets: "builtin.volume_by_queue",
  ticket_events: "builtin.volume",
  daily_snapshot: "builtin.backlog_trend",
  accounted_time: "builtin.time_by_agent",
  open_tickets: "builtin.open_now",
};

/** Pure stand-in for `POST /api/v1/reports/run`: the built-in's result by `report_key`, else by `definition.source`. */
export function resolveReportRun(body: ReportRunIn): ReportRunOut {
  const byKey = body.report_key ? resultFor(body.report_key) : undefined;
  const bySource = body.definition ? resultFor(FALLBACK_BY_SOURCE[body.definition.source] ?? "") : undefined;
  const result = byKey ?? bySource ?? (resultFor("builtin.ticket_kpis") as ReportResult);
  return { result: { ...result, report_key: body.report_key ?? null }, job: null };
}
