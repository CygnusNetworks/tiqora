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
 * Eager resources for day-one translations. Additional locales can be added
 * here once their JSON ships, or loaded on demand via {@link ensureLocaleLoaded}.
 */
const bundledResources: Record<string, { translation: object }> = {
  en: { translation: en },
  de: { translation: de },
};

/**
 * Dynamic loaders for locales that are not eager-bundled. Keep empty until a
 * third language ships — the hook is in place so pickers can call
 * `setAppLanguage` without a further i18n rewrite.
 */
const localeLoaders: Record<string, () => Promise<{ default: object }>> = {
  // e.g. fr: () => import("./locales/fr.json"),
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
  const mod = await loader();
  i18n.addResourceBundle(resolved, "translation", mod.default, true, true);
  loadedCodes.add(resolved);
}

/**
 * Switch UI language: persist preference, load resources if needed, update
 * i18next + `<html lang/dir>`. Safe to call from menus and settings.
 */
export async function setAppLanguage(code: string): Promise<void> {
  const resolved = resolveLocaleCode(code);
  writeStoredLang(resolved);
  await ensureLocaleLoaded(resolved);
  await i18n.changeLanguage(resolved);
  applyDocumentLocale(resolved);
}

void i18n.use(initReactI18next).init({
  resources: bundledResources,
  lng: readStoredLang(),
  fallbackLng: DEFAULT_LOCALE,
  // Accept Znuny-style codes (pt_BR) and BCP-47 (pt-BR).
  supportedLngs: false,
  nonExplicitSupportedLngs: true,
  load: "currentOnly",
  interpolation: { escapeValue: false },
  // Return the key path only when truly missing — partial locales fall back.
  returnNull: false,
});

i18n.on("languageChanged", (lng) => {
  applyDocumentLocale(lng);
});

// Initial document attributes (languageChanged does not fire on first init).
applyDocumentLocale(i18n.language);

export {
  DEFAULT_LOCALE,
  LANG_STORAGE_KEY,
  LOCALE_CODES,
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
