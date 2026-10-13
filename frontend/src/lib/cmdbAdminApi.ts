/** Admin CMDB endpoints (`backend/src/tiqora/api/v1/admin/cmdb.py`). Link types live on the ITSM page (R2). */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";
import type { CatalogItem, CmdbClass, CmdbDefinition, JobAccepted } from "./cmdbApi";

export type CmdbAdminStatus = Schemas["CmdbAdminStatusOut"];
export type CatalogItemIn = Schemas["CatalogItemIn"];
export type CmdbClassIn = Schemas["CmdbClassIn"];
export type CmdbClassUpdateIn = Schemas["CmdbClassUpdateIn"];
export type CmdbDefinitionIn = Schemas["CmdbDefinitionIn"];
export type DefinitionValidateIn = Schemas["DefinitionValidateIn"];
export type DefinitionIssue = Schemas["DefinitionIssueOut"];
export type DefinitionChange = Schemas["DefinitionChangeOut"];
export type DefinitionImportIn = Schemas["DefinitionImportIn"];
export type DefinitionImportOut = Schemas["DefinitionImportOut"];
export type ClassTemplate = Schemas["ClassTemplateOut"];

const BASE = "/api/v1/admin/cmdb";

export const cmdbAdminApi = {
  status() {
    return api.request<CmdbAdminStatus>("GET", `${BASE}/status`);
  },
  seedDefaults() {
    return api.request<CmdbAdminStatus>("POST", `${BASE}/seed-defaults`);
  },
  catalogClasses() {
    return api.request<string[]>("GET", `${BASE}/catalog-classes`);
  },
  catalog(catalogClass?: string) {
    return api.request<CatalogItem[]>("GET", `${BASE}/catalog`, { query: { catalog_class: catalogClass } });
  },
  createCatalogItem(body: CatalogItemIn) {
    return api.request<CatalogItem>("POST", `${BASE}/catalog`, { body });
  },
  updateCatalogItem(id: number, body: CatalogItemIn) {
    return api.request<CatalogItem>("PUT", `${BASE}/catalog/${id}`, { body });
  },
  classes() {
    return api.request<CmdbClass[]>("GET", `${BASE}/classes`);
  },
  getClass(id: number) {
    return api.request<CmdbClass>("GET", `${BASE}/classes/${id}`);
  },
  createClass(body: CmdbClassIn) {
    return api.request<CmdbClass>("POST", `${BASE}/classes`, { body });
  },
  updateClass(id: number, body: CmdbClassUpdateIn) {
    return api.request<CmdbClass>("PUT", `${BASE}/classes/${id}`, { body });
  },
  definitions(classId: number) {
    return api.request<CmdbDefinition[]>("GET", `${BASE}/classes/${classId}/definitions`);
  },
  addDefinition(classId: number, body: CmdbDefinitionIn) {
    return api.request<CmdbDefinition>("POST", `${BASE}/classes/${classId}/definitions`, { body });
  },
  definitionDiff(classId: number, fromVersion: number, toVersion: number) {
    return api.request<DefinitionChange[]>("GET", `${BASE}/classes/${classId}/definitions/diff`, {
      query: { from_version: fromVersion, to_version: toVersion },
    });
  },
  validateDefinition(body: DefinitionValidateIn) {
    return api.request<DefinitionIssue[]>("POST", `${BASE}/definitions/validate`, { body });
  },
  importDefinition(body: DefinitionImportIn) {
    return api.request<DefinitionImportOut>("POST", `${BASE}/definition-import`, { body });
  },
  classTemplates() {
    return api.request<ClassTemplate[]>("GET", `${BASE}/class-templates`);
  },
  applyClassTemplate(key: string, groupId: number) {
    return api.request<CmdbClass>("POST", `${BASE}/class-templates/${encodeURIComponent(key)}/apply`, {
      body: { group_id: groupId },
    });
  },
  recomputePropagation() {
    return api.request<JobAccepted>("POST", `${BASE}/propagation/recompute`);
  },
};
