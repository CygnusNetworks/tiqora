import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ticketPerms } from "@/lib/ticket";

/**
 * "Danach" — what happens to the ticket once a reply goes out. Shared by the
 * reply dialog and the Telegram chat composer so both send the same
 * `state_id`/`pending_time` pair.
 */
export type NextState = "keep" | "pending" | "closed";

/** Choices for the ticket after the reply is sent. `color` is the state colour
 * the segment takes when picked. */
export const NEXT_STATES: { key: NextState; color: string }[] = [
  { key: "keep", color: "var(--color-state-open)" },
  { key: "pending", color: "var(--color-state-pending)" },
  { key: "closed", color: "var(--color-state-new)" },
];

function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function todayIso(): string {
  return isoDate(new Date());
}
/** Default reminder for "Wartend": three days from today. */
export function defaultPendingDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 3);
  return isoDate(d);
}

/**
 * The ticket and the state catalogue are cache hits on the ticket page. Only
 * agents who may change the state get the control — the backend refuses
 * `state_id` without `rw` (a note-only agent can still reply).
 */
export function useNextStateOptions(ticketId: number, enabled: boolean) {
  const ticketQ = useQuery({
    queryKey: ["tickets", ticketId],
    queryFn: () => api.getTicket(ticketId),
    enabled,
  });
  const statesQ = useQuery({
    queryKey: ["reference", "states"],
    queryFn: () => api.listReferenceStates(),
    enabled,
  });
  const states = statesQ.data ?? [];
  const pendingState =
    states.find((s) => s.name === "pending reminder") ??
    states.find((s) => s.type_name === "pending reminder");
  const closedState =
    states.find((s) => s.name === "closed successful") ??
    states.find((s) => s.type_name.startsWith("closed"));
  const canSetState = Boolean(ticketQ.data && ticketPerms(ticketQ.data).rw);
  /** Writing to a closed ticket usually means the agent now waits for the
   * customer (a follow-up question, "is it solved?"). Plain "Senden" would
   * leave it closed and the answer unnoticed, so the default becomes
   * "Wartend" there — Znuny reopens on reply too (StateDefault "open"), but
   * here the ball is with the customer, not the agent. */
  const ticketClosed = (ticketQ.data?.state_type ?? "").startsWith("closed");
  const options = NEXT_STATES.filter(
    (o) =>
      o.key === "keep" ||
      (o.key === "pending" && pendingState) ||
      (o.key === "closed" && closedState),
  );
  const stateIdFor = (next: NextState): number | undefined =>
    next === "pending" ? pendingState?.id : next === "closed" ? closedState?.id : undefined;

  /** The `state_id`/`pending_time` part of the create-article payload. */
  const payloadFor = (next: NextState, pendingDate: string) => {
    const stateId = stateIdFor(next);
    if (!canSetState || stateId == null) return {};
    return {
      state_id: stateId,
      // 08:00 local on the chosen day — a reminder for the morning.
      pending_time: next === "pending" ? new Date(`${pendingDate}T08:00`).toISOString() : null,
    };
  };

  const defaultNext: NextState =
    ticketClosed && canSetState && options.some((o) => o.key === "pending") ? "pending" : "keep";

  return { canSetState, options, stateIdFor, payloadFor, defaultNext };
}
