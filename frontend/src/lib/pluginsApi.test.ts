import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { pluginsApi } from "./pluginsApi";
import { catalogEntry, packageReport, repository, restartStatus } from "@/test/pluginFixtures";

afterEach(() => vi.restoreAllMocks());

describe("pluginsApi install wrappers", () => {
  it("uploads a package as multipart form data", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue(packageReport());
    await pluginsApi.uploadPackage(new File(["x"], "a.tqplugin"));
    const [method, path, opts] = spy.mock.calls[0];
    expect([method, path]).toEqual(["POST", "/api/v1/admin/plugins/packages"]);
    expect(opts?.body).toBeInstanceOf(FormData);
    expect(((opts?.body as FormData).get("file") as File).name).toBe("a.tqplugin");
  });

  it("encodes plugin ids and package hashes in the path", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({ job_id: 1, status: "queued" });
    await pluginsApi.uninstall("org.example.hello", { delete_data: true, restart: false });
    await pluginsApi.installPackage("ab/cd", { enable: true, restart: true });
    expect(spy.mock.calls[0].slice(0, 2)).toEqual(["POST", "/api/v1/admin/plugins/org.example.hello/uninstall"]);
    expect(spy.mock.calls[0][2]?.body).toEqual({ delete_data: true, restart: false });
    expect(spy.mock.calls[1].slice(0, 2)).toEqual(["POST", "/api/v1/admin/plugins/packages/ab%2Fcd/install"]);
  });

  it("sends catalog filters as query and omits empty ones", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue({ categories: [], entries: [catalogEntry()] });
    await pluginsApi.catalog({ q: "tab le", category: "crm", repositoryId: 2 });
    await pluginsApi.catalog({});
    expect(spy.mock.calls[0][1]).toBe(
      "/api/v1/admin/plugin-repositories/catalog?q=tab+le&category=crm&repository_id=2",
    );
    expect(spy.mock.calls[1][1]).toBe("/api/v1/admin/plugin-repositories/catalog");
  });

  it("addresses repositories by id and passes the download body", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue(repository());
    await pluginsApi.updateRepository(7, { name: "n", url: "https://x.example.org/i.json", enabled: false });
    await pluginsApi.refreshRepository(7);
    await pluginsApi.download(7, { plugin_id: "org.example.tables", version: "1.1.0" });
    expect(spy.mock.calls.map((c) => c.slice(0, 2))).toEqual([
      ["PUT", "/api/v1/admin/plugin-repositories/7"],
      ["POST", "/api/v1/admin/plugin-repositories/7/refresh"],
      ["POST", "/api/v1/admin/plugin-repositories/7/download"],
    ]);
    expect(spy.mock.calls[2][2]?.body).toEqual({ plugin_id: "org.example.tables", version: "1.1.0" });
  });

  it("requests a restart with an optional reason", async () => {
    const spy = vi.spyOn(api, "request").mockResolvedValue(restartStatus());
    await pluginsApi.requestRestart();
    await pluginsApi.requestRestart("Update");
    expect(spy.mock.calls[0][2]?.body).toEqual({ reason: null });
    expect(spy.mock.calls[1][2]?.body).toEqual({ reason: "Update" });
  });
});

describe("demo data for installing plugins", () => {
  it("answers the install endpoints and the finished job", async () => {
    const { resolveData } = await import("@/demo/mockData");
    const list = resolveData("/api/v1/admin/plugins", "GET") as { plugins: { id: string; source: string; update: unknown }[] };
    expect(list.plugins.some((p) => p.source === "image")).toBe(true);
    expect(list.plugins.find((p) => p.id === "org.example.tables")?.update).toMatchObject({ version: "1.1.0" });
    expect(resolveData("/api/v1/admin/plugins/restart", "GET")).toMatchObject({ coordinated: true });
    expect((resolveData("/api/v1/admin/plugin-repositories", "GET") as unknown[]).length).toBe(2);
    const cat = resolveData("/api/v1/admin/plugin-repositories/catalog", "GET") as { entries: { image_plugin: boolean; update_available: boolean }[] };
    expect(cat.entries).toHaveLength(3);
    expect(cat.entries.some((e) => e.image_plugin)).toBe(true);
    expect(cat.entries.some((e) => e.update_available)).toBe(true);
    expect(resolveData("/api/v1/admin/plugins/packages", "POST")).toMatchObject({ trust: "unsigned" });
    for (const [path] of [
      ["/api/v1/admin/plugins/packages/abc/install"],
      ["/api/v1/admin/plugins/org.example.tables/uninstall"],
      ["/api/v1/admin/plugin-repositories/1/refresh"],
      ["/api/v1/admin/plugin-repositories/1/download"],
    ])
      expect(resolveData(path, "POST")).toEqual({ job_id: 990001, status: "queued" });
    expect(resolveData("/api/v1/jobs/990001", "GET")).toMatchObject({ id: 990001, status: "succeeded" });
  });
});
