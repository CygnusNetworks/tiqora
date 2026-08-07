/**
 * Wrappers for `/api/v1/tickets/{ticket_id}/drafts*` (`tiqora_form_draft`).
 *
 * Hand-written for the same reason as `./ticketAiApi.ts`: the generated
 * client in `packages/api-client` has no bindings for these routes yet.
 * The types mirror `DraftIn`/`DraftOut` in
 * `backend/src/tiqora/api/v1/tickets.py`.
 *
 * Note this is *not* Znuny's `form_draft` table — that one holds Perl
 * Storable blobs. `tiqora_form_draft` is Tiqora-owned and stores JSON.
 */
import { api } from "./api";

/** The only action Tiqora writes today; the column is free-form. */
export const COMPOSE_ACTION = "AgentTicketCompose";

export type FormDraftOut = {
  id: number;
  ticket_id: number;
  user_id: number;
  action: string;
  article_id: number | null;
  title: string | null;
  /** JSON blob — see `ReplyDraftContent` in `./replyDrafts`. */
  content: string;
  created: string;
  changed: string;
};

export type FormDraftIn = {
  action: string;
  article_id: number | null;
  title?: string | null;
  content: string;
};

export const formDraftApi = {
  list(ticketId: number, signal?: AbortSignal) {
    return api.request<FormDraftOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/drafts`,
      { signal },
    );
  },
  upsert(ticketId: number, body: FormDraftIn, signal?: AbortSignal) {
    return api.request<FormDraftOut>(
      "PUT",
      `/api/v1/tickets/${ticketId}/drafts/${encodeURIComponent(body.action)}`,
      { body, signal },
    );
  },
  remove(
    ticketId: number,
    action: string,
    articleId: number | null,
    signal?: AbortSignal,
  ) {
    return api.request<void>(
      "DELETE",
      `/api/v1/tickets/${ticketId}/drafts/${encodeURIComponent(action)}`,
      // Omitted (not null) so the backend falls back to the ticket-wide row.
      { query: articleId === null ? undefined : { article_id: articleId }, signal },
    );
  },
};
