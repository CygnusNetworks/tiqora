/**
 * Znuny-compatible locale registry for the Tiqora UI.
 *
 * Codes match Znuny `UserLanguage` / `i18n/Znuny/Znuny.<code>.po` so agent UI
 * choice, notification templates, and legacy preferences stay aligned.
 * Only locales with a shipped JSON under `./locales/` are fully translated;
 * everything else falls back to English strings via i18next while still using
 * the correct BCP-47 tag for `Intl` formatting and `document.dir`.
 */

export type TextDirection = "ltr" | "rtl";

export type LocaleDef = {
  /** Znuny-style code (`de`, `pt_BR`, `zh_CN`). */
  code: string;
  /** Autonym shown in language pickers. */
  label: string;
  /** BCP-47 tag for `Intl` and `document.documentElement.lang`. */
  bcp47: string;
  dir: TextDirection;
  /** `true` when `./locales/<code>.json` is bundled. */
  translated: boolean;
};

/** Storage key for the agent/portal UI language preference. */
export const LANG_STORAGE_KEY = "tiqora-lang";

export const DEFAULT_LOCALE = "en";

/**
 * Full Znuny language set (plus plain `en` as the Tiqora default / source).
 * Order: English + German first (day-one translations), then alphabetical by label.
 */
export const SUPPORTED_LOCALES: readonly LocaleDef[] = [
  { code: "en", label: "English", bcp47: "en", dir: "ltr", translated: true },
  { code: "de", label: "Deutsch", bcp47: "de", dir: "ltr", translated: true },
  { code: "ar_SA", label: "العربية", bcp47: "ar-SA", dir: "rtl", translated: false },
  { code: "bg", label: "Български", bcp47: "bg", dir: "ltr", translated: false },
  { code: "ca", label: "Català", bcp47: "ca", dir: "ltr", translated: false },
  { code: "cs", label: "Čeština", bcp47: "cs", dir: "ltr", translated: false },
  { code: "da", label: "Dansk", bcp47: "da", dir: "ltr", translated: false },
  { code: "el", label: "Ελληνικά", bcp47: "el", dir: "ltr", translated: false },
  { code: "en_CA", label: "English (Canada)", bcp47: "en-CA", dir: "ltr", translated: false },
  { code: "en_GB", label: "English (UK)", bcp47: "en-GB", dir: "ltr", translated: false },
  { code: "es", label: "Español", bcp47: "es", dir: "ltr", translated: false },
  { code: "es_CO", label: "Español (Colombia)", bcp47: "es-CO", dir: "ltr", translated: false },
  { code: "es_MX", label: "Español (México)", bcp47: "es-MX", dir: "ltr", translated: false },
  { code: "et", label: "Eesti", bcp47: "et", dir: "ltr", translated: false },
  { code: "fa", label: "فارسی", bcp47: "fa", dir: "rtl", translated: false },
  { code: "fi", label: "Suomi", bcp47: "fi", dir: "ltr", translated: false },
  { code: "fr", label: "Français", bcp47: "fr", dir: "ltr", translated: false },
  { code: "fr_CA", label: "Français (Canada)", bcp47: "fr-CA", dir: "ltr", translated: false },
  { code: "gl", label: "Galego", bcp47: "gl", dir: "ltr", translated: false },
  { code: "he", label: "עברית", bcp47: "he", dir: "rtl", translated: false },
  { code: "hi", label: "हिन्दी", bcp47: "hi", dir: "ltr", translated: false },
  { code: "hr", label: "Hrvatski", bcp47: "hr", dir: "ltr", translated: false },
  { code: "hu", label: "Magyar", bcp47: "hu", dir: "ltr", translated: false },
  { code: "id", label: "Bahasa Indonesia", bcp47: "id", dir: "ltr", translated: false },
  { code: "it", label: "Italiano", bcp47: "it", dir: "ltr", translated: false },
  { code: "ja", label: "日本語", bcp47: "ja", dir: "ltr", translated: false },
  { code: "ko", label: "한국어", bcp47: "ko", dir: "ltr", translated: false },
  { code: "lt", label: "Lietuvių", bcp47: "lt", dir: "ltr", translated: false },
  { code: "lv", label: "Latviešu", bcp47: "lv", dir: "ltr", translated: false },
  { code: "mk", label: "Македонски", bcp47: "mk", dir: "ltr", translated: false },
  { code: "ms", label: "Bahasa Melayu", bcp47: "ms", dir: "ltr", translated: false },
  { code: "nb_NO", label: "Norsk bokmål", bcp47: "nb-NO", dir: "ltr", translated: false },
  { code: "nl", label: "Nederlands", bcp47: "nl", dir: "ltr", translated: false },
  { code: "pl", label: "Polski", bcp47: "pl", dir: "ltr", translated: false },
  { code: "pt", label: "Português", bcp47: "pt", dir: "ltr", translated: false },
  { code: "pt_BR", label: "Português (Brasil)", bcp47: "pt-BR", dir: "ltr", translated: false },
  { code: "ro", label: "Română", bcp47: "ro", dir: "ltr", translated: false },
  { code: "ru", label: "Русский", bcp47: "ru", dir: "ltr", translated: false },
  { code: "sk_SK", label: "Slovenčina", bcp47: "sk-SK", dir: "ltr", translated: false },
  { code: "sl", label: "Slovenščina", bcp47: "sl", dir: "ltr", translated: false },
  { code: "sr", label: "Српски", bcp47: "sr", dir: "ltr", translated: false },
  { code: "sv", label: "Svenska", bcp47: "sv", dir: "ltr", translated: false },
  { code: "sw", label: "Kiswahili", bcp47: "sw", dir: "ltr", translated: false },
  { code: "th_TH", label: "ไทย", bcp47: "th-TH", dir: "ltr", translated: false },
  { code: "tr", label: "Türkçe", bcp47: "tr", dir: "ltr", translated: false },
  { code: "uk", label: "Українська", bcp47: "uk", dir: "ltr", translated: false },
  { code: "vi_VN", label: "Tiếng Việt", bcp47: "vi-VN", dir: "ltr", translated: false },
  { code: "zh_CN", label: "简体中文", bcp47: "zh-CN", dir: "ltr", translated: false },
  { code: "zh_TW", label: "繁體中文", bcp47: "zh-TW", dir: "ltr", translated: false },
] as const;

const byCode = new Map(SUPPORTED_LOCALES.map((l) => [l.code.toLowerCase(), l]));
const byBcp47 = new Map(SUPPORTED_LOCALES.map((l) => [l.bcp47.toLowerCase(), l]));

/** Locale codes that ship a full UI translation JSON. */
export const TRANSLATED_LOCALE_CODES: readonly string[] = SUPPORTED_LOCALES.filter(
  (l) => l.translated,
).map((l) => l.code);

/** All Znuny-compatible language codes (UI + content pickers). */
export const LOCALE_CODES: readonly string[] = SUPPORTED_LOCALES.map((l) => l.code);

export function getLocale(code: string | null | undefined): LocaleDef | undefined {
  if (!code) return undefined;
  const raw = code.trim();
  if (!raw) return undefined;
  const lower = raw.toLowerCase();
  return byCode.get(lower) ?? byBcp47.get(lower) ?? byCode.get(lower.replace(/-/g, "_"));
}

/**
 * Resolve an arbitrary language tag (stored preference, i18n.language, navigator)
 * to a supported Znuny-style code. Falls back to {@link DEFAULT_LOCALE}.
 */
export function resolveLocaleCode(lang: string | null | undefined): string {
  if (!lang) return DEFAULT_LOCALE;
  const exact = getLocale(lang);
  if (exact) return exact.code;

  // i18next may report "de-DE" / "pt-BR" — try base + underscore form.
  const normalized = lang.replace(/-/g, "_");
  const underscored = getLocale(normalized);
  if (underscored) return underscored.code;

  const base = lang.split(/[-_]/)[0]?.toLowerCase();
  if (base) {
    const baseMatch = getLocale(base);
    if (baseMatch) return baseMatch.code;
    // Prefer a regional variant when only the base is known (e.g. "zh" → zh_CN).
    const regional = SUPPORTED_LOCALES.find((l) => l.code.toLowerCase().startsWith(`${base}_`));
    if (regional) return regional.code;
  }
  return DEFAULT_LOCALE;
}

/** BCP-47 tag for `Intl.*` formatters. */
export function toBcp47(lang: string | null | undefined): string {
  return getLocale(resolveLocaleCode(lang))?.bcp47 ?? DEFAULT_LOCALE;
}

/** Text direction for the resolved locale. */
export function textDirection(lang: string | null | undefined): TextDirection {
  return getLocale(resolveLocaleCode(lang))?.dir ?? "ltr";
}

/** Apply `lang` + `dir` on `<html>` for a11y and RTL layout hooks. */
export function applyDocumentLocale(lang: string | null | undefined): void {
  if (typeof document === "undefined") return;
  const code = resolveLocaleCode(lang);
  const loc = getLocale(code);
  document.documentElement.lang = loc?.bcp47 ?? DEFAULT_LOCALE;
  document.documentElement.dir = loc?.dir ?? "ltr";
}

export function readStoredLang(): string {
  try {
    return resolveLocaleCode(localStorage.getItem(LANG_STORAGE_KEY) ?? DEFAULT_LOCALE);
  } catch {
    return DEFAULT_LOCALE;
  }
}

export function writeStoredLang(code: string): void {
  try {
    localStorage.setItem(LANG_STORAGE_KEY, resolveLocaleCode(code));
  } catch {
    // private mode / SSR — ignore
  }
}

/** Items ready for `SelectMenu` / settings pickers. */
export function localePickerItems(): { value: string; label: string; hint?: string }[] {
  return SUPPORTED_LOCALES.map((l) => ({
    value: l.code,
    label: l.label,
    hint: l.translated ? undefined : l.code,
  }));
}
