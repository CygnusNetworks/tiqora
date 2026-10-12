import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { processDesignerApi } from "./processDesignerApi";

afterEach(() => vi.restoreAllMocks());

const BASE = "/api/v1/admin/process-designer";

describe("processDesignerApi", () => {
  it("uses the documented paths and methods", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await processDesignerApi.catalog();
    await processDesignerApi.listProcesses();
    await processDesignerApi.listEntities("Activity");
    await processDesignerApi.getProcess("Process-a/b");
    await processDesignerApi.createProcess({ name: "x", description: "" });
    await processDesignerApi.copyProcess("Process-1");
    await processDesignerApi.deleteProcess("Process-1");
    await processDesignerApi.createEntity("Transition", { name: "t", config: {} });
    await processDesignerApi.updateEntity("ActivityDialog", "AD-1", { name: "d", config: {}, expected_version: "v1" });
    await processDesignerApi.deleteEntity("TransitionAction", "TA-1");
    await processDesignerApi.validation("Process-1");
    await processDesignerApi.importFile("a: 1", true);
    await processDesignerApi.revisions();
    await processDesignerApi.revisionDiff(7, { against: 0, process: "Process-1" });
    await processDesignerApi.restoreRevision(7, "Process-1");
    expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ["GET", `${BASE}/catalog`],
      ["GET", `${BASE}/processes`],
      ["GET", `${BASE}/entities/Activity`],
      ["GET", `${BASE}/processes/Process-a%2Fb`],
      ["POST", `${BASE}/processes`],
      ["POST", `${BASE}/processes/Process-1/copy`],
      ["DELETE", `${BASE}/processes/Process-1`],
      ["POST", `${BASE}/entities/Transition`],
      ["PUT", `${BASE}/entities/ActivityDialog/AD-1`],
      ["DELETE", `${BASE}/entities/TransitionAction/TA-1`],
      ["GET", `${BASE}/processes/Process-1/validation`],
      ["POST", `${BASE}/import`],
      ["GET", `${BASE}/revisions`],
      ["GET", `${BASE}/revisions/7/diff`],
      ["POST", `${BASE}/revisions/7/restore`],
    ]);
    expect(spy.mock.calls[11][2]).toMatchObject({ body: { content: "a: 1", overwrite_existing: true } });
    expect(spy.mock.calls[13][2]).toMatchObject({ query: { against: 0, process: "Process-1" } });
    expect(spy.mock.calls[14][2]).toMatchObject({ body: { process_entity_id: "Process-1" } });
  });

  it("builds a plain download url for the export", () => {
    expect(processDesignerApi.exportUrl("Process-1")).toBe(`${BASE}/processes/Process-1/export`);
  });
});
