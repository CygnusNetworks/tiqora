import type { SortKey } from "@/components/agent/TicketTable";
import type { SavedSearch } from "@/lib/savedSearchesApi";

/** The sort keys `POST /tickets/query` and a saved search understand (backend `TicketSortKey`). */
export type TicketSortKey = SavedSearch["sort_by"];

export const TICKET_SORT_KEYS: readonly TicketSortKey[] = [
  "activity",
  "changed",
  "created",
  "age",
  "tn",
  "title",
  "priority",
  "deadline",
];

export function isTicketSortKey(v: unknown): v is TicketSortKey {
  return typeof v === "string" && (TICKET_SORT_KEYS as readonly string[]).includes(v);
}

/**
 * The queue list's sort key as a backend key. The list also sorts by state, owner and customer,
 * which the backend does not know (it would silently fall back to creation time or answer 422),
 * so those three stay on the list's default, last activity.
 */
export function toTicketSortKey(sort: SortKey | string | undefined): TicketSortKey {
  return isTicketSortKey(sort) ? sort : "activity";
}

/** The sort key `TicketTable` can highlight for a backend key (it has no "created" column). */
export function toTableSortKey(sort: TicketSortKey): SortKey {
  return sort === "created" ? "age" : sort;
}
