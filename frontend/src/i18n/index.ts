import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import de from "./locales/de.json";
import en from "./locales/en.json";
import {
  DEFAULT_LOCALE,
  applyDocumentLocale,
  readStoredLang,
  resolveLocaleCode,
  writeStoredLang,
} from "./locales";

/**
 * Eager resources for the two day-one languages. All other shipped locales
 * load on demand via {@link ensureLocaleLoaded} so the main bundle stays small.
 */
const bundledResources: Record<string, { translation: object }> = {
  en: { translation: en },
  de: { translation: de },
};

/**
 * Dynamic loaders for priority languages beyond en/de. Vite code-splits each
 * JSON into its own chunk.
 */
const localeLoaders: Record<string, () => Promise<{ default: object }>> = {
  fr: () => import("./locales/fr.json"),
  es: () => import("./locales/es.json"),
  it: () => import("./locales/it.json"),
  nl: () => import("./locales/nl.json"),
  pl: () => import("./locales/pl.json"),
  pt_BR: () => import("./locales/pt_BR.json"),
  ru: () => import("./locales/ru.json"),
  zh_CN: () => import("./locales/zh_CN.json"),
  ja: () => import("./locales/ja.json"),
  tr: () => import("./locales/tr.json"),
  cs: () => import("./locales/cs.json"),
  hu: () => import("./locales/hu.json"),
  sv: () => import("./locales/sv.json"),
};

const loadedCodes = new Set(Object.keys(bundledResources));

export async function ensureLocaleLoaded(code: string): Promise<void> {
  const resolved = resolveLocaleCode(code);
  if (loadedCodes.has(resolved)) return;
  const loader = localeLoaders[resolved];
  if (!loader) {
    // Untranslated locale: i18next falls back to `en` for missing keys.
    loadedCodes.add(resolved);
    return;
  }
  try {
    const mod = await loader();
    i18n.addResourceBundle(resolved, "translation", mod.default, true, true);
    loadedCodes.add(resolved);
  } catch (err) {
    // Missing chunk during development before MT finishes — fall back to en.
    console.warn(`[i18n] failed to load locale ${resolved}`, err);
    loadedCodes.add(resolved);
  }
}

export type SetAppLanguageOptions = {
  /** When true (default), also persist Znuny UserLanguage via the API if logged in. */
  persistRemote?: boolean;
};

/**
 * Switch UI language: persist preference, load resources if needed, update
 * i18next + `<html lang/dir>`. Optionally syncs to `PUT /auth/me/language`.
 */
export async function setAppLanguage(
  code: string,
  opts: SetAppLanguageOptions = {},
): Promise<void> {
  const resolved = resolveLocaleCode(code);
  writeStoredLang(resolved);
  await ensureLocaleLoaded(resolved);
  await i18n.changeLanguage(resolved);
  applyDocumentLocale(resolved);

  if (opts.persistRemote === false) return;
  try {
    // Dynamic import avoids a circular dep with the api client package.
    const { api } = await import("@/lib/api");
    await api.setMyLanguage(resolved);
  } catch {
    // Not authenticated or network error — local preference still applies.
  }
}

void i18n.use(initReactI18next).init({
  resources: bundledResources,
  lng: readStoredLang(),
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: false,
  nonExplicitSupportedLngs: true,
  load: "currentOnly",
  interpolation: { escapeValue: false },
  returnNull: false,
});

i18n.on("languageChanged", (lng) => {
  applyDocumentLocale(lng);
});

applyDocumentLocale(i18n.language);

// Preload the stored language if it is not already in the eager bundle.
const initial = readStoredLang();
if (initial !== "en" && initial !== "de") {
  void ensureLocaleLoaded(initial).then(() => {
    if (i18n.language !== initial) {
      void i18n.changeLanguage(initial);
    }
  });
}

export {
  DEFAULT_LOCALE,
  LANG_STORAGE_KEY,
  LOCALE_CODES,
  SHIPPED_UI_LOCALE_CODES,
  SUPPORTED_LOCALES,
  TRANSLATED_LOCALE_CODES,
  applyDocumentLocale,
  getLocale,
  localePickerItems,
  readStoredLang,
  resolveLocaleCode,
  textDirection,
  toBcp47,
  writeStoredLang,
} from "./locales";

export default i18n;
