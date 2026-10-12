import { TICKET_PRESETS, type TicketPreset } from "../definitions";
import type { TicketFilter } from "@/lib/savedSearchesApi";

/** Settings `source` of the counter and the ticket list. */
export interface PresetSource {
  kind: "preset";
  preset: TicketPreset;
}

/** A saved search, read as the viewer (not the dashboard's owner). */
export interface SavedSearchSource {
  kind: "saved_search";
  id: number;
}

/** An own filter (the wave-0 `Filter` JSON). */
export interface FilterSource {
  kind: "filter";
  filter: TicketFilter;
}

export type TicketSource = PresetSource | SavedSearchSource | FilterSource;

export const DEFAULT_SOURCE: PresetSource = { kind: "preset", preset: "mine_open" };

/** The id of the saved search a `source` names, or null. */
export function savedSearchIdOf(source: unknown): number | null {
  if (!source || typeof source !== "object") return null;
  const s = source as { kind?: unknown; id?: unknown };
  return s.kind === "saved_search" && typeof s.id === "number" && Number.isInteger(s.id) && s.id > 0 ? s.id : null;
}

/** Whether a `source` is a saved search or an own filter (no preset behind it). */
export function isCustomSource(source: unknown): boolean {
  if (!source || typeof source !== "object") return false;
  const kind = (source as { kind?: unknown }).kind;
  return kind === "saved_search" || kind === "filter";
}

/** The preset a `source` names, or null for other source kinds. */
export function presetOf(source: unknown): TicketPreset | null {
  if (!source || typeof source !== "object") return null;
  const s = source as { kind?: unknown; preset?: unknown };
  return s.kind === "preset" && (TICKET_PRESETS as readonly unknown[]).includes(s.preset)
    ? (s.preset as TicketPreset)
    : null;
}

/** Counters where fewer is better: a drop is good news (green). */
export const FEWER_IS_BETTER: ReadonlySet<TicketPreset> = new Set(["escalated", "ai_escalated", "unowned_new"]);

/** The preset a ticket widget is titled by. On a wallboard the server sends no `source` (it may name
 * a customer), only the preset name of a preset source in `settings.preset`; any other source has no
 * preset there. Elsewhere the `source` decides. */
export function widgetPreset(settings: { source?: unknown; preset?: unknown }, kiosk: boolean): TicketPreset | null {
  if (!kiosk) return presetOf(settings.source);
  return presetOf({ kind: "preset", preset: settings.preset });
}
