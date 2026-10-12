/**
 * Agent CMDB endpoints (`backend/src/tiqora/api/v1/cmdb/`, API-key area `cmdb`). Hand-written
 * wrappers like `./dashboardsApi.ts`; the shapes come from the generated schema.
 */
import { useQuery } from "@tanstack/react-query";
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type CmdbStatus = Schemas["CmdbStatusOut"];
export type CmdbMode = CmdbStatus["mode"];
export type Ci = Schemas["CiOut"];
export type CiListItem = Schemas["CiListItemOut"];
export type CiPage = Schemas["CiPage"];
export type CiRef = Schemas["CiRefOut"];
export type CiSearchIn = Schemas["CiSearchIn"];
export type CiSearchCriteria = Schemas["CiSearchCriteria"];
export type CiCreateIn = Schemas["CiCreateIn"];
export type CiUpdateIn = Schemas["CiUpdateIn"];
export type CiSaveOut = Schemas["CiSaveOut"];
export type CiVersion = Schemas["CiVersionOut"];
export type CiVersionPage = Schemas["CiVersionPage"];
export type CiVersionSummary = Schemas["CiVersionSummaryOut"];
export type CiDiff = Schemas["CiDiffOut"];
export type CiHistoryPage = Schemas["CiHistoryPage"];
export type CiTicket = Schemas["CiTicketOut"];
export type CmdbClass = Schemas["CmdbClassOut"];
export type CmdbDefinition = Schemas["CmdbDefinitionOut"];
export type AttributeDef = Schemas["AttributeDef-Output"];
export type RenderedAttr = Schemas["RenderedAttrOut"];
export type CatalogItem = Schemas["CatalogItemOut"];
export type FormOptions = Schemas["CmdbFormOptionsOut"];
export type FilterField = Schemas["FilterFieldOut"];
export type NamedRef = Schemas["CmdbNamedRefOut"];
export type LinkOut = Schemas["LinkOut"];
export type LinkIn = Schemas["LinkIn"];
export type LinkType = Schemas["LinkTypeOut"];
export type ImpactGraph = Schemas["ImpactGraphOut"];
export type Affected = Schemas["AffectedOut"];
export type CmdbAttachment = Schemas["CmdbAttachmentOut"];
export type CmdbSavedSearch = Schemas["CmdbSavedSearchOut"];
export type CmdbSavedSearchIn = Schemas["CmdbSavedSearchIn"];
export type ImportTemplate = Schemas["ImportTemplateOut"];
export type ImportTemplateIn = Schemas["ImportTemplateIn"];
export type CmdbExportIn = Schemas["CmdbExportIn"];
export type CmdbBulkIn = Schemas["CmdbBulkIn"];
export type JobAccepted = Schemas["JobAccepted"];
/** The `request` form field of `POST /cmdb/import-jobs` (`CmdbImportRequestIn`). */
export type CmdbImportRequest = {
  class_id?: number | null;
  template_id?: number | null;
  file_format?: "csv" | "xlsx" | null;
  options?: Schemas["ImportOptions"] | null;
  mapping?: Schemas["MappingRowIn"][] | null;
  dry_run?: boolean;
  time_zone?: string;
};

const BASE = "/api/v1/cmdb";

export const cmdbApi = {
  status(signal?: AbortSignal) {
    return api.request<CmdbStatus>("GET", `${BASE}/status`, { signal });
  },
  classes() {
    return api.request<CmdbClass[]>("GET", `${BASE}/classes`);
  },
  definition(classId: number, version: number) {
    return api.request<CmdbDefinition>("GET", `${BASE}/classes/${classId}/definitions/${version}`);
  },
  catalog(catalogClass: string) {
    return api.request<CatalogItem[]>("GET", `${BASE}/catalog`, { query: { catalog_class: catalogClass } });
  },
  formOptions(classId: number) {
    return api.request<FormOptions>("GET", `${BASE}/form-options`, { query: { class_id: classId } });
  },
  filterFields(classId?: number) {
    return api.request<FilterField[]>("GET", `${BASE}/filter-fields`, { query: { class_id: classId } });
  },
  companies(q: string, limit = 20) {
    return api.request<NamedRef[]>("GET", `${BASE}/companies/lookup`, { query: { q, limit } });
  },
  search(body: CiSearchIn) {
    return api.request<CiPage>("POST", `${BASE}/cis/search`, { body });
  },
  lookup(q: string, classKeys?: string[], limit = 20) {
    return api.request<CiRef[]>("GET", `${BASE}/cis/lookup`, {
      query: { q, class_keys: classKeys, limit },
    });
  },
  create(body: CiCreateIn) {
    return api.request<Ci>("POST", `${BASE}/cis`, { body });
  },
  get(id: number) {
    return api.request<Ci>("GET", `${BASE}/cis/${id}`);
  },
  update(id: number, body: CiUpdateIn) {
    return api.request<CiSaveOut>("PUT", `${BASE}/cis/${id}`, { body });
  },
  remove(id: number) {
    return api.request<void>("DELETE", `${BASE}/cis/${id}`);
  },
  versions(id: number, query: { before_id?: number; limit?: number; include_archived?: boolean } = {}) {
    return api.request<CiVersionPage>("GET", `${BASE}/cis/${id}/versions`, { query });
  },
  version(id: number, versionId: number) {
    return api.request<CiVersion>("GET", `${BASE}/cis/${id}/versions/${versionId}`);
  },
  diff(id: number, fromVersion: number, toVersion: number) {
    return api.request<CiDiff>("GET", `${BASE}/cis/${id}/diff`, {
      query: { from_version: fromVersion, to_version: toVersion },
    });
  },
  history(id: number, query: { before_id?: number; limit?: number } = {}) {
    return api.request<CiHistoryPage>("GET", `${BASE}/cis/${id}/history`, { query });
  },
  links(id: number) {
    return api.request<LinkOut[]>("GET", `${BASE}/cis/${id}/links`);
  },
  tickets(id: number, openOnly = false) {
    return api.request<CiTicket[]>("GET", `${BASE}/cis/${id}/tickets`, { query: { open_only: openOnly } });
  },
  impact(id: number, query: { depth?: number; direction?: string } = {}) {
    return api.request<ImpactGraph>("GET", `${BASE}/cis/${id}/impact`, { query });
  },
  affected(id: number) {
    return api.request<Affected>("GET", `${BASE}/cis/${id}/affected`);
  },
  addLink(body: LinkIn) {
    return api.request<LinkOut>("POST", `${BASE}/links`, { body });
  },
  removeLink(id: number) {
    return api.request<void>("DELETE", `${BASE}/links/${id}`);
  },
  linkTypes() {
    return api.request<LinkType[]>("GET", `${BASE}/link-types`);
  },
  uploadAttachment(file: File, ciId?: number | null) {
    const form = new FormData();
    form.append("file", file);
    if (ciId != null) form.append("ci_id", String(ciId));
    return api.request<CmdbAttachment>("POST", `${BASE}/attachments`, { body: form });
  },
  attachmentUrl(id: number): string {
    return `${BASE}/attachments/${id}`;
  },
  removeAttachment(id: number) {
    return api.request<void>("DELETE", `${BASE}/attachments/${id}`);
  },
  savedSearches() {
    return api.request<CmdbSavedSearch[]>("GET", `${BASE}/saved-searches`);
  },
  createSavedSearch(body: CmdbSavedSearchIn) {
    return api.request<CmdbSavedSearch>("POST", `${BASE}/saved-searches`, { body });
  },
  updateSavedSearch(id: number, body: CmdbSavedSearchIn) {
    return api.request<CmdbSavedSearch>("PUT", `${BASE}/saved-searches/${id}`, { body });
  },
  removeSavedSearch(id: number) {
    return api.request<void>("DELETE", `${BASE}/saved-searches/${id}`);
  },
  importTemplates(classId?: number) {
    return api.request<ImportTemplate[]>("GET", `${BASE}/import-templates`, { query: { class_id: classId } });
  },
  createImportTemplate(body: ImportTemplateIn) {
    return api.request<ImportTemplate>("POST", `${BASE}/import-templates`, { body });
  },
  updateImportTemplate(id: number, body: ImportTemplateIn) {
    return api.request<ImportTemplate>("PUT", `${BASE}/import-templates/${id}`, { body });
  },
  removeImportTemplate(id: number) {
    return api.request<void>("DELETE", `${BASE}/import-templates/${id}`);
  },
  startImport(file: File, request: CmdbImportRequest) {
    const form = new FormData();
    form.append("file", file);
    form.append("request", JSON.stringify(request));
    return api.request<JobAccepted>("POST", `${BASE}/import-jobs`, { body: form });
  },
  startExport(body: CmdbExportIn) {
    return api.request<JobAccepted>("POST", `${BASE}/export-jobs`, { body });
  },
  startBulk(body: CmdbBulkIn) {
    return api.request<JobAccepted>("POST", `${BASE}/bulk-jobs`, { body });
  },
};

export const cmdbStatusKey = ["cmdb", "status"] as const;

/** The CMDB mode and the caller's reach; drives the nav entry and the mode notice. */
export function useCmdbStatus() {
  return useQuery({
    queryKey: cmdbStatusKey,
    queryFn: ({ signal }) => cmdbApi.status(signal),
    staleTime: 60_000,
  });
}

export const cmdbSavedSearchesKey = ["cmdb", "saved-searches"] as const;

/** The caller's own and the shared saved CI searches. */
export function useCmdbSavedSearches() {
  return useQuery({ queryKey: cmdbSavedSearchesKey, queryFn: () => cmdbApi.savedSearches(), staleTime: 30_000 });
}
