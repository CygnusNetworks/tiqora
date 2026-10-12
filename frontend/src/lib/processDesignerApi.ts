/**
 * Process designer endpoints (`/api/v1/admin/process-designer`) typed from the generated schema.
 * Hand-written wrapper like `./processApi.ts`. Everything here edits drafts; nothing is live
 * until somebody presses "Bereitstellen" (`deployStatusApi.deploy`).
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type DesignerEntityType = Schemas["DesignerEntityOut"]["entity_type"];
/** The four entity types that live below a process (a process has its own routes). */
export type SubEntityType = Exclude<DesignerEntityType, "Process">;

export type DesignerCatalog = Schemas["DesignerCatalogOut"];
export type CatalogAction = Schemas["CatalogActionOut"];
export type CatalogParam = Schemas["CatalogParamOut"];
export type CatalogField = Schemas["CatalogFieldOut"];
export type DesignerEntity = Schemas["DesignerEntityOut"];
export type ProcessDraft = Schemas["ProcessDraftOut"];
export type ProcessDraftSummary = Schemas["ProcessDraftSummaryOut"];
export type EntitySummary = Schemas["EntitySummaryOut"];
export type ProcessCreateIn = Schemas["ProcessCreateIn"];
export type ProcessUpdateIn = Schemas["ProcessUpdateIn"];
export type EntityCreateIn = Schemas["EntityCreateIn"];
export type EntityUpdateIn = Schemas["EntityUpdateIn"];
export type ValidationIssue = Schemas["ValidationIssueOut"];
export type ProcessImportOut = Schemas["ProcessImportOut"];
export type DesignerRevision = Schemas["RevisionOut"];
export type RevisionDiff = Schemas["RevisionDiffOut"];
export type EntityChange = Schemas["EntityChangeOut"];

const BASE = "/api/v1/admin/process-designer";
const enc = encodeURIComponent;

export const processDesignerApi = {
  catalog(signal?: AbortSignal) {
    return api.request<DesignerCatalog>("GET", `${BASE}/catalog`, { signal });
  },
  listProcesses(signal?: AbortSignal) {
    return api.request<ProcessDraftSummary[]>("GET", `${BASE}/processes`, { signal });
  },
  listEntities(entityType: SubEntityType, signal?: AbortSignal) {
    return api.request<EntitySummary[]>("GET", `${BASE}/entities/${entityType}`, { signal });
  },
  getProcess(processEntityId: string, signal?: AbortSignal) {
    return api.request<ProcessDraft>("GET", `${BASE}/processes/${enc(processEntityId)}`, { signal });
  },
  createProcess(body: ProcessCreateIn) {
    return api.request<ProcessDraft>("POST", `${BASE}/processes`, { body });
  },
  updateProcess(processEntityId: string, body: ProcessUpdateIn) {
    return api.request<ProcessDraft>("PUT", `${BASE}/processes/${enc(processEntityId)}`, { body });
  },
  copyProcess(processEntityId: string) {
    return api.request<ProcessDraft>("POST", `${BASE}/processes/${enc(processEntityId)}/copy`);
  },
  deleteProcess(processEntityId: string) {
    return api.request<void>("DELETE", `${BASE}/processes/${enc(processEntityId)}`);
  },
  createEntity(entityType: SubEntityType, body: EntityCreateIn) {
    return api.request<DesignerEntity>("POST", `${BASE}/entities/${entityType}`, { body });
  },
  updateEntity(entityType: SubEntityType, entityId: string, body: EntityUpdateIn) {
    return api.request<DesignerEntity>("PUT", `${BASE}/entities/${entityType}/${enc(entityId)}`, { body });
  },
  deleteEntity(entityType: SubEntityType, entityId: string) {
    return api.request<void>("DELETE", `${BASE}/entities/${entityType}/${enc(entityId)}`);
  },
  validation(processEntityId: string, signal?: AbortSignal) {
    return api.request<ValidationIssue[]>("GET", `${BASE}/processes/${enc(processEntityId)}/validation`, {
      signal,
    });
  },
  /**
   * Plain URL of the Znuny process file. Used as `<a href download>` with cookie authentication
   * (like the GDPR and attachment downloads); `api.request` would return text for a non-JSON body.
   */
  exportUrl(processEntityId: string) {
    return `${BASE}/processes/${enc(processEntityId)}/export`;
  },
  importFile(content: string, overwriteExisting = false) {
    return api.request<ProcessImportOut>("POST", `${BASE}/import`, {
      body: { content, overwrite_existing: overwriteExisting },
    });
  },
  revisions(signal?: AbortSignal) {
    return api.request<DesignerRevision[]>("GET", `${BASE}/revisions`, { signal });
  },
  revisionDiff(revisionId: number, opts?: { against?: number; process?: string }, signal?: AbortSignal) {
    return api.request<RevisionDiff>("GET", `${BASE}/revisions/${revisionId}/diff`, {
      query: opts ? { against: opts.against, process: opts.process } : undefined,
      signal,
    });
  },
  restoreRevision(revisionId: number, processEntityId: string) {
    return api.request<ProcessDraft>("POST", `${BASE}/revisions/${revisionId}/restore`, {
      body: { process_entity_id: processEntityId },
    });
  },
};
