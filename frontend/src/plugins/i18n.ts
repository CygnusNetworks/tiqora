/**
 * Plugin translations (P12): `asset_base + "i18n/<lang>.json"` is loaded into
 * the i18next namespace `plugin.<id>`, so `t("plugin.<id>:some.key")` works
 * anywhere (nav labels, settings forms) and `usePluginTranslation()` inside
 * the plugin. Fallback: the language without region, then English, then
 * nothing (keys show their fallback text).
 */
import i18n from "@/i18n";
import type { FrontendPlugin } from "@/lib/pluginsApi";
import { PLUGIN_IMPORT_TIMEOUT_MS, pluginAssetUrl } from "./loader";

export function pluginNamespace(pluginId: string): string {
  return `plugin.${pluginId}`;
}

const LOCALE_CODE = /^[A-Za-z]{2,3}(?:[_-][A-Za-z0-9]{2,8})?$/;

/** Successful (or in-flight) loads per `id@version:lang`: the file's texts. */
const loads = new Map<string, Promise<object | null>>();

/** The plugin's best file for `lang`: exact, then without region, then English. */
function pickLocale(available: string[], lang: string): string | null {
  const base = lang.split(/[_-]/)[0];
  for (const candidate of [lang, base, "en"]) {
    if (available.includes(candidate)) return candidate;
  }
  return null;
}

async function fetchBundle(plugin: FrontendPlugin, lang: string): Promise<object | null> {
  // Same limit as the module import; a stalled file must not hold the slot.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PLUGIN_IMPORT_TIMEOUT_MS);
  try {
    const url = pluginAssetUrl(plugin, `i18n/${lang}.json`);
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
    return data;
  } catch (err) {
    console.warn(`[plugin ${plugin.id}] translations ${lang} not loaded`, err);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function loadOnce(plugin: FrontendPlugin, lang: string): Promise<object | null> {
  const key = `${plugin.id}@${plugin.version}:${lang}`;
  const existing = loads.get(key);
  if (existing) return existing;
  const promise = fetchBundle(plugin, lang).then((data) => {
    if (!data) loads.delete(key); // try again next time (language switch, reload of the list)
    return data;
  });
  loads.set(key, promise);
  return promise;
}

/**
 * Make the plugin's texts for `lang` available. English is loaded as well when
 * the plugin has it, for keys the chosen file lacks (i18next falls back to en).
 * A file without region (`pt.json` for `pt_BR`) is also registered under the
 * requested language: the app's i18next (`load: "currentOnly"`) resolves
 * `pt_BR` → `en` and would never look at `pt`. Never rejects.
 */
export async function ensurePluginI18n(plugin: FrontendPlugin, lang: string): Promise<void> {
  const available = plugin.locales.filter((code) => LOCALE_CODE.test(code));
  const chosen = pickLocale(available, lang);
  if (!chosen) return;
  const wanted = chosen !== "en" && available.includes("en") ? [chosen, "en"] : [chosen];
  const ns = pluginNamespace(plugin.id);
  await Promise.all(
    wanted.map(async (code) => {
      const data = await loadOnce(plugin, code);
      if (!data) return;
      i18n.addResourceBundle(code, ns, data, true, true);
      if (code === chosen && code !== lang && code !== "en") {
        i18n.addResourceBundle(lang, ns, data, true, true);
      }
    }),
  );
}
