/**
 * Queue default for the New-ticket form (`backend/src/tiqora/domain/new_ticket_queue.py`).
 * Hand-written wrappers like `./phoneApi.ts`; the shape comes from the
 * generated schema.
 *
 * Order (Decision 1 of the compact phone ticket plan): the `?queue_id=` param
 * wins (handled by the page), then the customer's last ticket, the company's
 * last ticket, the configured `QueueDefault`, and finally the first queue that
 * is not Junk/Raw/Postmaster. The backend applies everything after the param.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type QueueSuggestion = Schemas["QueueSuggestion"];
export type QueueSuggestionSource = NonNullable<QueueSuggestion["source"]>;
export type NewTicketScreen = "phone" | "email";

export const newTicketApi = {
  /** Queue for a ticket of this customer user (history, then default). */
  suggestedQueue(login: string, screen: NewTicketScreen, signal?: AbortSignal) {
    return api.request<QueueSuggestion>(
      "GET",
      `/api/v1/customers/${encodeURIComponent(login)}/suggested-queue`,
      { query: { screen }, signal },
    );
  },
  /** Queue before a customer is known, or for a ticket without one. */
  defaultQueue(screen: NewTicketScreen, signal?: AbortSignal) {
    return api.request<QueueSuggestion>("GET", "/api/v1/tickets/new/default-queue", {
      query: { screen },
      signal,
    });
  },
};
