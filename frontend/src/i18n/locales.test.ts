import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  TRANSLATED_LOCALE_CODES,
  applyDocumentLocale,
  getLocale,
  localePickerItems,
  resolveLocaleCode,
  textDirection,
  toBcp47,
} from "./locales";

describe("locale registry", () => {
  it("includes day-one translations and the Znuny language set", () => {
    expect(TRANSLATED_LOCALE_CODES).toEqual(expect.arrayContaining(["en", "de"]));
    // Plain `en` (source) + every Znuny .po code (48) → 49 entries.
    expect(SUPPORTED_LOCALES.length).toBe(49);
    expect(getLocale("pt_BR")?.bcp47).toBe("pt-BR");
    expect(getLocale("zh_CN")?.dir).toBe("ltr");
    expect(getLocale("ar_SA")?.dir).toBe("rtl");
    expect(getLocale("he")?.dir).toBe("rtl");
  });

  it("resolves Znuny codes, BCP-47 tags, and base-language fallbacks", () => {
    expect(resolveLocaleCode("de")).toBe("de");
    expect(resolveLocaleCode("de-DE")).toBe("de");
    expect(resolveLocaleCode("pt-BR")).toBe("pt_BR");
    expect(resolveLocaleCode("pt_BR")).toBe("pt_BR");
    expect(resolveLocaleCode("zh-CN")).toBe("zh_CN");
    expect(resolveLocaleCode("fr")).toBe("fr");
    expect(resolveLocaleCode("nope")).toBe(DEFAULT_LOCALE);
    expect(resolveLocaleCode(undefined)).toBe(DEFAULT_LOCALE);
  });

  it("maps to BCP-47 and text direction", () => {
    expect(toBcp47("de")).toBe("de");
    expect(toBcp47("pt_BR")).toBe("pt-BR");
    expect(toBcp47("ar_SA")).toBe("ar-SA");
    expect(textDirection("he")).toBe("rtl");
    expect(textDirection("en")).toBe("ltr");
  });

  it("exposes picker items for every supported locale", () => {
    const items = localePickerItems();
    expect(items.length).toBe(SUPPORTED_LOCALES.length);
    expect(items.find((i) => i.value === "de")?.label).toBe("Deutsch");
  });
});

describe("applyDocumentLocale", () => {
  const prevLang = document.documentElement.lang;
  const prevDir = document.documentElement.dir;

  beforeEach(() => {
    document.documentElement.lang = "";
    document.documentElement.dir = "";
  });

  afterEach(() => {
    document.documentElement.lang = prevLang;
    document.documentElement.dir = prevDir;
  });

  it("sets html lang and dir for LTR and RTL locales", () => {
    applyDocumentLocale("de");
    expect(document.documentElement.lang).toBe("de");
    expect(document.documentElement.dir).toBe("ltr");

    applyDocumentLocale("ar_SA");
    expect(document.documentElement.lang).toBe("ar-SA");
    expect(document.documentElement.dir).toBe("rtl");
  });
});
