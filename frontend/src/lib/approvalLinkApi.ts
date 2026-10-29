/**
 * Public decision link (`POST /api/v1/approval-links/*`, `backend/src/tiqora/api/v1/approvals.py`).
 * No session: the token from the e-mail is the credential. Never retried — a retry would spend
 * the rate limit; errors carry `detail.code` (`link_invalid`, `link_expired`, `link_used`,
 * `rate_limited`), see `approvalErrorMessage` in `./approvalsApi`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type ApprovalLinkInspect = Schemas["LinkInspectOut"];
export type ApprovalLinkDecision = Schemas["LinkDecisionOut"];

const BASE = "/api/v1/approval-links";

export const approvalLinkApi = {
  inspect(token: string, signal?: AbortSignal) {
    return api.request<ApprovalLinkInspect>("POST", `${BASE}/inspect`, { body: { token }, signal });
  },
  decide(token: string, decision: "approved" | "rejected", comment?: string) {
    return api.request<ApprovalLinkDecision>("POST", `${BASE}/decide`, {
      body: { token, decision, comment: comment ? comment : null },
    });
  },
};
