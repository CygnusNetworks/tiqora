import { describe, it, expect } from "vitest";
import { DEMO_REPORTS } from "./reportsCatalogue";
import { resolveData, resolveReportRun } from "./mockData";
import { setDemoFactsBuilding } from "./reportsMock";
import de from "@/i18n/locales/de.json";
import en from "@/i18n/locales/en.json";
import type { ReportRunIn } from "@/lib/reportsApi";

describe("report demo mocks", () => {
  it("serves the 25 built-ins with ready facts", () => {
    const cat = resolveData("/api/v1/reports", "GET") as { reports: unknown[]; facts: { ready: boolean } };
    expect(cat.reports).toHaveLength(25);
    expect(cat.facts.ready).toBe(true);
    expect(resolveData("/api/v1/reports/builtin.volume", "GET")).toMatchObject({ key: "builtin.volume" });
  });

  it("resolves every built-in to a result that matches its definition", () => {
    for (const report of DEMO_REPORTS) {
      const out = resolveReportRun({ report_key: report.key } as ReportRunIn);
      const res = out.result;
      expect(res, report.key).toBeTruthy();
      expect(out.job).toBeNull();
      const dims = report.definition.dimensions ?? [];
      expect(res!.columns.map((c) => c.key), report.key).toEqual([
        ...dims.map((d) => d.key),
        ...report.definition.measures.map((m) => m.key),
      ]);
      expect(res!.rows.length, report.key).toBeGreaterThan(0);
      expect(res!.rows.every((r) => r.length === res!.columns.length), report.key).toBe(true);
      expect(res!.row_count).toBe(res!.rows.length);
      expect(res!.facts.ready).toBe(true);
    }
  });

  it("falls back to the definition's source when there is no report key", () => {
    const out = resolveReportRun({
      definition: { source: "accounted_time", measures: [{ key: "time_units" }], schema_version: 1 },
    } as ReportRunIn);
    expect(out.result?.report_key).toBeNull();
    expect(out.result?.columns.map((c) => c.key)).toContain("time_units");
  });

  it("answers export, status and the export job", () => {
    expect(resolveData("/api/v1/reports/export", "POST")).toEqual({ job_id: 9201, status: "queued" });
    expect(resolveData("/api/v1/reporting/status", "GET")).toMatchObject({ ready: true });
    expect(resolveData("/api/v1/admin/reporting/status", "GET")).toMatchObject({ fact_rows: 1840 });
    expect(resolveData("/api/v1/admin/reporting/backfill", "POST")).toMatchObject({ status: "queued" });
    expect(resolveData("/api/v1/jobs/9201", "GET")).toMatchObject({
      status: "succeeded",
      files: [{ name: "bericht.xlsx" }],
    });
  });
});

describe("report demo runs follow the request", () => {
  const run = (body: Partial<ReportRunIn>) => resolveReportRun(body as ReportRunIn);
  const col = (out: ReturnType<typeof run>, key: string) => out.result!.columns.findIndex((c) => c.key === key);

  it("rebuckets the date dimension by the chosen grain", () => {
    const weeks = run({ report_key: "builtin.volume" }).result!;
    const months = run({ report_key: "builtin.volume", grain: "month" }).result!;
    expect(weeks.columns[0]).toMatchObject({ key: "occurred", grain: "week" });
    expect(months.columns[0]).toMatchObject({ key: "occurred", grain: "month" });
    const distinct = (r: typeof weeks) => new Set(r.rows.map((row) => row[0])).size;
    expect(distinct(months)).toBeLessThan(distinct(weeks));
    expect(months.rows.every((row) => String(row[0]).endsWith("-01"))).toBe(true);
  });

  it("swaps the dimension for the chosen split", () => {
    const out = run({ report_key: "builtin.open_now", dimension_choices: { "0": "owner" } });
    expect(out.result!.columns[0].key).toBe("owner");
    expect(out.result!.labels.owner).toMatchObject({ "2": "Anna Beispiel" });
    expect(run({ report_key: "builtin.open_now" }).result!.columns[0].key).toBe("queue");
  });

  it("covers an absolute range day by day and reports it as the window", () => {
    const out = run({
      report_key: "builtin.backlog_trend",
      time_range: { kind: "absolute", start: "2026-09-01", end: "2026-09-07" },
      time_zone: "Europe/Vienna",
    }).result!;
    expect(out.rows.map((r) => r[0])).toEqual([
      "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07",
    ]);
    expect(out.time_window).toMatchObject({ start_date: "2026-09-01", end_date: "2026-09-07", time_zone: "Europe/Vienna" });
  });

  it("restricts queue splits to the chosen queues and shrinks other counts", () => {
    const byQueue = run({ report_key: "builtin.open_now", parameters: { queues: [2] } }).result!;
    expect(byQueue.rows.map((r) => r[0])).toEqual(["2"]);
    const all = run({ report_key: "builtin.ticket_kpis" }).result!;
    const one = run({ report_key: "builtin.ticket_kpis", parameters: { queues: [1] } }).result!;
    const t = col({ result: all, job: null }, "tickets");
    expect(one.rows[0][t] as number).toBeLessThan(all.rows[0][t] as number);
  });

  it("adds the previous period when asked", () => {
    expect(run({ report_key: "builtin.ticket_kpis" }).result!.previous).toBeNull();
    const out = run({ report_key: "builtin.ticket_kpis", compare_previous: true }).result!;
    expect(out.previous?.grand).toHaveLength(out.columns.length);
    expect(out.previous!.grand!.every((v) => typeof v === "number")).toBe(true);
  });

  it("runs a long range as a background job that succeeds on the second poll", () => {
    const out = run({
      report_key: "builtin.volume",
      time_range: { kind: "absolute", start: "2025-01-01", end: "2025-12-31" },
    });
    expect(out.result).toBeNull();
    const id = out.job!.job_id;
    expect(resolveData(`/api/v1/jobs/${id}`, "GET")).toMatchObject({ status: "running", kind: "core.report_run" });
    const done = resolveData(`/api/v1/jobs/${id}`, "GET") as { status: string; result: { report_key: string; rows: unknown[] } };
    expect(done.status).toBe("succeeded");
    expect(done.result.report_key).toBe("builtin.volume");
    expect(done.result.rows.length).toBeGreaterThan(0);
  });

  it("serves the sources with each measure's aggregation", () => {
    const sources = resolveData("/api/v1/reporting/sources", "GET") as { key: string; measures: { key: string; agg: string }[] }[];
    const agg = (src: string, m: string) => sources.find((s) => s.key === src)?.measures.find((x) => x.key === m)?.agg;
    expect(agg("ticket_events", "events")).toBe("count");
    expect(agg("ticket_events", "tickets")).toBe("count_distinct");
    expect(agg("daily_snapshot", "open_tickets")).toBe("sum");
    expect(agg("tickets", "reopen_rate")).toBe("rate");
    // every measure a built-in uses is described
    for (const r of DEMO_REPORTS) {
      for (const m of r.definition.measures) expect(agg(r.definition.source, m.key), `${r.key} ${m.key}`).toBeTruthy();
    }
  });

  it("switches the catalogue, status and results to 'still building'", () => {
    setDemoFactsBuilding(true);
    try {
      const cat = resolveData("/api/v1/reports", "GET") as { facts: { ready: boolean; backfill_job_id: number } };
      expect(cat.facts.ready).toBe(false);
      expect(resolveData("/api/v1/reporting/status", "GET")).toMatchObject({ ready: false, backfill_progress: 0.42 });
      expect(run({ report_key: "builtin.volume" }).result!.facts.ready).toBe(false);
      // the admin's progress follows a running rebuild job
      expect(resolveData(`/api/v1/jobs/${cat.facts.backfill_job_id}`, "GET")).toMatchObject({
        status: "running",
        kind: "core.report_backfill",
      });
    } finally {
      setDemoFactsBuilding(false);
    }
    expect((resolveData("/api/v1/reports", "GET") as { facts: { ready: boolean } }).facts.ready).toBe(true);
  });
});

function lookup(tree: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>((n, k) => (n as Record<string, unknown> | undefined)?.[k], tree);
}

describe("report texts", () => {
  it("has a title and description for every built-in in German and English", () => {
    for (const report of DEMO_REPORTS) {
      for (const tree of [de, en]) {
        expect(typeof lookup(tree, report.title_key), report.title_key).toBe("string");
        expect(typeof lookup(tree, report.description_key), report.description_key).toBe("string");
      }
    }
  });

  it("names every dimension and measure the built-ins use", () => {
    for (const report of DEMO_REPORTS) {
      for (const d of report.definition.dimensions ?? [])
        expect(lookup(de, `reports.dimensions.${d.key}`), d.key).toBeTruthy();
      for (const m of report.definition.measures)
        expect(lookup(de, `reports.measures.${m.key}`), m.key).toBeTruthy();
    }
  });

  it("explains the closed count of the creation-cohort reports (R-T14-COHORT)", () => {
    for (const name of ["ticket_kpis", "volume_by_queue", "top_customers"]) {
      expect(lookup(de, `reports.builtin.${name}.description`), name).toContain(
        "im Zeitraum erstellt und inzwischen geschlossen",
      );
    }
  });

  it("does not claim a closed column for sla_by_customer, which has none", () => {
    expect(lookup(de, "reports.builtin.sla_by_customer.description")).not.toContain("geschlossen");
  });

  it("says a choice replaces the queue split and defines first-contact resolution", () => {
    for (const name of ["open_now", "volume_by_queue"]) {
      const text = lookup(de, `reports.builtin.${name}.description`) as string;
      expect(text, name).toContain("statt nach Queue");
      expect(text, name).not.toContain("zusätzlich");
    }
    const fcr = lookup(de, "reports.builtin.first_contact_resolution.description") as string;
    expect(fcr).toContain("höchstens eine ausgehende Antwort");
    expect(lookup(de, "reports.builtin.ticket_kpis.description")).toContain("Anteil verletzter Erstreaktionen");
    expect(lookup(de, "reports.builtin.sla_times.description")).toContain("langsamsten 10 %");
  });

  it("explains both halves of first-contact resolution (R-T14-FCR)", () => {
    const text = lookup(de, "reports.builtin.first_contact_resolution.description") as string;
    expect(text).toContain("gerade geschlossen sind");
    expect(text).toContain("im Zeitraum geschlossen wurden");
  });
});
