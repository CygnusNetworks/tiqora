import { describe, expect, it, vi } from "vitest";

const en = vi.hoisted(() => vi.fn(() => Promise.resolve({ default: { "launcher.open": "Open chat" } })));
vi.mock("virtual:webchat-locales", () => ({
  LANGS: ["de", "en"],
  load: {
    de: () => Promise.reject(new TypeError("Failed to fetch dynamically imported module")),
    en,
  },
}));

import { loadMessages } from "./i18n";

describe("widget i18n chunk failure", () => {
  it("falls back to the keys instead of rejecting", async () => {
    const t = await loadMessages("de");
    expect(t("launcher.open")).toBe("launcher.open");
    expect(t("x", { a: "b" })).toBe("x");
    expect(en).not.toHaveBeenCalled(); // no runtime English load
  });
});
