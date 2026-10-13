import { beforeEach, describe, expect, it } from "vitest";
import { resolveData } from "./mockData";
import { DEMO_ITSM_IMPORT_JOB_ID, resetItsmImportMock, resolveItsmImport } from "./itsmImportMock";
import type { ImportRunOut, ImportStatus } from "@/lib/itsmImportApi";
import type { Job } from "@/lib/jobsApi";

describe("itsm import demo mock", () => {
  beforeEach(() => resetItsmImportMock());

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

  it("switches to Tiqora with drift 3 and back", () => {
    expect(resolveData("/api/v1/admin/itsm/import/switch", "POST")).toEqual({
      job_id: DEMO_ITSM_IMPORT_JOB_ID,
      status: "queued",
    });
    const s = resolveData("/api/v1/admin/itsm/import/status", "GET") as ImportStatus;
    expect(s).toMatchObject({ mode: "tiqora", znuny_changes_since_switch: 3 });
    expect(s.switched?.switched_at).toBeTruthy();
    const back = resolveData("/api/v1/admin/itsm/import/rollback", "POST") as ImportStatus;
    expect(back.mode).toBe("znuny_readthrough");
    expect((resolveData("/api/v1/admin/itsm/import/status", "GET") as ImportStatus).mode).toBe("znuny_readthrough");
  });

  it("links the CSV report of the run and shows the switch run with its own counts", () => {
    const runs = resolveData("/api/v1/admin/itsm/import/runs", "GET") as ImportRunOut[];
    expect(runs[0]!.job_id).toBe(DEMO_ITSM_IMPORT_JOB_ID);
    const job = resolveData(`/api/v1/jobs/${DEMO_ITSM_IMPORT_JOB_ID}`, "GET") as Job;
    expect(job.files.map((f) => f.name)).toEqual(["itsm-import-bericht.csv"]);
    resolveData("/api/v1/admin/itsm/import/switch", "POST");
    const [switchRun, dryRun] = resolveData("/api/v1/admin/itsm/import/runs", "GET") as ImportRunOut[];
    expect(switchRun!.kind).toBe("switch");
    expect(switchRun!.steps.cis_pass1).not.toEqual(dryRun!.steps.cis_pass1);
  });

  it("ignores foreign paths", () => {
    expect(resolveItsmImport("/api/v1/itsm/status", "GET")).toBeUndefined();
  });
});
