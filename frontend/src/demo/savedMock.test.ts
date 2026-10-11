import { describe, expect, it } from "vitest";
import { resolveData, resolveReportRun } from "./mockData";
import { demoReportFile, demoSavedReports, demoSavedSearches, demoZnunyPreview, resolveSavedRequest } from "./savedMock";
import type { ReportDefinition } from "@/lib/reportsApi";

const DEF = {
  schema_version: 1,
  source: "tickets",
  dimensions: [{ key: "queue" }],
  measures: [{ key: "tickets" }],
} as unknown as ReportDefinition;

describe("saved demo store", () => {
  it("round-trips a saved report and a saved search", () => {
    const made = resolveSavedRequest("POST", "/api/v1/reports", { name: "Demo", definition: DEF, visibility: "private", group_ids: [] });
    expect(made).toMatchObject({ kind: "saved", name: "Demo", version: 1 });
    const listed = resolveSavedRequest("GET", "/api/v1/reports", undefined) as { reports: { key: string }[] };
    expect(listed.reports.some((r) => r.key === String((made as { id: number }).id))).toBe(true);
    const search = resolveSavedRequest("POST", "/api/v1/saved-searches", { name: "S", criteria: { version: 1 } });
    expect(resolveSavedRequest("GET", "/api/v1/saved-searches/counts", undefined)).toMatchObject({ counts: expect.any(Object) });
    expect(search).toMatchObject({ name: "S", is_owner: true });
  });

  it("updates with a version bump and deletes", () => {
    const made = resolveSavedRequest("POST", "/api/v1/reports", { name: "A", definition: DEF, visibility: "private" }) as { id: number };
    const put = resolveSavedRequest("PUT", `/api/v1/reports/${made.id}`, { name: "B", definition: DEF, visibility: "all", version: 1 });
    expect(put).toMatchObject({ name: "B", version: 2, visibility: "all" });
    expect(resolveSavedRequest("DELETE", `/api/v1/reports/${made.id}`, undefined)).toBeNull();
    expect(resolveSavedRequest("GET", `/api/v1/reports/${made.id}`, undefined)).toBeUndefined();
    expect(resolveSavedRequest("PUT", "/api/v1/reports/987654", { name: "B", definition: DEF, visibility: "all", version: 1 })).toBeUndefined();
  });

  it("serves demo fixtures: a private and a shared report, three searches, a Znuny preview", () => {
    const reports = demoSavedReports();
    expect(reports.map((r) => [r.name, r.visibility, r.owner?.user_id])).toEqual([
      ["Meine offenen Tickets je Queue", "private", 1],
      ["Team-SLA je Kunde", "groups", 2],
    ]);
    const searches = demoSavedSearches();
    expect(searches).toHaveLength(3);
    expect(searches.some((s) => !s.is_owner && s.show_count)).toBe(true);
    const profiles = demoZnunyPreview().profiles;
    expect(profiles.map((p) => [p.partial, p.unmapped.length > 0])).toEqual([[false, false], [true, true]]);
  });

  it("applies a Znuny import once and updates on the second apply", () => {
    const first = resolveSavedRequest("POST", "/api/v1/saved-searches/import/znuny", { names: ["Offen in Support", "Nope"] }) as {
      created: number[];
      updated: number[];
      skipped: { name: string }[];
    };
    expect(first.created).toHaveLength(1);
    expect(first.skipped.map((s) => s.name)).toEqual(["Nope"]);
    const second = resolveSavedRequest("POST", "/api/v1/saved-searches/import/znuny", { names: ["Offen in Support"] }) as typeof first;
    expect(second.updated).toEqual(first.created);
  });

  it("stores prefs per search and answers unknown ids with undefined", () => {
    const prefs = resolveSavedRequest("PUT", "/api/v1/saved-searches/13/prefs", { show_in_sidebar: true, show_count: true });
    expect(prefs).toMatchObject({ id: 13, show_in_sidebar: true, show_count: true });
    expect(resolveSavedRequest("PUT", "/api/v1/saved-searches/999/prefs", { show_in_sidebar: true, show_count: true })).toBeUndefined();
  });

  it("pages /tickets/query through the passed ticket list", () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ id: i }));
    expect(resolveSavedRequest("POST", "/api/v1/tickets/query", { filter: { version: 1 }, offset: 2, limit: 2 }, { items })).toEqual({
      items: [{ id: 2 }, { id: 3 }],
      total: 5,
      offset: 2,
      limit: 2,
    });
  });

  it("encodes keys in paths and ignores other routes", () => {
    expect(resolveSavedRequest("GET", "/api/v1/reports/builtin%2Evolume", undefined)).toBeDefined();
    expect(resolveSavedRequest("GET", "/api/v1/tickets", undefined)).toBeUndefined();
  });

  it("resolveData answers saved GETs but never mutates on writes", () => {
    const before = (resolveData("/api/v1/saved-searches", "GET") as unknown[]).length;
    resolveData("/api/v1/saved-searches", "POST");
    resolveData("/api/v1/reports", "POST");
    expect((resolveData("/api/v1/saved-searches", "GET") as unknown[]).length).toBe(before);
    const cat = resolveData("/api/v1/reports", "GET") as { reports: { key: string }[] };
    expect(cat.reports.some((r) => r.key === "102")).toBe(true);
  });

  it("runs a saved report with its own definition", () => {
    const out = resolveReportRun({ report_key: "102" } as never);
    expect(out.result?.columns.map((c) => c.key)).toEqual(["customer", "tickets", "sla_met_rate", "response_breach_rate"]);
    expect(out.result?.report_key).toBe("102");
  });

  it("builds JSON and YAML files for built-in and saved reports", () => {
    const json = demoReportFile("102", "json");
    expect(JSON.parse(json!.body)).toMatchObject({ format: "tiqora-report", name: "Team-SLA je Kunde" });
    expect(json!.filename).toBe("team-sla-je-kunde.json");
    const yaml = demoReportFile("builtin.volume", "yaml");
    expect(yaml!.body).toContain("format: tiqora-report");
    expect(yaml!.body).toContain("definition:");
    expect(demoReportFile("nope", "json")).toBeUndefined();
  });
});
