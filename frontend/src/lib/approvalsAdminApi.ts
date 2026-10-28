/** Admin approval routes (`/api/v1/admin/approvals`, `backend/src/tiqora/api/v1/admin/approvals.py`): overview, templates, Freigabegruppen, delegations. */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type ApprovalAdminPage = Schemas["ApprovalAdminPage"];
export type ApprovalAdminItem = Schemas["ApprovalAdminItemOut"];
export type ApprovalTemplate = Schemas["ApprovalTemplateOut"];
export type ApprovalTemplateIn = Schemas["ApprovalTemplateIn"];
export type ApprovalDefinition = Schemas["ApprovalDefinition"];
export type ApprovalDefinitionCheck = Schemas["DefinitionCheckOut"];
export type ApprovalList = Schemas["ApprovalListOut"];
export type ApprovalListIn = Schemas["ApprovalListIn"];
export type AdminDelegationIn = Schemas["AdminDelegationIn"];

export type ApprovalAdminQuery = {
  status?: string;
  ticket_id?: number;
  subject_type?: string;
  overdue?: boolean;
  limit?: number;
  offset?: number;
};

const BASE = "/api/v1/admin/approvals";

export const approvalsAdminKeys = {
  overview: (query: ApprovalAdminQuery) => ["admin", "approvals", "overview", query] as const,
  templates: () => ["admin", "approvals", "templates"] as const,
  lists: () => ["admin", "approvals", "lists"] as const,
  delegations: (userId?: number | null, includeEnded?: boolean) =>
    ["admin", "approvals", "delegations", userId ?? null, !!includeEnded] as const,
};

export const approvalsAdminApi = {
  overview(query: ApprovalAdminQuery = {}, signal?: AbortSignal) {
    return api.request<ApprovalAdminPage>("GET", BASE, { query, signal });
  },
  templates(signal?: AbortSignal) {
    return api.request<ApprovalTemplate[]>("GET", `${BASE}/templates`, { signal });
  },
  createTemplate(body: ApprovalTemplateIn) {
    return api.request<ApprovalTemplate>("POST", `${BASE}/templates`, { body });
  },
  updateTemplate(templateId: number, body: ApprovalTemplateIn) {
    return api.request<ApprovalTemplate>("PUT", `${BASE}/templates/${templateId}`, { body });
  },
  deleteTemplate(templateId: number) {
    return api.request<void>("DELETE", `${BASE}/templates/${templateId}`);
  },
  validateTemplate(definition: Record<string, unknown>, subjectType?: string | null) {
    return api.request<ApprovalDefinitionCheck>("POST", `${BASE}/templates/validate`, {
      body: { definition, subject_type: subjectType ?? null },
    });
  },
  lists(signal?: AbortSignal) {
    return api.request<ApprovalList[]>("GET", `${BASE}/lists`, { signal });
  },
  createList(body: ApprovalListIn) {
    return api.request<ApprovalList>("POST", `${BASE}/lists`, { body });
  },
  updateList(listId: number, body: ApprovalListIn) {
    return api.request<ApprovalList>("PUT", `${BASE}/lists/${listId}`, { body });
  },
  deleteList(listId: number) {
    return api.request<void>("DELETE", `${BASE}/lists/${listId}`);
  },
  delegations(query: { user_id?: number; include_ended?: boolean; limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    return api.request<Schemas["DelegationOut"][]>("GET", `${BASE}/delegations`, { query, signal });
  },
  createDelegation(body: AdminDelegationIn) {
    return api.request<Schemas["DelegationOut"]>("POST", `${BASE}/delegations`, { body });
  },
  deleteDelegation(delegationId: number) {
    return api.request<void>("DELETE", `${BASE}/delegations/${delegationId}`);
  },
};
