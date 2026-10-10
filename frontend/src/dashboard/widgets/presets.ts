import { TICKET_PRESETS, type TicketPreset } from "../definitions";
import type { QueueLinkSearch } from "../chrome/WidgetFrame";

/** Settings `source` of the counter and the ticket list (phase 1: presets only). */
export interface PresetSource {
  kind: "preset";
  preset: TicketPreset;
}

export const DEFAULT_SOURCE: PresetSource = { kind: "preset", preset: "mine_open" };

/** The preset a `source` names, or null for other (future) source kinds. */
export function presetOf(source: unknown): TicketPreset | null {
  if (!source || typeof source !== "object") return null;
  const s = source as { kind?: unknown; preset?: unknown };
  return s.kind === "preset" && (TICKET_PRESETS as readonly unknown[]).includes(s.preset)
    ? (s.preset as TicketPreset)
    : null;
}

/** Counters where fewer is better: a drop is good news (green). */
export const FEWER_IS_BETTER: ReadonlySet<TicketPreset> = new Set(["escalated", "ai_escalated", "unowned_new"]);

/**
 * The queue view behind a ticket list's "Alle N anzeigen". `mine_new` and `unowned_new` match
 * exactly; `mine_open` opens the owner's "Zu tun" view (without waiting tickets) and the escalated
 * presets the escalated view (which also lists AI handoffs) — QueuesPage has no exact view for
 * those yet (R-DASH-COUNTER-LINKS). Null when the viewer's id is needed but unknown.
 */
export function presetQueueSearch(preset: TicketPreset, userId: number | null | undefined): QueueLinkSearch | null {
  switch (preset) {
    case "mine_open":
      return userId ? { owner_id: userId } : null;
    case "mine_new":
      return userId ? { owner_id: userId, state_type: "new" } : null;
    case "unowned_new":
      return { owner_id: 1, state_type: "new" };
    case "escalated":
    case "ai_escalated":
      return { escalated: true };
  }
}
