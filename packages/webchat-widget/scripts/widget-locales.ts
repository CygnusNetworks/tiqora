/**
 * Build-time extraction of the `webchatWidget.*` texts from the app's locale files
 * (`frontend/src/i18n/locales/*.json`, the single source of truth for every language).
 *
 * The widget never ships the app's full locale files: each language becomes one lazy
 * chunk holding only the flattened widget namespace (`{"launcher.open": "…"}`).
 *
 * Virtual modules:
 * - `virtual:webchat-locales` → `export const LANGS = [...]; export const load = { de: () => import("virtual:webchat-locale/de"), … }`
 * - `virtual:webchat-locale/<lang>` → `export default {…flattened…}`; keys the language
 *   lacks (or leaves empty) are filled with the English text at build time, so the
 *   runtime needs only ONE locale chunk per frame start (R-BUDGET).
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { Plugin } from "vite";

const INDEX_ID = "virtual:webchat-locales";
const LOCALE_PREFIX = "virtual:webchat-locale/";
const RESOLVED_INDEX = `\0${INDEX_ID}`;
const RESOLVED_LOCALE_PREFIX = `\0${LOCALE_PREFIX}`;
const FALLBACK_LANG = "en";
const LANG_RE = /^[A-Za-z]{2,3}(_[A-Za-z0-9]{2,4})?$/;

/** Flatten `locale[namespace]` to dotted keys; non-string leaves are ignored. */
export function extractWidgetMessages(
  locale: Record<string, unknown>,
  namespace = "webchatWidget",
): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (node: unknown, prefix: string): void => {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return;
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      const path = prefix ? `${prefix}.${key}` : key;
      if (typeof value === "string") out[path] = value;
      else walk(value, path);
    }
  };
  walk(locale[namespace], "");
  return out;
}

/** Chunk-name prefix of the per-language chunks (check-size.mjs relies on it). */
export const LOCALE_CHUNK_PREFIX = "webchat-locale-";

/** Language of a resolved locale module id, or `null` for any other module. */
export function localeOfModule(id: string | null | undefined): string | null {
  if (!id || !id.startsWith(RESOLVED_LOCALE_PREFIX)) return null;
  const lang = id.slice(RESOLVED_LOCALE_PREFIX.length);
  return LANG_RE.test(lang) ? lang : null;
}

export function widgetLocales(opts: { localesDir: string; namespace?: string }): Plugin {
  const dir = resolve(opts.localesDir);
  const namespace = opts.namespace ?? "webchatWidget";

  const fileOf = (lang: string) => join(dir, `${lang}.json`);
  const langs = (): string[] =>
    readdirSync(dir)
      .filter((f) => f.endsWith(".json"))
      .map((f) => f.slice(0, -".json".length))
      .filter((l) => LANG_RE.test(l))
      .sort();
  const messagesOf = (lang: string): Record<string, string> =>
    extractWidgetMessages(
      JSON.parse(readFileSync(fileOf(lang), "utf8")) as Record<string, unknown>,
      namespace,
    );

  return {
    name: "tiqora-webchat-locales",
    resolveId(id) {
      if (id === INDEX_ID) return RESOLVED_INDEX;
      if (id.startsWith(LOCALE_PREFIX)) {
        const lang = id.slice(LOCALE_PREFIX.length);
        if (LANG_RE.test(lang) && langs().includes(lang)) return `\0${id}`;
      }
      return null;
    },
    load(id) {
      if (id === RESOLVED_INDEX) {
        const all = langs();
        if (!all.includes(FALLBACK_LANG)) {
          this.error(`webchat locales: ${FALLBACK_LANG}.json missing in ${dir}`);
        }
        for (const lang of all) this.addWatchFile(fileOf(lang));
        const loaders = all
          .map((l) => `  ${JSON.stringify(l)}: () => import(${JSON.stringify(LOCALE_PREFIX + l)}),`)
          .join("\n");
        return `export const LANGS = ${JSON.stringify(all)};\nexport const load = {\n${loaders}\n};\n`;
      }
      const lang = localeOfModule(id);
      if (lang !== null) {
        this.addWatchFile(fileOf(lang));
        this.addWatchFile(fileOf(FALLBACK_LANG));
        const english = messagesOf(FALLBACK_LANG);
        const own = lang === FALLBACK_LANG ? english : messagesOf(lang);
        const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);
        const merged: Record<string, string> = {};
        for (const [key, text] of Object.entries(english)) {
          merged[key] = has(own, key) && own[key] ? own[key] : text;
        }
        for (const [key, text] of Object.entries(own)) {
          if (text && !has(merged, key)) merged[key] = text;
        }
        return `export default ${JSON.stringify(merged)};\n`;
      }
      return null;
    },
  };
}
