/** Customer-portal approvals (`/api/portal/approvals`, `backend/src/tiqora/api/portal/approvals.py`); 404 while the feature is off. */
import type { Schemas } from "@tiqora/api-client";
import { portalApi } from "./portalApi";

export type PortalApprovalPage = Schemas["PortalApprovalPage"];
export type PortalApprovalItem = Schemas["PortalApprovalItemOut"];
export type PortalApproval = Schemas["PortalApprovalOut"];
export type PortalApprovalDecisionIn = Schemas["PortalDecisionIn"];

const BASE = "/api/portal/approvals";

export const portalApprovalsKeys = {
  all: () => ["portal", "approvals"] as const,
  /** The nav probe: the list answering 200 shows the "Freigaben" entry. */
  available: () => ["portal", "approvals", "available"] as const,
  list: (state: string, page: number) => ["portal", "approvals", "list", state, page] as const,
  detail: (id: number) => ["portal", "approvals", "detail", id] as const,
};

export const portalApprovalsApi = {
  list(query: { state?: "pending" | "decided"; limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    return portalApi.request<PortalApprovalPage>("GET", BASE, { query, signal });
  },
  get(approvalId: number, signal?: AbortSignal) {
    return portalApi.request<PortalApproval>("GET", `${BASE}/${approvalId}`, { signal });
  },
  decide(approvalId: number, body: PortalApprovalDecisionIn) {
    return portalApi.request<PortalApproval>("POST", `${BASE}/${approvalId}/decision`, { body });
  },
};
