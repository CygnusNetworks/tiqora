/**
 * Change-management administration (`backend/src/tiqora/api/v1/admin/itsm_change.py`): change
 * templates, CAB boards and their members, risk questions, freeze windows and the Znuny change
 * archive. Hand-written wrappers like `./itsmApi.ts`; the shapes come from the generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";
import type { WithDefaults } from "./changeApi";

export type ChangeTemplate = Schemas["ChangeTemplateOut"];
export type ChangeTemplateIn = WithDefaults<Schemas["TemplateIn"], "is_standard" | "valid">;
export type ChangeTemplateTask = Schemas["TemplateTask"];
export type ChangeTemplateDefaults = Schemas["TemplateDefaults"];
export type AdminCab = Schemas["CabOut"];
export type AdminCabIn = WithDefaults<Schemas["CabIn"], "is_emergency" | "rule" | "valid">;
export type AdminCabMemberIn = Schemas["CabMemberIn"];
export type AdminCabMember = Schemas["ResolvedMemberOut"];
export type AdminRiskQuestion = Schemas["ChangeRiskQuestionOut"];
export type AdminRiskQuestionIn = WithDefaults<Schemas["ChangeRiskQuestionIn"], "valid">;
export type RiskExamples = Schemas["ChangeRiskExamplesOut"];
export type FreezeWindow = Schemas["FreezeWindowOut"];
export type FreezeWindowIn = WithDefaults<Schemas["FreezeIn"], "allow_emergency" | "enforcement" | "scope" | "valid">;
export type LegacyArchiveStatus = Schemas["LegacyArchiveStatusOut"];
export type LegacyArchiveStartIn = WithDefaults<Schemas["LegacyArchiveStartIn"], "dry_run">;
export type JobAccepted = Schemas["JobAccepted"];

const ADMIN = "/api/v1/admin/itsm/change";

export const changeAdminKeys = {
  all: () => ["admin", "itsm", "change"] as const,
  templates: () => ["admin", "itsm", "change", "templates"] as const,
  template: (id: number) => ["admin", "itsm", "change", "templates", id] as const,
  cabs: () => ["admin", "itsm", "change", "cabs"] as const,
  riskQuestions: () => ["admin", "itsm", "change", "risk-questions"] as const,
  freezeWindows: () => ["admin", "itsm", "change", "freeze-windows"] as const,
  legacyArchive: () => ["admin", "itsm", "change", "legacy-archive"] as const,
};

export const changeAdminApi = {
  templates(signal?: AbortSignal) {
    return api.request<ChangeTemplate[]>("GET", `${ADMIN}/templates`, { signal });
  },
  template(id: number, signal?: AbortSignal) {
    return api.request<ChangeTemplate>("GET", `${ADMIN}/templates/${id}`, { signal });
  },
  createTemplate(body: ChangeTemplateIn) {
    return api.request<ChangeTemplate>("POST", `${ADMIN}/templates`, { body });
  },
  saveTemplate(id: number, body: ChangeTemplateIn) {
    return api.request<ChangeTemplate>("PUT", `${ADMIN}/templates/${id}`, { body });
  },
  deleteTemplate(id: number) {
    return api.request<void>("DELETE", `${ADMIN}/templates/${id}`);
  },

  cabs(signal?: AbortSignal) {
    return api.request<AdminCab[]>("GET", `${ADMIN}/cabs`, { signal });
  },
  createCab(body: AdminCabIn) {
    return api.request<AdminCab>("POST", `${ADMIN}/cabs`, { body });
  },
  saveCab(id: number, body: AdminCabIn) {
    return api.request<AdminCab>("PUT", `${ADMIN}/cabs/${id}`, { body });
  },
  deleteCab(id: number) {
    return api.request<void>("DELETE", `${ADMIN}/cabs/${id}`);
  },
  saveCabMembers(id: number, members: AdminCabMemberIn[]) {
    return api.request<AdminCab>("PUT", `${ADMIN}/cabs/${id}/members`, { body: members });
  },

  riskQuestions(signal?: AbortSignal) {
    return api.request<AdminRiskQuestion[]>("GET", `${ADMIN}/risk-questions`, { signal });
  },
  createRiskQuestion(body: AdminRiskQuestionIn) {
    return api.request<AdminRiskQuestion>("POST", `${ADMIN}/risk-questions`, { body });
  },
  saveRiskQuestion(id: number, body: AdminRiskQuestionIn) {
    return api.request<AdminRiskQuestion>("PUT", `${ADMIN}/risk-questions/${id}`, { body });
  },
  deleteRiskQuestion(id: number) {
    return api.request<void>("DELETE", `${ADMIN}/risk-questions/${id}`);
  },
  orderRiskQuestions(order: number[]) {
    return api.request<AdminRiskQuestion[]>("PUT", `${ADMIN}/risk-questions/order`, { body: { order } });
  },
  /** Creates the example questionnaire (only when none exists yet). */
  createRiskExamples() {
    return api.request<RiskExamples>("POST", `${ADMIN}/risk-questions/examples`);
  },

  freezeWindows(signal?: AbortSignal) {
    return api.request<FreezeWindow[]>("GET", `${ADMIN}/freeze-windows`, { signal });
  },
  createFreezeWindow(body: FreezeWindowIn) {
    return api.request<FreezeWindow>("POST", `${ADMIN}/freeze-windows`, { body });
  },
  saveFreezeWindow(id: number, body: FreezeWindowIn) {
    return api.request<FreezeWindow>("PUT", `${ADMIN}/freeze-windows/${id}`, { body });
  },
  deleteFreezeWindow(id: number) {
    return api.request<void>("DELETE", `${ADMIN}/freeze-windows/${id}`);
  },

  legacyArchive(signal?: AbortSignal) {
    return api.request<LegacyArchiveStatus>("GET", `${ADMIN}/legacy-archive`, { signal });
  },
  /** Starts the archive job (`dry_run` only counts); poll `GET /api/v1/jobs/{id}`. */
  startLegacyArchive(body: LegacyArchiveStartIn = {}) {
    return api.request<JobAccepted>("POST", `${ADMIN}/legacy-archive`, { body });
  },
};
