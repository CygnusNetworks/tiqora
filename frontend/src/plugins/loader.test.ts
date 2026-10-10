import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { helloPlugin } from "@/test/pluginFixtures";
import {
  PLUGIN_IMPORT_TIMEOUT_MS,
  clearPluginModuleCacheForTests,
  loadPluginModule,
  setPluginImporterForTests,
} from "./loader";

const plugin = helloPlugin();

beforeEach(() => {
  clearPluginModuleCacheForTests();
  document.head.querySelectorAll("link").forEach((l) => l.remove());
});

afterEach(() => {
  setPluginImporterForTests(null);
  vi.useRealTimers();
});

describe("loadPluginModule", () => {
  it("imports the entry once and returns its components", async () => {
    const Comp = () => null;
    const importer = vi.fn().mockResolvedValue({ default: { components: { HelloPanel: Comp } } });
    setPluginImporterForTests(importer);
    const [a, b] = await Promise.all([loadPluginModule(plugin), loadPluginModule(plugin)]);
    expect(a.components.HelloPanel).toBe(Comp);
    expect(b).toBe(a);
    expect(await loadPluginModule(plugin)).toBe(a);
    expect(importer).toHaveBeenCalledTimes(1);
    expect(importer).toHaveBeenCalledWith("/api/v1/plugins/org.example.hello/assets/1.0.0/web/index.js");
    expect(
      document.querySelectorAll('link[href="/api/v1/plugins/org.example.hello/assets/1.0.0/web/hello.css"]'),
    ).toHaveLength(1);
  });

  it("loader times out", async () => {
    vi.useFakeTimers();
    setPluginImporterForTests(() => new Promise(() => {}));
    const p = loadPluginModule(plugin);
    vi.advanceTimersByTime(PLUGIN_IMPORT_TIMEOUT_MS + 1);
    await expect(p).rejects.toThrow(/Zeit/);
  });

  it("rejects a module without components", async () => {
    setPluginImporterForTests(() => Promise.resolve({ default: {} }));
    await expect(loadPluginModule(plugin)).rejects.toThrow(/ungültig/);
  });

  it("a failed load is not cached forever", async () => {
    const importer = vi
      .fn()
      .mockRejectedValueOnce(new Error("404"))
      .mockResolvedValue({ default: { components: {} } });
    setPluginImporterForTests(importer);
    await expect(loadPluginModule(plugin)).rejects.toThrow();
    await expect(loadPluginModule(plugin)).resolves.toEqual({ components: {} });
    expect(importer).toHaveBeenCalledTimes(2);
  });

  it("a plugin without a web entry is rejected without importing", async () => {
    const importer = vi.fn();
    setPluginImporterForTests(importer);
    await expect(loadPluginModule(helloPlugin({ entry: null }))).rejects.toThrow(/keinen Programmteil/);
    expect(importer).not.toHaveBeenCalled();
  });

  it.each([
    ["foreign asset base", { asset_base: "https://evil.example.org/api/v1/plugins/x/" }],
    ["protocol-relative asset base", { asset_base: "//evil.example.org/api/v1/plugins/x/" }],
    ["asset base outside the plugin API", { asset_base: "/assets/" }],
    ["asset base climbing out", { asset_base: "/api/v1/plugins/../../evil/" }],
    ["absolute entry", { entry: "/web/index.js" }],
    ["entry with a scheme", { entry: "data:text/javascript,alert(1)" }],
    ["protocol-relative entry", { entry: "web//evil.example.org/x.js" }],
    ["entry climbing out", { entry: "web/../../../evil.js" }],
    ["style climbing out", { styles: ["web/../../x.css"] }],
  ])("refuses a cross-origin or escaping address: %s", async (_label, overrides) => {
    const importer = vi.fn();
    setPluginImporterForTests(importer);
    await expect(loadPluginModule(helloPlugin(overrides))).rejects.toThrow(/ungültige Adresse/);
    expect(importer).not.toHaveBeenCalled();
    expect(document.querySelectorAll("link")).toHaveLength(0);
  });
});
