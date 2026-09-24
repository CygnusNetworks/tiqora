/**
 * Where the agent opened a ticket from — the list it belongs to — so the
 * ticket page can offer "← back to <that list>" and ‹ › through the same
 * list instead of a generic "back to queues".
 *
 * Kept in sessionStorage (per tab, survives a reload of the ticket page)
 * rather than in the ticket URL: the list's full filter/sort/page state is
 * just its own search params, and the id order is only needed for ‹ ›.
 */

export type TicketNavContext = {
  /** Display name of the origin, e.g. a queue name, "Meine Tickets", a search. */
  label: string;
  /** Router path of the origin list. */
  to: string;
  /** Path params when the origin route has any (e.g. a customer login). */
  params?: Record<string, string>;
  /** Search params to restore the list exactly (filters, sort, page). */
  search?: Record<string, unknown>;
  /** Ticket ids in the order the list showed them. */
  ids: number[];
};

const KEY = "tiqora.ticketNavContext";

export function setTicketNavContext(ctx: TicketNavContext): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(ctx));
  } catch {
    // Private mode / storage disabled: the ticket page falls back to its queue.
  }
}

export function getTicketNavContext(): TicketNavContext | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as TicketNavContext;
    if (!parsed || typeof parsed.label !== "string" || typeof parsed.to !== "string") return null;
    return { ...parsed, ids: Array.isArray(parsed.ids) ? parsed.ids.filter(Number.isFinite) : [] };
  } catch {
    return null;
  }
}

/** The stored context, but only if this ticket is part of it — opening a
 * ticket some other way (notification, link, direct URL) must not show a
 * stale "back to" from an earlier list. */
export function navContextFor(ticketId: number): TicketNavContext | null {
  const ctx = getTicketNavContext();
  if (!ctx || !ctx.ids.includes(ticketId)) return null;
  return ctx;
}

/** Neighbours of a ticket within its context list (null at either end). */
export function neighbours(
  ctx: TicketNavContext,
  ticketId: number,
): { index: number; prev: number | null; next: number | null } {
  const index = ctx.ids.indexOf(ticketId);
  return {
    index,
    prev: index > 0 ? ctx.ids[index - 1] : null,
    next: index >= 0 && index < ctx.ids.length - 1 ? ctx.ids[index + 1] : null,
  };
}
