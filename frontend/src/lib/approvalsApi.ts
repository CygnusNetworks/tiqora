/**
 * Approval endpoints for agents (`backend/src/tiqora/api/v1/approvals.py`): inbox, detail, votes,
 * forwarding, templates/lists/resolvers for the request dialog, own delegations and the
 * ticket-scoped routes. Hand-written wrappers like `./itsmApi.ts`; the shapes come from the
 * generated schema. Public link, portal and admin routes live in their own modules.
 */
import type { Schemas } from "@tiqora/api-client";
import type { TFunction } from "i18next";
import { api, ApiError } from "./api";

export type ApprovalSummary = Schemas["ApprovalSummaryOut"];
export type ApprovalDetail = Schemas["ApprovalDetailOut"];
export type ApprovalStep = Schemas["ApprovalStepOut"];
export type ApprovalApprover = Schemas["ApproverOut"];
export type ApprovalVote = Schemas["VoteOut"];
export type ApprovalEvent = Schemas["ApprovalEventOut"];
export type MyApproverRow = Schemas["MyApproverRowOut"];
export type ApprovalInboxPage = Schemas["ApprovalInboxPage"];
export type ApprovalInboxItem = Schemas["InboxItemOut"];
export type ApprovalDecisionIn = Schemas["DecisionIn"];
export type ApprovalForwardIn = Schemas["ForwardIn"];
export type ApprovalRequestIn = Schemas["ApprovalRequestIn"];
export type ApprovalTemplateOption = Schemas["ApprovalTemplateOptionOut"];
export type ApprovalListOption = Schemas["ApprovalListOptionOut"];
export type ApprovalResolverOption = Schemas["ResolverOptionOut"];
export type ApprovalDelegation = Schemas["DelegationOut"];
export type ApprovalDelegationIn = Schemas["DelegationIn"];

export type ApprovalInboxState = "pending" | "decided";
export type ApprovalsTab = "open" | "done" | "delegation";
export type ApprovalDecision = "approved" | "rejected";

const BASE = "/api/v1/approvals";
const ticket = (id: number) => `/api/v1/tickets/${id}`;

/** Page size of the inbox list. */
export const APPROVALS_PAGE_SIZE = 25;

/** React-query keys (A14); every key starts with `"approvals"` so the SSE `approval_changed` invalidates all. */
export const approvalsKeys = {
  all: () => ["approvals"] as const,
  inbox: (state: ApprovalInboxState, page: number) => ["approvals", "inbox", state, page] as const,
  status: () => ["approvals", "status"] as const,
  count: () => ["approvals", "count"] as const,
  detail: (id: number) => ["approvals", "detail", id] as const,
  templates: (subjectType?: string | null) => ["approvals", "templates", subjectType ?? null] as const,
  lists: () => ["approvals", "lists"] as const,
  resolvers: (subjectType?: string | null) => ["approvals", "resolvers", subjectType ?? null] as const,
  delegations: () => ["approvals", "delegations"] as const,
  ticket: (ticketId: number) => ["tickets", ticketId, "approvals"] as const,
};

export const approvalsApi = {
  /** Answers while the module is switched off; every other route then answers 404 `approvals_disabled`. */
  status(signal?: AbortSignal) {
    return api.request<Schemas["ApprovalsStatusOut"]>("GET", `${BASE}/status`, { signal });
  },
  inbox(query: { state?: ApprovalInboxState; limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    return api.request<ApprovalInboxPage>("GET", `${BASE}/inbox`, { query, signal });
  },
  inboxCount(signal?: AbortSignal) {
    return api.request<Schemas["InboxCountOut"]>("GET", `${BASE}/inbox/count`, { signal });
  },
  get(approvalId: number, signal?: AbortSignal) {
    return api.request<ApprovalDetail>("GET", `${BASE}/${approvalId}`, { signal });
  },
  decide(approvalId: number, body: ApprovalDecisionIn) {
    return api.request<ApprovalDetail>("POST", `${BASE}/${approvalId}/decision`, { body });
  },
  forward(approvalId: number, approverId: number, body: ApprovalForwardIn) {
    return api.request<ApprovalDetail>("POST", `${BASE}/${approvalId}/approvers/${approverId}/forward`, { body });
  },
  cancel(approvalId: number) {
    return api.request<ApprovalDetail>("POST", `${BASE}/${approvalId}/cancel`);
  },
  refreshApprovers(approvalId: number) {
    return api.request<ApprovalDetail>("POST", `${BASE}/${approvalId}/refresh-approvers`);
  },
  templates(subjectType?: string | null, signal?: AbortSignal) {
    return api.request<ApprovalTemplateOption[]>("GET", `${BASE}/templates`, {
      query: { subject_type: subjectType ?? undefined },
      signal,
    });
  },
  lists(signal?: AbortSignal) {
    return api.request<ApprovalListOption[]>("GET", `${BASE}/lists`, { signal });
  },
  resolvers(subjectType?: string | null, signal?: AbortSignal) {
    return api.request<ApprovalResolverOption[]>("GET", `${BASE}/resolvers`, {
      query: { subject_type: subjectType ?? undefined },
      signal,
    });
  },
  delegations(signal?: AbortSignal) {
    return api.request<ApprovalDelegation[]>("GET", `${BASE}/delegations`, { signal });
  },
  createDelegation(body: ApprovalDelegationIn) {
    return api.request<ApprovalDelegation>("POST", `${BASE}/delegations`, { body });
  },
  deleteDelegation(delegationId: number) {
    return api.request<void>("DELETE", `${BASE}/delegations/${delegationId}`);
  },
  ticketApprovals(ticketId: number, page: { limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    return api.request<ApprovalSummary[]>("GET", `${ticket(ticketId)}/approvals`, { query: page, signal });
  },
  requestApproval(ticketId: number, body: ApprovalRequestIn) {
    return api.request<ApprovalDetail>("POST", `${ticket(ticketId)}/approvals`, { body });
  },
};

/**
 * Every `detail.code` the approval routes can answer, with an `approvals.errors.<code>` text in
 * en and de. Keep in sync with `ERROR_STATUS` in `backend/src/tiqora/approvals/contract.py` plus
 * `rate_limited` (HTTP 429 of `POST /api/v1/approval-links/*`, `api/v1/approvals.py`);
 * `approvalsApi.test.ts` compares both lists and the locale files.
 */
export const APPROVAL_ERROR_CODES = [
  "approvals_disabled",
  "subject_unavailable",
  "approval_not_found",
  "ticket_not_found",
  "template_not_found",
  "list_not_found",
  "forbidden",
  "not_an_approver",
  "self_approval_forbidden",
  "customer_approver_outside_company",
  "delegation_not_active",
  "comment_too_long",
  "already_decided",
  "approval_closed",
  "step_not_active",
  "forward_not_allowed",
  "forward_target_invalid",
  "invalid_definition",
  "unknown_subject_type",
  "subject_not_requestable",
  "customer_approvers_not_allowed",
  "unknown_resolver",
  "link_invalid",
  "link_expired",
  "link_used",
  "invalid_member",
  "list_name_taken",
  "list_in_use",
  "template_key_taken",
  "delegation_overlap",
  "approver_invalid",
  "template_changed",
  "rate_limited",
] as const;

export type ApprovalErrorCode = (typeof APPROVAL_ERROR_CODES)[number];

const KNOWN_CODES: ReadonlySet<string> = new Set(APPROVAL_ERROR_CODES);

/** The API's `{detail: {code}}` of a failed call, or null. */
export function approvalErrorCode(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
  const code = (inner as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
}

/** Plain-language message for a failed approval call; unknown codes and non-API errors fall back to a generic text. */
export function approvalErrorMessage(err: unknown, t: TFunction): string {
  const code = approvalErrorCode(err);
  if (code && KNOWN_CODES.has(code)) return t(`approvals.errors.${code}`);
  if (err instanceof ApiError && err.status === 429) return t("approvals.errors.rate_limited");
  return t("approvals.errors.unknown");
}

/** i18n key of a step's rule sentence; the portal API sends the key itself (`rule_text_key`), the link API the plain rule. */
export function approvalRuleKey(rule: string): string {
  return `approvals.rule.${rule}`;
}
