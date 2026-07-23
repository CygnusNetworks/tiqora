/**
 * Wrappers for the agent-facing `/api/v1/tickets/{ticket_id}/ai/*` endpoints
 * (Tiqora AI subsystem, Phase B/C — see `~/TIQORA_LLM_PLAN.md` §3.4/§3.5).
 *
 * Hand-written for the same reason as `./aiApi.ts` (the admin-facing
 * counterpart): this page set is built in a worktree scoped to
 * `frontend/src/` only, and `packages/api-client/src/client.ts` does not yet
 * expose wrapper methods for these routes even though the response shapes
 * are already generated in `schema.d.ts` (`AiStateOut`, `AiDraftOut`,
 * `AiDraftRequestOut`, `AiSummarizeOut`). The types below mirror
 * `backend/src/tiqora/api/v1/ai.py` exactly; once the shared client picks up
 * bindings for these routes, callers can switch to `api.getTicketAi` etc.
 * without changing call sites here.
 */
import { api } from "./api";

export type AiDraftKind = "reply" | "clarify";
export type AiDraftStatus = "open" | "accepted" | "discarded";
export type AiDraftSource = "auto" | "manual";

export type AiDraftOut = {
  id: number;
  ticket_id: number;
  kind: string;
  subject: string | null;
  body: string;
  based_on_article_id: number | null;
  status: string;
  source: string;
  accepted_article_id: number | null;
  create_time: string;
};

export type AiStateOut = {
  manual_assist_available: boolean;
  summary_available: boolean;
  can_summarize: boolean;
  operation_mode_ready: boolean;
  drafts: AiDraftOut[];
  summary_body: string | null;
  last_summary_upto_article_id: number | null;
  summary_created_at: string | null;
};

export type AiSummarizeOut = {
  status: string;
  summary_body?: string | null;
  upto_article_id?: number | null;
};

export type AiDraftRequestOut = {
  status: string;
  draft_id?: number | null;
  article_id?: number | null;
  notes?: string | null;
};

export const ticketAiApi = {
  getState(ticketId: number, signal?: AbortSignal) {
    return api.request<AiStateOut>("GET", `/api/v1/tickets/${ticketId}/ai`, { signal });
  },
  requestDraft(ticketId: number, signal?: AbortSignal) {
    return api.request<AiDraftRequestOut>("POST", `/api/v1/tickets/${ticketId}/ai/draft`, {
      signal,
    });
  },
  summarize(ticketId: number, signal?: AbortSignal) {
    return api.request<AiSummarizeOut>("POST", `/api/v1/tickets/${ticketId}/ai/summarize`, {
      signal,
    });
  },
  discardDraft(ticketId: number, draftId: number, signal?: AbortSignal) {
    return api.request<void>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/drafts/${draftId}/discard`,
      { signal },
    );
  },
};
