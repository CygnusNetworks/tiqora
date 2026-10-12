import { describe, expect, it } from "vitest";
import { createDemoSchedules, DEMO_ZNUNY_STATS, demoZnunyApply, resolveSchedules } from "./schedulesMock";

describe("demo schedules store", () => {
  it("seeds three schedules: active weekly PDF, paused, monthly XLSX bundle", () => {
    const rows = createDemoSchedules().list(false);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ active: true, format: "pdf" });
    expect(rows[1].active).toBe(false);
    expect(rows[1].paused_reason).toBeTruthy();
    expect(rows[2]).toMatchObject({ format: "xlsx" });
    expect(rows[2].items.length).toBeGreaterThan(1);
  });

  it("has runs in all four states", () => {
    const store = createDemoSchedules();
    const statuses = [1, 2, 3].flatMap((id) => store.runs(id).map((r) => r.status));
    expect(new Set(statuses)).toEqual(new Set(["succeeded", "partial", "failed", "running"]));
  });

  it("creates, updates with the version check, and removes", () => {
    const store = createDemoSchedules();
    const body = {
      name: "Neu", items: [{ report_key: "builtin.volume" }], format: "csv", language: "de", active: true,
      only_if_nonempty: false, body: "", subject: "", recipients: { emails: ["x@example.org"] },
      recurrence: { kind: "daily", time: "09:00", workdays_only: false },
    };
    const r = resolveSchedules(store, "POST", "/api/v1/report-schedules", body)!;
    expect(r.status).toBe(201);
    const id = (r.body as { id: number }).id;
    const stale = resolveSchedules(store, "PUT", `/api/v1/report-schedules/${id}`, { ...body, version: 9 })!;
    expect(stale.status).toBe(409);
    const ok = resolveSchedules(store, "PUT", `/api/v1/report-schedules/${id}`, { ...body, name: "Neu2", version: 1 })!;
    expect(ok).toMatchObject({ status: 200, body: { name: "Neu2", version: 2 } });
    expect(resolveSchedules(store, "DELETE", `/api/v1/report-schedules/${id}`)!.status).toBe(204);
    expect(resolveSchedules(store, "GET", `/api/v1/report-schedules/${id}`)!.status).toBe(404);
  });

  it("runNow adds a queued run; unknown paths are not ours", () => {
    const store = createDemoSchedules();
    const out = resolveSchedules(store, "POST", "/api/v1/report-schedules/1/run", { owner_only: true })!;
    expect(out.status).toBe(202);
    expect(store.runs(1)[0]).toMatchObject({ status: "queued", owner_only: true });
    expect(resolveSchedules(store, "GET", "/api/v1/tickets")).toBeUndefined();
  });
});

describe("demo Znuny stats", () => {
  it("covers report, saved search, none, already imported and issues", () => {
    expect(DEMO_ZNUNY_STATS).toHaveLength(5);
    const targets = DEMO_ZNUNY_STATS.map((s) => s.target);
    expect(targets).toContain("report");
    expect(targets).toContain("saved_search");
    expect(targets).toContain("none");
    expect(DEMO_ZNUNY_STATS.some((s) => s.already_imported)).toBe(true);
    expect(DEMO_ZNUNY_STATS.some((s) => s.issues.some((i) => i.severity === "approximated"))).toBe(true);
  });

  it("apply skips unimportable stats and updates already imported ones", () => {
    const out = demoZnunyApply([1, 3, 4]);
    expect(out.imported.map((i) => [i.title, i.action])).toEqual([
      ["Neue Tickets pro Queue", "created"],
      ["Tickets nach Status", "updated"],
    ]);
    expect(out.skipped.map((s) => s.stat_id)).toEqual([3]);
  });
});
