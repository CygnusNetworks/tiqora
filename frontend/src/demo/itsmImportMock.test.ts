import { describe, expect, it } from "vitest";
import { resolveData } from "./mockData";
import { DEMO_ITSM_IMPORT_JOB_ID, resolveItsmImport } from "./itsmImportMock";
import type { ImportRunOut, ImportStatus } from "@/lib/itsmImportApi";

describe("itsm import demo mock", () => {
  it("answers the status as a Mode A site that may import", () => {
    const s = resolveData("/api/v1/admin/itsm/import/status", "GET") as ImportStatus;
    expect(s).toMatchObject({ mode: "znuny_readthrough", can_import: true, znuny_itsm_present: true });
    expect(s.znuny_counts?.cis).toBeGreaterThan(0);
  });

  it("queues a run and lists it", () => {
    expect(resolveData("/api/v1/admin/itsm/import/runs", "POST")).toEqual({
      job_id: DEMO_ITSM_IMPORT_JOB_ID,
      status: "queued",
    });
    const runs = resolveData("/api/v1/admin/itsm/import/runs", "GET") as ImportRunOut[];
    expect(runs).toHaveLength(1);
    expect(runs[0]!.steps.cis_pass1!.created).toBeGreaterThan(0);
  });

  it("ignores foreign paths", () => {
    expect(resolveItsmImport("/api/v1/itsm/status", "GET")).toBeUndefined();
  });
});
