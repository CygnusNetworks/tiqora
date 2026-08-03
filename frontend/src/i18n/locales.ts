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
 * Priority UI languages shipped with translation JSON (~15 most common for
 * European/global helpdesks). All other Znuny codes remain in the registry
 * for content pickers (KB, AI reply language) and `UserLanguage` validation.
 */
export const SHIPPED_UI_LOCALE_CODES = [
  "en",
  "de",
  "fr",
  "es",
  "it",
  "nl",
  "pl",
  "pt_BR",
  "ru",
  "zh_CN",
  "ja",
  "tr",
  "cs",
  "hu",
  "sv",
] as const;

const SHIPPED = new Set<string>(SHIPPED_UI_LOCALE_CODES);

function loc(
  code: string,
  label: string,
  bcp47: string,
  dir: TextDirection = "ltr",
): LocaleDef {
  return { code, label, bcp47, dir, translated: SHIPPED.has(code) };
}

/**
 * Full Znuny language set (plus plain `en` as the Tiqora default / source).
 * Order: English + German first, then remaining shipped UI languages, then
 * the rest of Znuny codes alphabetical by label.
 */
export const SUPPORTED_LOCALES: readonly LocaleDef[] = [
  loc("en", "English", "en"),
  loc("de", "Deutsch", "de"),
  loc("fr", "Français", "fr"),
  loc("es", "Español", "es"),
  loc("it", "Italiano", "it"),
  loc("nl", "Nederlands", "nl"),
  loc("pl", "Polski", "pl"),
  loc("pt_BR", "Português (Brasil)", "pt-BR"),
  loc("ru", "Русский", "ru"),
  loc("zh_CN", "简体中文", "zh-CN"),
  loc("ja", "日本語", "ja"),
  loc("tr", "Türkçe", "tr"),
  loc("cs", "Čeština", "cs"),
  loc("hu", "Magyar", "hu"),
  loc("sv", "Svenska", "sv"),
  // Remaining Znuny codes (content / UserLanguage only until translated).
  loc("ar_SA", "العربية", "ar-SA", "rtl"),
  loc("bg", "Български", "bg"),
  loc("ca", "Català", "ca"),
  loc("da", "Dansk", "da"),
  loc("el", "Ελληνικά", "el"),
  loc("en_CA", "English (Canada)", "en-CA"),
  loc("en_GB", "English (UK)", "en-GB"),
  loc("es_CO", "Español (Colombia)", "es-CO"),
  loc("es_MX", "Español (México)", "es-MX"),
  loc("et", "Eesti", "et"),
  loc("fa", "فارسی", "fa", "rtl"),
  loc("fi", "Suomi", "fi"),
  loc("fr_CA", "Français (Canada)", "fr-CA"),
  loc("gl", "Galego", "gl"),
  loc("he", "עברית", "he", "rtl"),
  loc("hi", "हिन्दी", "hi"),
  loc("hr", "Hrvatski", "hr"),
  loc("id", "Bahasa Indonesia", "id"),
  loc("ko", "한국어", "ko"),
  loc("lt", "Lietuvių", "lt"),
  loc("lv", "Latviešu", "lv"),
  loc("mk", "Македонски", "mk"),
  loc("ms", "Bahasa Melayu", "ms"),
  loc("nb_NO", "Norsk bokmål", "nb-NO"),
  loc("pt", "Português", "pt"),
  loc("ro", "Română", "ro"),
  loc("sk_SK", "Slovenčina", "sk-SK"),
  loc("sl", "Slovenščina", "sl"),
  loc("sr", "Српски", "sr"),
  loc("sw", "Kiswahili", "sw"),
  loc("th_TH", "ไทย", "th-TH"),
  loc("uk", "Українська", "uk"),
  loc("vi_VN", "Tiếng Việt", "vi-VN"),
  loc("zh_TW", "繁體中文", "zh-TW"),
];

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

/**
 * Items for language pickers. By default only locales with shipped UI JSON
 * (`translated: true`) appear so agents don't switch into English-fallback UIs.
 * Pass `{ all: true }` for admin/content language fields.
 */
export function localePickerItems(opts?: {
  all?: boolean;
}): { value: string; label: string; hint?: string }[] {
  const list = opts?.all ? SUPPORTED_LOCALES : SUPPORTED_LOCALES.filter((l) => l.translated);
  return list.map((l) => ({
    value: l.code,
    label: l.label,
    hint: l.translated ? undefined : l.code,
  }));
}
