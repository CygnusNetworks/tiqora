import type { ImportIssue, StatPreview, ZnunyStatsApplyOut, ZnunyStatsPreview } from "@/lib/reportsApi";
import type {
  Recurrence,
  ReportRun,
  ReportSchedule,
  ReportScheduleIn,
  ReportScheduleListItem,
  ReportScheduleUpdateIn,
} from "@/lib/reportSchedulesApi";

/**
 * Demo-mode data for report schedules and the Znuny statistics import: a
 * small in-memory store behind `resolveSchedules` (method, path, body ->
 * `{status, body}`), wired into the MSW handlers like `dashboardsMock.ts`.
 * Example data only (example.org addresses).
 */

export type DemoResponse = { status: number; body?: unknown };

const OWNER = { user_id: 1, name: "Alex Turner" };

const WEEKLY: Recurrence = { kind: "weekly", time: "07:30", weekdays: [1], workdays_only: false };
const MONTHLY: Recurrence = { kind: "monthly", time: "06:00", day_of_month: 1, workdays_only: false };
const DAILY: Recurrence = { kind: "daily", time: "08:00", workdays_only: true };

const RECURRENCE_TEXT: Record<string, string> = {
  weekly: "Jeden Montag um 07:30 Uhr",
  monthly: "Am 1. jedes Monats um 06:00 Uhr",
  daily: "Werktags um 08:00 Uhr",
  yearly: "Jährlich",
};

function seedSchedules(): ReportSchedule[] {
  return [
    {
      id: 1,
      name: "Wochenübersicht Support",
      active: true,
      body: "Anbei die Wochenübersicht.",
      subject: "Wochenübersicht {report}",
      format: "pdf",
      language: "de",
      only_if_nonempty: false,
      time_zone: "Europe/Berlin",
      recurrence: WEEKLY,
      recurrence_text: RECURRENCE_TEXT.weekly,
      next_run_at: "2026-10-12T05:30:00Z",
      last_run_at: "2026-10-05T05:30:00Z",
      last_status: "succeeded",
      failure_count: 0,
      paused_reason: null,
      is_owner: true,
      can_edit: true,
      owner: OWNER,
      version: 3,
      changed_at: "2026-10-01T09:00:00Z",
      items: [
        { report_key: "builtin.volume", title: null, title_key: "reports.builtin.volume.title", available: true, problem: null, overrides: {} },
      ],
      recipients: [
        { kind: "user", id: 1, email: null, label: "Alex Turner", problem: null },
        { kind: "group", id: 2, email: null, label: "Support", problem: null },
      ],
    },
    {
      id: 2,
      name: "SLA-Bericht Incidents",
      active: false,
      body: "",
      subject: "",
      format: "pdf",
      language: "de",
      only_if_nonempty: true,
      time_zone: "Europe/Berlin",
      recurrence: DAILY,
      recurrence_text: RECURRENCE_TEXT.daily,
      next_run_at: null,
      last_run_at: "2026-10-09T06:00:00Z",
      last_status: "failed",
      failure_count: 3,
      paused_reason: "Angehalten nach 3 fehlgeschlagenen Versandversuchen in Folge.",
      is_owner: true,
      can_edit: true,
      owner: OWNER,
      version: 5,
      changed_at: "2026-10-09T06:05:00Z",
      items: [
        { report_key: "builtin.sla", title: null, title_key: "reports.builtin.sla.title", available: true, problem: null, overrides: {} },
      ],
      recipients: [{ kind: "email", id: null, email: "teamleitung@example.org", label: "teamleitung@example.org", problem: null }],
    },
    {
      id: 3,
      name: "Monatspaket Geschäftsführung",
      active: true,
      body: "Die Berichte des Vormonats.",
      subject: "Monatsbericht",
      format: "xlsx",
      language: "de",
      only_if_nonempty: false,
      time_zone: "Europe/Berlin",
      recurrence: MONTHLY,
      recurrence_text: RECURRENCE_TEXT.monthly,
      next_run_at: "2026-11-01T05:00:00Z",
      last_run_at: "2026-10-01T05:00:00Z",
      last_status: "partial",
      failure_count: 0,
      paused_reason: null,
      is_owner: true,
      can_edit: true,
      owner: OWNER,
      version: 1,
      changed_at: "2026-09-15T10:00:00Z",
      items: [
        { report_key: "builtin.volume", title: null, title_key: "reports.builtin.volume.title", available: true, problem: null, overrides: {} },
        { report_key: "builtin.sla", title: null, title_key: "reports.builtin.sla.title", available: true, problem: null, overrides: {} },
        { report_key: "41", title: "Alte Auswertung", title_key: null, available: false, problem: "Nicht mehr verfügbar.", overrides: {} },
      ],
      recipients: [
        { kind: "email", id: null, email: "gf@example.org", label: "gf@example.org", problem: null },
        { kind: "email", id: null, email: "controlling@example.org", label: "controlling@example.org", problem: null },
      ],
    },
  ];
}

function seedRuns(): ReportRun[] {
  const base = { schedule_id: 1, schedule_name: "Wochenübersicht Support", owner_only: false, run_kind: "schedule" as const, job_id: null };
  return [
    {
      ...base, id: 14, status: "succeeded", created_at: "2026-10-05T05:30:00Z", due_at: "2026-10-05T05:30:00Z",
      started_at: "2026-10-05T05:30:02Z", finished_at: "2026-10-05T05:30:09Z", detail: "Versendet.", error: null,
      recipient_count: 4, row_count: 126,
      files: [{ id: 31, name: "wochenuebersicht.pdf", content_type: "application/pdf", size_bytes: 48213 }],
    },
    {
      ...base, id: 13, schedule_id: 3, schedule_name: "Monatspaket Geschäftsführung", status: "partial",
      created_at: "2026-10-01T05:00:00Z", due_at: "2026-10-01T05:00:00Z", started_at: "2026-10-01T05:00:01Z",
      finished_at: "2026-10-01T05:00:14Z", detail: "Ein Bericht war nicht mehr verfügbar und wurde ausgelassen.", error: null,
      recipient_count: 2, row_count: 88,
      files: [{ id: 30, name: "monatspaket.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size_bytes: 21804 }],
    },
    {
      ...base, id: 12, schedule_id: 2, schedule_name: "SLA-Bericht Incidents", status: "failed",
      created_at: "2026-10-09T06:00:00Z", due_at: "2026-10-09T06:00:00Z", started_at: "2026-10-09T06:00:01Z",
      finished_at: "2026-10-09T06:00:04Z", detail: null, error: "Der E-Mail-Versand ist fehlgeschlagen.",
      recipient_count: 1, row_count: null, files: [],
    },
    {
      ...base, id: 15, status: "running", run_kind: "manual", owner_only: true, job_id: 9301,
      created_at: "2026-10-11T08:00:00Z", due_at: null, started_at: "2026-10-11T08:00:01Z", finished_at: null,
      detail: null, error: null, recipient_count: null, row_count: null, files: [],
    },
  ];
}

const ISSUE_DROPPED: ImportIssue = {
  element: "Restrictions",
  value: "StateIDs",
  reason: "Nicht übernommen: Wird in Tiqora nicht unterstützt.",
  severity: "dropped",
};
const ISSUE_APPROX: ImportIssue = {
  element: "TimeRelativeUnit",
  value: "Last 7 days",
  reason: "Angepasst: Der Zeitraum wurde auf 7 Tage gerundet.",
  severity: "approximated",
};
const ISSUE_RUNTIME: ImportIssue = {
  element: "OrderBy",
  value: "Fixed=0",
  reason: "In Znuny zur Laufzeit änderbar – in der gespeicherten Suche fest eingestellt.",
  severity: "approximated",
};

function stat(id: number, over: Partial<StatPreview>): StatPreview {
  return {
    stat_id: id,
    stat_number: String(10000 + id),
    import_key: `znuny:stat:${id}`,
    title: `Statistik ${id}`,
    description: "",
    object_label: "Ticketliste",
    target: "report",
    definition: null,
    already_imported: null,
    existing_id: null,
    issues: [],
    ...over,
  };
}

export const DEMO_ZNUNY_STATS: StatPreview[] = [
  stat(1, { title: "Neue Tickets pro Queue", object_label: "Matrix", target: "report" }),
  stat(2, { title: "Offene Tickets Support", object_label: "Ticketliste", target: "saved_search" }),
  stat(3, { title: "Antwortzeiten (Spezial)", object_label: "Dynamisches Objekt", target: "none", issues: [ISSUE_DROPPED] }),
  stat(4, { title: "Tickets nach Status", target: "report", already_imported: "report", existing_id: 7 }),
  stat(5, { title: "Lösungszeit der letzten Woche", target: "report", issues: [ISSUE_APPROX, ISSUE_RUNTIME, ISSUE_DROPPED] }),
];

export function demoZnunyPreview(): ZnunyStatsPreview {
  return { stats: DEMO_ZNUNY_STATS, errors: [], truncated: false };
}

export function demoZnunyApply(statIds: number[]): ZnunyStatsApplyOut {
  const imported: ZnunyStatsApplyOut["imported"] = [];
  const skipped: StatPreview[] = [];
  for (const s of DEMO_ZNUNY_STATS) {
    if (s.stat_id == null || !statIds.includes(s.stat_id)) continue;
    if (s.target === "none") {
      skipped.push(s);
      continue;
    }
    imported.push({
      id: s.existing_id ?? 100 + s.stat_id,
      import_key: s.import_key,
      title: s.title,
      target: s.target,
      action: s.already_imported ? "updated" : "created",
      issues: s.issues,
    });
  }
  return { imported, skipped };
}

export interface DemoSchedules {
  list(all: boolean): ReportScheduleListItem[];
  get(id: number): ReportSchedule | undefined;
  create(body: ReportScheduleIn): ReportSchedule;
  update(id: number, body: ReportScheduleUpdateIn): DemoResponse;
  remove(id: number): boolean;
  runs(id: number): ReportRun[];
  runNow(id: number, ownerOnly: boolean): { run_id: number; job: { job_id: number; status: "queued" } } | undefined;
}

function toListItem(s: ReportSchedule): ReportScheduleListItem {
  return {
    id: s.id,
    name: s.name,
    active: s.active,
    format: s.format,
    items: s.items.map((i) => ({ report_key: i.report_key, title: i.title, title_key: i.title_key })),
    recurrence_text: s.recurrence_text,
    next_run_at: s.next_run_at,
    last_run_at: s.last_run_at,
    last_status: s.last_status,
    failure_count: s.failure_count,
    paused_reason: s.paused_reason,
    is_owner: s.is_owner,
    can_edit: s.can_edit,
    owner: s.owner,
    version: s.version,
  };
}

export function createDemoSchedules(): DemoSchedules {
  let schedules = seedSchedules();
  let runs = seedRuns();
  let nextId = 4;
  let nextRunId = 16;

  function build(id: number, body: ReportScheduleIn, version: number): ReportSchedule {
    const emails = body.recipients.emails ?? [];
    return {
      id,
      name: body.name,
      active: body.active,
      body: body.body,
      subject: body.subject,
      format: body.format,
      language: body.language,
      only_if_nonempty: body.only_if_nonempty,
      time_zone: body.time_zone ?? "Europe/Berlin",
      recurrence: body.recurrence,
      recurrence_text: RECURRENCE_TEXT[body.recurrence.kind] ?? "",
      next_run_at: body.active ? "2026-10-12T05:30:00Z" : null,
      last_run_at: null,
      last_status: null,
      failure_count: 0,
      paused_reason: null,
      is_owner: true,
      can_edit: true,
      owner: OWNER,
      version,
      changed_at: "2026-10-11T09:00:00Z",
      items: body.items.map((i) => ({
        report_key: i.report_key,
        title: null,
        title_key: i.report_key.startsWith("builtin.") ? `reports.builtin.${i.report_key.slice(8)}.title` : null,
        available: true,
        problem: null,
        overrides: i.overrides ?? {},
      })),
      recipients: [
        ...(body.recipients.user_ids ?? []).map((uid) => ({ kind: "user" as const, id: uid, email: null, label: `Agent #${uid}`, problem: null })),
        ...(body.recipients.group_ids ?? []).map((gid) => ({ kind: "group" as const, id: gid, email: null, label: `Gruppe #${gid}`, problem: null })),
        ...(body.recipients.role_ids ?? []).map((rid) => ({ kind: "role" as const, id: rid, email: null, label: `Rolle #${rid}`, problem: null })),
        ...emails.map((e) => ({ kind: "email" as const, id: null, email: e, label: e, problem: null })),
      ],
    };
  }

  return {
    list: (all) => schedules.filter((s) => all || s.is_owner).map(toListItem),
    get: (id) => schedules.find((s) => s.id === id),
    create(body) {
      const s = build(nextId++, body, 1);
      schedules = [...schedules, s];
      return s;
    },
    update(id, body) {
      const cur = schedules.find((s) => s.id === id);
      if (!cur) return { status: 404, body: { detail: { message: "Der Zeitplan wurde nicht gefunden." } } };
      if (body.version !== cur.version)
        return { status: 409, body: { detail: { message: "Der Zeitplan wurde zwischenzeitlich geändert.", current_version: cur.version } } };
      const next = { ...build(id, body, cur.version + 1), last_run_at: cur.last_run_at, last_status: cur.last_status };
      schedules = schedules.map((s) => (s.id === id ? next : s));
      return { status: 200, body: next };
    },
    remove(id) {
      const had = schedules.some((s) => s.id === id);
      schedules = schedules.filter((s) => s.id !== id);
      runs = runs.filter((r) => r.schedule_id !== id);
      return had;
    },
    runs: (id) => runs.filter((r) => r.schedule_id === id).sort((a, b) => b.id - a.id),
    runNow(id, ownerOnly) {
      const s = schedules.find((x) => x.id === id);
      if (!s) return undefined;
      const run_id = nextRunId++;
      runs = [
        ...runs,
        {
          id: run_id, schedule_id: id, schedule_name: s.name, status: "queued", run_kind: "manual", owner_only: ownerOnly,
          job_id: 9300 + run_id, created_at: "2026-10-11T09:00:00Z", due_at: null, started_at: null, finished_at: null,
          detail: null, error: null, recipient_count: null, row_count: null, files: [],
        },
      ];
      return { run_id, job: { job_id: 9300 + run_id, status: "queued" } };
    },
  };
}

const NOT_FOUND: DemoResponse = { status: 404, body: { detail: { message: "Nicht gefunden." } } };

/** Pure router; `undefined` for paths that are not ours (the next handler answers). */
export function resolveSchedules(
  store: DemoSchedules,
  method: string,
  path: string,
  body?: unknown,
  query?: URLSearchParams,
): DemoResponse | undefined {
  const i = path.indexOf("/api/v1/");
  if (i < 0) return undefined;
  const p = path.slice(i + "/api/v1".length).replace(/\/$/, "");
  const b = (body ?? {}) as Record<string, unknown>;

  if (p === "/reports/import/znuny" && method === "GET") return { status: 200, body: demoZnunyPreview() };
  if (p === "/reports/import/znuny/file" && method === "POST") {
    // Any file previews as the first two demo stats; the content is not parsed in the demo.
    return { status: 200, body: { stats: DEMO_ZNUNY_STATS.slice(0, 2), errors: [], truncated: false } satisfies ZnunyStatsPreview };
  }
  if (p === "/reports/import/znuny/apply" && method === "POST") {
    const ids = Array.isArray(b.stat_ids) ? (b.stat_ids as number[]) : DEMO_ZNUNY_STATS.slice(0, 2).map((s) => s.stat_id as number);
    return { status: 200, body: demoZnunyApply(ids) };
  }

  if (p.startsWith("/report-runs/")) {
    return {
      status: 200,
      body: "PDF demo",
    };
  }
  if (p === "/report-schedules/preview-recurrence" && method === "POST") {
    const rec = b.recurrence as Recurrence;
    return {
      status: 200,
      body: {
        text: RECURRENCE_TEXT[rec?.kind] ?? "",
        next_runs: ["2026-10-12T05:30:00Z", "2026-10-19T05:30:00Z", "2026-10-26T05:30:00Z", "2026-11-02T05:30:00Z", "2026-11-09T05:30:00Z"],
      },
    };
  }
  if (p === "/report-schedules") {
    if (method === "GET") return { status: 200, body: store.list(query?.get("all") === "true") };
    if (method === "POST") return { status: 201, body: store.create(b as unknown as ReportScheduleIn) };
    return undefined;
  }
  const m = /^\/report-schedules\/(\d+)(\/run|\/runs)?$/.exec(p);
  if (!m) return undefined;
  const id = Number(m[1]);
  if (m[2] === "/run" && method === "POST") {
    const out = store.runNow(id, b.owner_only !== false);
    return out ? { status: 202, body: out } : NOT_FOUND;
  }
  if (m[2] === "/runs" && method === "GET") return { status: 200, body: store.runs(id) };
  if (m[2]) return undefined;
  if (method === "GET") {
    const s = store.get(id);
    return s ? { status: 200, body: s } : NOT_FOUND;
  }
  if (method === "PUT") return store.update(id, b as unknown as ReportScheduleUpdateIn);
  if (method === "DELETE") return store.remove(id) ? { status: 204 } : NOT_FOUND;
  return undefined;
}

const demoSchedules = createDemoSchedules();

/** `schedulesMock.resolve(method, path, body, query)` of the MSW handlers (demo store). */
export function resolve(method: string, path: string, body?: unknown, query?: URLSearchParams): DemoResponse | undefined {
  return resolveSchedules(demoSchedules, method, path, body, query);
}
