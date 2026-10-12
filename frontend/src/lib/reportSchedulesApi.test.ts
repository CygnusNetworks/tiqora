import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { reportSchedulesApi } from "./reportSchedulesApi";

afterEach(() => vi.restoreAllMocks());

describe("reportSchedulesApi", () => {
  it("lists with all=true only when asked", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue([] as never);
    await reportSchedulesApi.list();
    await reportSchedulesApi.list(true);
    expect(spy.mock.calls.map((c) => [c[0], c[1], c[2]?.query])).toEqual([
      ["GET", "/api/v1/report-schedules", undefined],
      ["GET", "/api/v1/report-schedules", { all: true }],
    ]);
  });

  it("runNow posts owner_only", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({ run_id: 7, job: { job_id: 9, status: "queued" } } as never);
    await reportSchedulesApi.runNow(3, false);
    await reportSchedulesApi.runNow(3);
    expect(spy.mock.calls[0].slice(0, 2)).toEqual(["POST", "/api/v1/report-schedules/3/run"]);
    expect(spy.mock.calls[0][2]).toMatchObject({ body: { owner_only: false } });
    expect(spy.mock.calls[1][2]).toMatchObject({ body: { owner_only: true } });
  });

  it("calls the CRUD, runs and preview routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    const rec = { kind: "daily", time: "07:00", workdays_only: true } as never;
    await reportSchedulesApi.get(4);
    await reportSchedulesApi.create({ name: "x" } as never);
    await reportSchedulesApi.update(4, { name: "x", version: 2 } as never);
    await reportSchedulesApi.remove(4);
    await reportSchedulesApi.runs(4, 50);
    await reportSchedulesApi.previewRecurrence(rec, "Europe/Berlin");
    expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ["GET", "/api/v1/report-schedules/4"],
      ["POST", "/api/v1/report-schedules"],
      ["PUT", "/api/v1/report-schedules/4"],
      ["DELETE", "/api/v1/report-schedules/4"],
      ["GET", "/api/v1/report-schedules/4/runs"],
      ["POST", "/api/v1/report-schedules/preview-recurrence"],
    ]);
    expect(spy.mock.calls[2][2]).toMatchObject({ body: { version: 2 } });
    expect(spy.mock.calls[4][2]?.query).toEqual({ limit: 50 });
    expect(spy.mock.calls[5][2]).toMatchObject({ body: { recurrence: rec, time_zone: "Europe/Berlin" } });
  });

  it("builds the run file URL", () => {
    expect(reportSchedulesApi.fileUrl(5, 8)).toBe("/api/v1/report-runs/5/files/8");
  });
});
