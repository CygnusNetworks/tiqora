/**
 * Wrapper for the queue-scoped `/api/v1/ai/refine*` endpoints — the composer's
 * "Text verfeinern" action (`backend/src/tiqora/ai/refine.py`).
 *
 * Hand-written for the same reason as `./ticketAiApi.ts`: the shared
 * `@tiqora/api-client` does not expose wrapper methods for the AI routes yet,
 * even though the shapes are generated in `schema.d.ts`. The types below
 * mirror `backend/src/tiqora/api/v1/ai.py` exactly.
 *
 * Deliberately NOT ticket-scoped: the New-ticket form has no ticket yet.
 * Exactly one of `ticket_id` / `queue_id` addresses the queue policy — with a
 * ticket the server reads its queue, so the two can never disagree.
 */
import { api } from "./api";
import type { Segment } from "./replyQuote";

export type RefineTone = "standard" | "formal" | "friendly" | "concise";

export const REFINE_TONES: RefineTone[] = [
  "standard",
  "formal",
  "friendly",
  "concise",
];

/** Exactly one of `ticket_id` / `queue_id` — the server rejects both or
 * neither. `customer_user_id` only rides along with `queue_id`: without a
 * ticket the backend has no source for PII name masking, so the composer has
 * to name the customer it was opened for. */
export type RefineTarget =
  | { ticket_id: number }
  | { queue_id: number; customer_user_id?: string | null };

export type RefineRequest = RefineTarget & {
  tone: RefineTone;
  segments: Segment[];
};

export type RefineSection = { id: number; text: string };

export type RefineResponse = { sections: RefineSection[] };

export type RefineAvailability = { available: boolean };

export const refineApi = {
  refineAvailability(target: RefineTarget, signal?: AbortSignal) {
    const params = new URLSearchParams(
      "ticket_id" in target
        ? { ticket_id: String(target.ticket_id) }
        : { queue_id: String(target.queue_id) },
    );
    return api.request<RefineAvailability>(
      "GET",
      `/api/v1/ai/refine/availability?${params.toString()}`,
      { signal },
    );
  },
  refine(request: RefineRequest, signal?: AbortSignal) {
    return api.request<RefineResponse>("POST", "/api/v1/ai/refine", {
      body: request,
      signal,
    });
  },
};
