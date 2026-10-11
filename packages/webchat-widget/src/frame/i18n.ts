/**
 * Widget i18n runtime: one lazily loaded chunk per language (built from the app's
 * `webchatWidget.*` keys by scripts/widget-locales.ts), plain-text interpolation.
 *
 * Fallback chain for a key: the language's text → the English text (merged into every
 * language chunk at build time) → the key itself.
 */
import { LANGS, load } from "virtual:webchat-locales";

export type Translate = (key: string, vars?: Record<string, string>) => string;

export const FALLBACK_LANG = "en";
export const RTL_LANGS: ReadonlySet<string> = new Set(["ar", "he", "fa", "ur"]);

const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);

/** Base language of a tag: `de-AT` / `de_AT` → `de`. */
export function baseLanguage(tag: string): string {
  return tag.split(/[-_]/)[0].toLowerCase();
}

export function isRtl(lang: string): boolean {
  return RTL_LANGS.has(baseLanguage(lang));
}

/**
 * Best available language for a requested BCP 47 tag (`de-AT` → `de`, `pt-BR` →
 * `pt_BR`, `ar` → `ar_SA`); anything unknown or missing → `en`.
 */
export function pickLanguage(requested: string | null, available: readonly string[]): string {
  const fallback = available.includes(FALLBACK_LANG) ? FALLBACK_LANG : (available[0] ?? FALLBACK_LANG);
  if (!requested) return fallback;
  const wanted = requested.trim().replace(/-/g, "_").toLowerCase();
  if (!wanted) return fallback;
  const exact = available.find((l) => l.toLowerCase() === wanted);
  if (exact) return exact;
  const base = baseLanguage(wanted);
  const sameBase = available.find((l) => l.toLowerCase() === base);
  if (sameBase) return sameBase;
  const regional = available.find((l) => baseLanguage(l) === base);
  return regional ?? fallback;
}

function interpolate(template: string, vars: Record<string, string> | undefined): string {
  if (!vars) return template;
  // Values are inserted as plain text; the result is always rendered as a text node.
  return template.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (match, name: string) =>
    has(vars, name) ? String(vars[name]) : match,
  );
}

/** Load the messages of *lang* (unknown → English) and return a translate function. */
export async function loadMessages(lang: string): Promise<Translate> {
  const chosen = pickLanguage(lang, LANGS);
  const loader = load[chosen] ?? load[FALLBACK_LANG];
  let messages: Record<string, string> = {};
  try {
    // English is already merged into every chunk at build time: no second runtime load.
    if (loader) messages = (await loader()).default;
  } catch {
    // chunk fetch failed (offline, blocked): the keys themselves are the fallback text
  }
  return (key, vars) => interpolate(has(messages, key) ? messages[key] : key, vars);
}
