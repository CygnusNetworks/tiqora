import { describe, expect, it } from "vitest";
import { LANGS } from "virtual:webchat-locales";
import { RTL_LANGS, isRtl, loadMessages, pickLanguage } from "./i18n";

describe("widget i18n", () => {
  it("picks languages", () => {
    expect(pickLanguage("de-AT", ["de", "en"])).toBe("de");
    expect(pickLanguage("xx", ["de", "en"])).toBe("en");
    expect(pickLanguage(null, ["de", "en"])).toBe("en");
    expect(RTL_LANGS.has("ar")).toBe(true);
  });

  it("maps region tags onto the locale file names", () => {
    expect(pickLanguage("pt-BR", ["pt", "pt_BR", "en"])).toBe("pt_BR");
    expect(pickLanguage("pt-PT", ["pt", "pt_BR", "en"])).toBe("pt");
    expect(pickLanguage("ar", ["ar_SA", "en"])).toBe("ar_SA");
    expect(pickLanguage("", ["de", "en"])).toBe("en");
    expect(isRtl("ar_SA")).toBe(true);
    expect(isRtl("de")).toBe(false);
  });

  it("interpolates as plain text", async () => {
    const t = await loadMessages("de");
    expect(t("status.active", { name: "<b>Max</b>" })).toBe("<b>Max</b> ist im Chat");
  });

  it("falls back to English for unknown languages and to the key for unknown keys", async () => {
    const en = await loadMessages("xx");
    expect(en("launcher.open")).toBe("Open chat");
    expect(en("does.not.exist")).toBe("does.not.exist");
    expect(en("constructor")).toBe("constructor");
    expect(en("composer.tooLong", { max: "4000" })).toContain("4000");
  });

  it("builds one chunk per locale file", () => {
    expect(LANGS).toContain("de");
    expect(LANGS).toContain("en");
    expect(LANGS.length).toBeGreaterThan(40);
  });
});
