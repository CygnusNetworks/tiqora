import { isTicketSortKey, type TicketSortKey } from "@/components/searches/searchSort";

/** Address of a saved search: page and sort, so a link opens the same page of the list. */
export type SavedSearchSearch = {
  offset?: number;
  sort?: TicketSortKey;
  order?: "asc" | "desc";
};

/** Router `validateSearch` of `/agent/searches/$searchId`. */
export function validateSavedSearchSearch(s: Record<string, unknown>): SavedSearchSearch {
  const offset = typeof s.offset === "number" ? s.offset : typeof s.offset === "string" ? Number(s.offset) : NaN;
  return {
    offset: Number.isInteger(offset) && offset > 0 ? offset : undefined,
    sort: isTicketSortKey(s.sort) ? s.sort : undefined,
    order: s.order === "asc" || s.order === "desc" ? s.order : undefined,
  };
}
