/**
 * Saved ticket searches (`backend/src/tiqora/api/v1/saved_searches.py`), the
 * structured ticket query behind them (`POST /tickets/query`) and the filter
 * helpers (`/ticket-filters/*`). Hand-written wrappers like `./reportsApi.ts`;
 * the shapes come from the generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type SavedSearch = Schemas["SavedSearchOut"];
export type SavedSearchIn = Schemas["SavedSearchIn"];
export type SavedSearchUpdateIn = Schemas["SavedSearchUpdateIn"];
export type SavedSearchPrefsIn = Schemas["SavedSearchPrefsIn"];
export type ZnunyImportPreview = Schemas["ZnunyImportPreviewOut"];
export type ZnunyImportResult = Schemas["ZnunyImportResultOut"];
export type TicketQueryIn = Schemas["TicketQueryIn"];
export type TicketFilter = Schemas["Filter"];
export type FilterFieldOut = Schemas["FilterFieldOut"];
export type PaginatedTickets = Schemas["PaginatedTickets"];

export const savedSearchesApi = {
  list(signal?: AbortSignal) {
    return api.request<SavedSearch[]>("GET", "/api/v1/saved-searches", { signal });
  },
  get(id: number, signal?: AbortSignal) {
    return api.request<SavedSearch>("GET", `/api/v1/saved-searches/${id}`, { signal });
  },
  create(body: SavedSearchIn) {
    return api.request<SavedSearch>("POST", "/api/v1/saved-searches", { body });
  },
  /** Full replace; `body.version` is the version the editor loaded (409 on a stale one). */
  update(id: number, body: SavedSearchUpdateIn) {
    return api.request<SavedSearch>("PUT", `/api/v1/saved-searches/${id}`, { body });
  },
  /** The caller's own sidebar settings; allowed on a search shared with them. */
  setPrefs(id: number, body: SavedSearchPrefsIn) {
    return api.request<SavedSearch>("PUT", `/api/v1/saved-searches/${id}/prefs`, { body });
  },
  remove(id: number) {
    return api.request<void>("DELETE", `/api/v1/saved-searches/${id}`);
  },
  /** Match counts by search id; `null` where a count timed out or failed. */
  counts(signal?: AbortSignal) {
    return api.request<Schemas["SavedSearchCountsOut"]>("GET", "/api/v1/saved-searches/counts", { signal });
  },
  znunyPreview(signal?: AbortSignal) {
    return api.request<ZnunyImportPreview>("GET", "/api/v1/saved-searches/import/znuny", { signal });
  },
  znunyImport(names: string[]) {
    return api.request<ZnunyImportResult>("POST", "/api/v1/saved-searches/import/znuny", {
      body: { names },
    });
  },
  queryTickets(body: Partial<TicketQueryIn> & { filter: TicketFilter }, signal?: AbortSignal) {
    return api.request<PaginatedTickets>("POST", "/api/v1/tickets/query", { body, signal });
  },
  countTickets(filter: TicketFilter, signal?: AbortSignal) {
    return api.request<{ count: number }>("POST", "/api/v1/ticket-filters/count", {
      body: { filter },
      signal,
    });
  },
  ticketFilterFields(signal?: AbortSignal) {
    return api.request<FilterFieldOut[]>("GET", "/api/v1/ticket-filters/fields", { signal });
  },
};
