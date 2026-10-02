import type { TelegramButtonIn, TemplateOut } from "@/lib/api";
import { stripHtml } from "@/lib/html";

/* Pure helpers of the Telegram chat composer, kept out of the component
 * files so those stay fast-refresh friendly. */

/** Telegram's limit for one text message. */
export const MAX_MESSAGE_LENGTH = 4096;

/** A `/query` being typed right before the caret: `/` at the start of the
 * text or after whitespace, then no whitespace up to the caret — so URLs and
 * "und/oder" never open the picker. */
export type SlashQuery = { start: number; end: number; query: string };

export function findSlashQuery(text: string, caret: number): SlashQuery | null {
  const m = /(^|\s)\/(\S*)$/.exec(text.slice(0, caret));
  if (!m) return null;
  const start = m.index + m[1].length;
  return { start, end: caret, query: m[2] };
}

export function snippetText(tpl: TemplateOut): string {
  return tpl.content_type?.includes("html") ? stripHtml(tpl.text) : tpl.text;
}

export function filterSnippets(templates: TemplateOut[], query: string): TemplateOut[] {
  const q = query.toLowerCase();
  if (!q) return templates;
  return templates.filter(
    (t) => t.name.toLowerCase().includes(q) || snippetText(t).toLowerCase().includes(q),
  );
}

/** Inline keyboards stay readable up to a handful of rows; the label limit
 * mirrors what the backend accepts for `telegram_buttons`. */
export const MAX_BUTTONS = 8;
export const MAX_BUTTON_LABEL = 64;

/** Customer-facing, so in the customer's language (the chat's
 * `customer_language`: what they wrote, else their Telegram app language),
 * not the agent's UI language. German for German, English otherwise — the
 * same rule as the bot's own texts (backend channels/telegram/texts.py). */
const RESOLVED_PRESETS: Record<"de" | "en", { body: string; yes: string; no: string }> = {
  de: { body: "Ist dein Problem damit gelöst?", yes: "Ja", no: "Nein" },
  en: { body: "Is your problem solved now?", yes: "Yes", no: "No" },
};

export function resolvedPreset(customerLanguage: string | null | undefined): {
  body: string;
  buttons: TelegramButtonIn[];
} {
  const p = RESOLVED_PRESETS[customerLanguage === "de" ? "de" : "en"];
  return {
    body: p.body,
    buttons: [
      { label: p.yes, action: "resolve_yes" },
      { label: p.no, action: "resolve_no" },
    ],
  };
}

/** What actually goes out: blank labels are dropped rather than refused, so
 * an "add" the agent didn't fill in never blocks the send. */
export function cleanButtons(buttons: TelegramButtonIn[]): TelegramButtonIn[] {
  return buttons
    .map((b) => ({ ...b, label: b.label.trim() }))
    .filter((b) => b.label.length > 0);
}
