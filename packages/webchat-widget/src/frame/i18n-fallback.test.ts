import { describe, expect, it, vi } from "vitest";

vi.mock("virtual:webchat-locales", () => ({
  LANGS: ["de", "en"],
  load: {
    de: () => Promise.reject(new TypeError("Failed to fetch dynamically imported module")),
    en: () => Promise.reject(new TypeError("offline")),
  },
}));

import { loadMessages } from "./i18n";

describe("widget i18n chunk failure", () => {
  it("falls back to the keys instead of rejecting", async () => {
    const t = await loadMessages("de");
    expect(t("launcher.open")).toBe("launcher.open");
    expect(t("x", { a: "b" })).toBe("x");
  });
});
