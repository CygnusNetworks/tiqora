import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { isSavedKey, reportDescription, reportTitle, reportsApi } from "./reportsApi";

const DEF = { schema_version: 1, source: "tickets", dimensions: [], measures: [{ key: "tickets" }] };

afterEach(() => vi.restoreAllMocks());

describe("reportsApi saved-report routes", () => {
  it("calls the saved-report routes", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await reportsApi.create({ name: "x", definition: DEF } as never);
    await reportsApi.update(12, { name: "x", definition: DEF, version: 3 } as never);
    await reportsApi.remove(12);
    await reportsApi.importFile("{}");
    await reportsApi.catalogue(undefined, { all: true });
    await reportsApi.shareGroups();
    expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ["POST", "/api/v1/reports"],
      ["PUT", "/api/v1/reports/12"],
      ["DELETE", "/api/v1/reports/12"],
      ["POST", "/api/v1/reports/import-file"],
      ["GET", "/api/v1/reports"],
      ["GET", "/api/v1/reference/share-groups"],
    ]);
    expect(spy.mock.calls[1][2]).toMatchObject({ body: { version: 3 } });
    expect(spy.mock.calls[3][2]).toMatchObject({ body: { content: "{}" } });
    expect(spy.mock.calls[4][2]).toMatchObject({ query: { all: true } });
  });

  it("sends no query for the plain catalogue", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
    await reportsApi.catalogue();
    expect(spy.mock.calls[0][2]?.query).toBeUndefined();
  });

  it("builds file urls and recognises saved keys", () => {
    expect(reportsApi.fileUrl("builtin.volume", "yaml")).toBe("/api/v1/reports/builtin.volume/file?format=yaml");
    expect(reportsApi.fileUrl("a/b c", "json")).toBe("/api/v1/reports/a%2Fb%20c/file?format=json");
    expect(isSavedKey("12")).toBe(true);
    expect(isSavedKey("builtin.volume")).toBe(false);
    expect(isSavedKey("012")).toBe(false);
    expect(isSavedKey("1".repeat(19))).toBe(false);
  });

  it("titles saved reports by name and built-ins by key", () => {
    const t = ((k: string) => `T:${k}`) as never;
    expect(reportTitle({ kind: "saved", name: "Meins" } as never, t)).toBe("Meins");
    expect(reportTitle({ kind: "builtin", title_key: "reports.builtin.volume.title" } as never, t)).toBe(
      "T:reports.builtin.volume.title",
    );
    expect(reportDescription({ kind: "saved", description: "Text" } as never, t)).toBe("Text");
    expect(reportDescription({ kind: "builtin", description_key: "d.key" } as never, t)).toBe("T:d.key");
  });
});
