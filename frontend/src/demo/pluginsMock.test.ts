import { describe, expect, it } from "vitest";
import { jobRestartGeneration } from "@/routes/admin/plugins/restartGeneration";
import { resolveData } from "./mockData";

type Job = { status: string; result: Record<string, unknown>; files: unknown[] };
type Restart = { generation: number; finished_at: string | null; failed_message: string | null };

describe("demo mocks for installing plugins", () => {
  const job = resolveData("/api/v1/jobs/990001", "GET") as Job;

  it("lets the download job hand over a package", () => {
    expect(job.status).toBe("succeeded");
    expect(job.result.package_sha256).toMatch(/^[0-9a-f]{64}$/);
    const pkg = resolveData(`/api/v1/admin/plugins/packages/${String(job.result.package_sha256)}`, "GET");
    expect(pkg).toMatchObject({ sha256: job.result.package_sha256 });
  });

  it("finishes the restart the job and the restart button record", () => {
    const generation = jobRestartGeneration(job.result);
    for (const method of ["GET", "POST"]) {
      const status = resolveData("/api/v1/admin/plugins/restart", method) as Restart;
      expect(status.finished_at).not.toBeNull();
      expect(status.failed_message).toBeNull();
      expect(status.generation).toBe(generation);
    }
  });

  it("offers a backup after deleting data", () => {
    expect(job.files.length).toBeGreaterThan(0);
  });
});
