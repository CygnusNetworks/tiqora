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

/** Znuny's "nobody" owner (backend `UNASSIGNED_OWNER_ID`): unowned tickets belong to user 1. */
export const UNASSIGNED_OWNER_ID = 1;

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
