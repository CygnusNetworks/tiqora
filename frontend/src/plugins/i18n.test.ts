import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import i18n from "@/i18n";
import { helloPlugin } from "@/test/pluginFixtures";
import { ensurePluginI18n, pluginNamespace } from "./i18n";
import { PLUGIN_IMPORT_TIMEOUT_MS } from "./loader";

const plugin = helloPlugin();
let fetchMock: Mock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

describe("plugin translations", () => {
  it("uses the namespace plugin.<id>", () => {
    expect(pluginNamespace("org.example.hello")).toBe("plugin.org.example.hello");
  });

  it("loads the language file into the plugin namespace", async () => {
    fetchMock.mockResolvedValueOnce(json({ panel: { title: "Hallo" } }));
    await ensurePluginI18n(plugin, "de");
    expect(i18n.getResource("de", "plugin.org.example.hello", "panel.title")).toBe("Hallo");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plugins/org.example.hello/assets/1.0.0/i18n/de.json",
      expect.anything(),
    );
    expect(i18n.t("plugin.org.example.hello:panel.title", { lng: "de" })).toBe("Hallo");
  });

  it("falls back to English, then to nothing, without throwing", async () => {
    const p = helloPlugin({ id: "org.example.fallback", locales: ["en"] });
    fetchMock.mockResolvedValueOnce(json({ panel: { title: "Hello" } }));
    await ensurePluginI18n(p, "fr");
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/i18n\/en\.json$/), expect.anything());
    expect(i18n.getResource("en", "plugin.org.example.fallback", "panel.title")).toBe("Hello");
    fetchMock.mockClear();
    await expect(ensurePluginI18n(helloPlugin({ locales: [] }), "fr")).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("prefers the language without region before English, and pt_BR users see the pt texts", async () => {
    const p = helloPlugin({ id: "org.example.region", locales: ["pt", "en"] });
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(json(url.endsWith("/pt.json") ? { a: "Olá" } : { a: "Hello", b: "Only English" })),
    );
    await ensurePluginI18n(p, "pt_BR");
    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls).toEqual([
      "/api/v1/plugins/org.example.region/assets/1.0.0/i18n/pt.json",
      "/api/v1/plugins/org.example.region/assets/1.0.0/i18n/en.json",
    ]);
    // The app's i18next (load: "currentOnly", fallbackLng: "en") resolves pt_BR → en.
    expect(i18n.t("plugin.org.example.region:a", { lng: "pt_BR" })).toBe("Olá");
    expect(i18n.t("plugin.org.example.region:b", { lng: "pt_BR" })).toBe("Only English");
    expect(i18n.t("plugin.org.example.region:a", { lng: "pt" })).toBe("Olá");
  });

  it("a translation file that never answers is aborted after the import timeout", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    let signal: AbortSignal | undefined;
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_, reject) => {
          signal = init.signal ?? undefined;
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const p = helloPlugin({ id: "org.example.stall", locales: ["de"] });
    let settled = false;
    const done = ensurePluginI18n(p, "de").then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(PLUGIN_IMPORT_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    await done;
    expect(signal?.aborted).toBe(true);
    expect(i18n.hasResourceBundle("de", "plugin.org.example.stall")).toBe(false);
    vi.useRealTimers();
  });

  it("loads each file once and never rejects on HTTP or network errors", async () => {
    const p = helloPlugin({ id: "org.example.once", locales: ["de"] });
    fetchMock.mockResolvedValue(json({ a: "x" }));
    await ensurePluginI18n(p, "de");
    await ensurePluginI18n(p, "de");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    vi.spyOn(console, "warn").mockImplementation(() => {});
    const broken = helloPlugin({ id: "org.example.broken", locales: ["de"] });
    fetchMock.mockResolvedValueOnce(json({ detail: "nope" }, 404));
    await expect(ensurePluginI18n(broken, "de")).resolves.toBeUndefined();
    fetchMock.mockRejectedValueOnce(new TypeError("network"));
    await expect(ensurePluginI18n(broken, "de")).resolves.toBeUndefined();
    expect(i18n.hasResourceBundle("de", "plugin.org.example.broken")).toBe(false);
  });

  it("does not fetch from an address outside the plugin assets", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const evil = helloPlugin({ id: "org.example.evil", asset_base: "https://evil.example.org/" });
    await expect(ensurePluginI18n(evil, "de")).resolves.toBeUndefined();
    await expect(ensurePluginI18n(helloPlugin({ id: "org.example.evil2", locales: ["../x"] }), "../x")).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
