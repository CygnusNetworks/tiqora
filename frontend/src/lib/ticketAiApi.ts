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

export type AiToolTraceOut = {
  name: string;
  content: string;
  /** JSON the tool was called with; null on traces recorded before it was kept. */
  arguments?: string | null;
};

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
  tool_trace: AiToolTraceOut[];
};

/** Manual Assist background-run status (nginx-90s-timeout fix — the draft
 * POST returns immediately and the frontend polls this instead of the
 * response). ``null`` when no manual run has ever been started for this
 * ticket. */
export type ManualRunStatus =
  | "running"
  | "drafted"
  | "skipped"
  | "escalated"
  // The agent ended the run on purpose without customer text — advertising, a
  // newsletter, nothing to act on (backend: STATUS_NO_REPLY / the
  // no_reply_needed tool).
  | "no_reply"
  | "superseded"
  | "error";

export type AiStateOut = {
  manual_assist_available: boolean;
  summary_available: boolean;
  can_summarize: boolean;
  operation_mode_ready: boolean;
  drafts: AiDraftOut[];
  summary_body: string | null;
  last_summary_upto_article_id: number | null;
  summary_created_at: string | null;
  manual_run_status?: ManualRunStatus | null;
  manual_run_notes?: string | null;
  manual_run_error_code?: string | null;
  manual_run_started_at?: string | null;
  ai_escalated_at?: string | null;
  /** Pending triage proposal, only present while its status is "open". */
  triage?: AiTriageOut | null;
};

/**
 * A pending triage proposal. The queue half and the customer half are
 * independent -- either can be absent, and an agent accepts them separately.
 */
export type AiTriageOut = {
  id: number;
  status: string;
  source_queue_id: number;
  suggested_queue_id?: number | null;
  suggested_queue_name?: string | null;
  queue_confidence?: number | null;
  queue_reason?: string | null;
  /** How many of the samples voted for the winning queue. */
  queue_votes?: number | null;
  extracted_email?: string | null;
  suggested_customer_user_id?: string | null;
  suggested_customer_name?: string | null;
  customer_confidence?: number | null;
  created_at?: string | null;
};

export type SummaryDetail = "standard" | "detailed";

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
    return api.request<AiStateOut>("GET", `/api/v1/tickets/${ticketId}/ai`, {
      signal,
    });
  },
  requestDraft(ticketId: number, signal?: AbortSignal) {
    return api.request<AiDraftRequestOut>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/draft`,
      {
        signal,
      },
    );
  },
  summarize(ticketId: number, detail?: SummaryDetail, signal?: AbortSignal) {
    return api.request<AiSummarizeOut>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/summarize`,
      {
        body: detail ? { detail } : undefined,
        signal,
      },
    );
  },
  discardDraft(ticketId: number, draftId: number, signal?: AbortSignal) {
    return api.request<void>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/drafts/${draftId}/discard`,
      { signal },
    );
  },
  resume(ticketId: number, signal?: AbortSignal) {
    return api.request<void>("POST", `/api/v1/tickets/${ticketId}/ai/resume`, {
      signal,
    });
  },
  /**
   * Apply a triage proposal. Runs with the agent's own permissions, so this
   * can 403 on a target queue the worker itself would have been allowed to
   * move into (see the backend route docstring).
   */
  acceptTriage(
    ticketId: number,
    triageId: number,
    parts: { queue: boolean; customer: boolean },
    signal?: AbortSignal,
  ) {
    return api.request<void>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/triage/${triageId}/accept`,
      { body: parts, signal },
    );
  },
  rejectTriage(
    ticketId: number,
    triageId: number,
    note?: string,
    signal?: AbortSignal,
  ) {
    return api.request<void>(
      "POST",
      `/api/v1/tickets/${ticketId}/ai/triage/${triageId}/reject`,
      { body: { note: note ?? null }, signal },
    );
  },
};
