/**
 * Small helpers of the old fixed home page, moved here unchanged from
 * `routes/agent/DashboardPage.tsx` so the widgets and the page share them.
 */
import { displayTimeZone, zonedParts } from "@/lib/timeZone";

/** The four escalation epochs of a ticket list row. */
export interface EscalationEpochs {
  escalation_time: number;
  escalation_response_time: number;
  escalation_update_time: number;
  escalation_solution_time: number;
}

/** Smallest escalation epoch that is actually set (>0), past or future. */
export function nearestEscalation(t: EscalationEpochs): number | null {
  const epochs = [
    t.escalation_time,
    t.escalation_response_time,
    t.escalation_update_time,
    t.escalation_solution_time,
  ].filter((e): e is number => typeof e === "number" && e > 0);
  return epochs.length ? Math.min(...epochs) : null;
}

/** Unsigned "3h20m" duration text for a breached escalation epoch — how long
 * ago it passed (the time left before a deadline uses `humanDuration`). */
export function formatDurationSince(epochSeconds: number): string {
  const diffMs = Math.max(0, Date.now() - epochSeconds * 1000);
  const abs = Math.round(diffMs / 60000);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** Time-of-day greeting key, German-style tri-split (morning/day/evening),
 * by the wall clock of `zone` (the agent's display zone). */
export function greetingKey(
  date = new Date(),
  zone: string = displayTimeZone(),
): "morning" | "day" | "evening" {
  const h = zonedParts(date, zone).hour;
  if (h < 11) return "morning";
  if (h < 18) return "day";
  return "evening";
}

/** Queues with work: open and/or new tickets (either count > 0). */
export function queueHasWork(queue: {
  counts?: { open?: number; new?: number } | null;
}): boolean {
  return (queue.counts?.open ?? 0) > 0 || (queue.counts?.new ?? 0) > 0;
}

/** Filter and rank queue shortcuts: only non-empty, highest open first, cap at 8. */
export function selectQueueShortcuts<
  T extends { counts?: { open?: number; new?: number } | null },
>(queues: T[], limit = 8): T[] {
  return queues
    .filter(queueHasWork)
    .slice()
    .sort((a, b) => (b.counts?.open ?? 0) - (a.counts?.open ?? 0))
    .slice(0, limit);
}
