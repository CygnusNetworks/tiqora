/** Shared bits of the CAB meeting pages. */
import type { CabMeeting, ChangePhase, ChangeType } from "@/lib/changeApi";

export const MEETING_STATUS_TONE: Record<CabMeeting["status"], "accent" | "success" | "muted"> = {
  planned: "accent",
  held: "success",
  cancelled: "muted",
};

/** `[first of the month, first of the next month)` as UTC instants (all change screens show UTC). */
export function utcMonthRange(year: number, month: number): { start: string; end: string } {
  const iso = (y: number, m: number) => new Date(Date.UTC(y, m, 1)).toISOString().replace(".000Z", "Z");
  return { start: iso(year, month), end: iso(year, month + 1) };
}

/** The month `delta` months from `{year, month}` (month 0-11; the year rolls over). */
export function shiftMonth(c: { year: number; month: number }, delta: number): { year: number; month: number } {
  const d = new Date(Date.UTC(c.year, c.month + delta, 1));
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() };
}

export type AgendaDraftItem = {
  ticket_id: number;
  tn: string;
  title: string;
  change_type: ChangeType | null;
  phase: ChangePhase | null;
  planned_start: string | null;
  planned_end: string | null;
  note: string;
  approval: CabMeeting["agenda"][number]["approval"];
};

/** The agenda lines the caller may read, in order; lines without read access are not editable and stay on the server. */
export function visibleAgenda(meeting: CabMeeting): AgendaDraftItem[] {
  return meeting.agenda
    .filter((e) => !e.hidden && e.change_ticket_id != null)
    .sort((a, b) => a.position - b.position)
    .map((e) => ({
      ticket_id: e.change_ticket_id as number,
      tn: e.tn ?? "",
      title: e.title ?? "",
      change_type: e.change_type,
      phase: e.phase,
      planned_start: e.planned_start,
      planned_end: e.planned_end,
      note: e.note ?? "",
      approval: e.approval,
    }));
}

/** `items` with the entry at `from` moved to `to` (out-of-range indexes leave the list as it is). */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return [...items];
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
