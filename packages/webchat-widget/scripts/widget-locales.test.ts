// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { extractWidgetMessages, localeOfModule, widgetLocales } from "./widget-locales";

const localesDir = fileURLToPath(new URL("../../../frontend/src/i18n/locales", import.meta.url));
const readLocale = (file: string) =>
  JSON.parse(readFileSync(join(localesDir, file), "utf8")) as Record<string, unknown>;

describe("widget locale extraction", () => {
  it("extracts and flattens only the widget namespace", () => {
    expect(
      extractWidgetMessages({
        webchatWidget: { launcher: { open: "Chat öffnen" }, typing: "schreibt …" },
        other: { a: "b" },
      }),
    ).toEqual({ "launcher.open": "Chat öffnen", typing: "schreibt …" });
    expect(extractWidgetMessages({})).toEqual({});
  });

  it("ignores non-string leaves", () => {
    expect(
      extractWidgetMessages({ webchatWidget: { a: 1, b: null, c: ["x"], d: { e: "ok" } } }),
    ).toEqual({ "d.e": "ok" });
  });

  it("every locale file has the same widget keys as de.json", () => {
    const de = Object.keys(extractWidgetMessages(readLocale("de.json"))).sort();
    expect(de.length).toBeGreaterThan(40);
    const files = readdirSync(localesDir).filter((f) => f.endsWith(".json"));
    expect(files.length).toBeGreaterThan(2);
    for (const file of files) {
      const keys = Object.keys(extractWidgetMessages(readLocale(file))).sort();
      expect(keys, file).toEqual(de);
    }
  });

  it("German widget texts address the visitor with Sie, never du", () => {
    const texts = Object.values(extractWidgetMessages(readLocale("de.json")));
    for (const text of texts) {
      expect(text).not.toMatch(/\b(du|dich|dir|dein\w*)\b/i);
    }
  });
});

describe("widgetLocales plugin", () => {
  type Ctx = { addWatchFile: (f: string) => void; error: (m: string) => never };
  const ctx: Ctx = {
    addWatchFile: () => undefined,
    error: (m: string) => {
      throw new Error(m);
    },
  };
  const plugin = widgetLocales({ localesDir });
  const resolveId = plugin.resolveId as (id: string) => string | null;
  const load = plugin.load as (this: Ctx, id: string) => string | null;

  it("generates the language index with one lazy import per locale", () => {
    const id = resolveId("virtual:webchat-locales");
    expect(id).toBe("\0virtual:webchat-locales");
    const code = load.call(ctx, id as string) as string;
    expect(code).toContain('export const LANGS = [');
    expect(code).toContain('"de": () => import("virtual:webchat-locale/de")');
    expect(code).toContain('"pt_BR": () => import("virtual:webchat-locale/pt_BR")');
  });

  it("resolves only existing languages and emits the flattened messages", () => {
    expect(resolveId("virtual:webchat-locale/../secrets")).toBeNull();
    expect(resolveId("virtual:webchat-locale/xx")).toBeNull();
    const id = resolveId("virtual:webchat-locale/de") as string;
    expect(localeOfModule(id)).toBe("de");
    const code = load.call(ctx, id) as string;
    const messages = JSON.parse(code.replace(/^export default /, "").replace(/;\s*$/, ""));
    expect(messages["launcher.open"]).toBe("Chat öffnen");
    expect(Object.keys(messages).every((k) => !k.startsWith("webchatWidget"))).toBe(true);
  });
});
