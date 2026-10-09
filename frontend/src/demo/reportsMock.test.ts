import { describe, it, expect } from "vitest";
import { DEMO_REPORTS } from "./reportsCatalogue";
import { resolveData, resolveReportRun } from "./mockData";
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
