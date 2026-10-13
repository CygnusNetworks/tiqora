/**
 * Znuny ITSM importer endpoints (`backend/src/tiqora/api/v1/admin/itsm_import.py`). Hand-written
 * wrappers like `./itsmApi.ts`; the shapes come from the generated schema.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type ImportStatus = Schemas["ItsmImportStatusOut"];
export type ImportRunOut = Schemas["ItsmImportRunOut"];
export type ImportIssue = Schemas["ItsmImportIssueOut"];
export type ImportStepCounts = Schemas["ItsmImportStepCountsOut"];
export type ImportSwitchIn = Schemas["ItsmImportSwitchIn"];
export type ImportRollbackIn = Schemas["ItsmImportRollbackIn"];
export type ImportJobAccepted = Schemas["JobAccepted"];

const BASE = "/api/v1/admin/itsm/import";

export const itsmImportKeys = {
  status: () => ["admin", "itsm-import", "status"] as const,
  runs: () => ["admin", "itsm-import", "runs"] as const,
  run: (id: number) => ["admin", "itsm-import", "runs", id] as const,
};

export const itsmImportApi = {
  status(signal?: AbortSignal) {
    return api.request<ImportStatus>("GET", `${BASE}/status`, { signal });
  },
  /** `dryRun` true = Probelauf (writes nothing), false = real import. */
  startRun(dryRun: boolean) {
    return api.request<ImportJobAccepted>("POST", `${BASE}/runs`, { body: { dry_run: dryRun } });
  },
  runs(signal?: AbortSignal) {
    return api.request<ImportRunOut[]>("GET", `${BASE}/runs`, { signal });
  },
  run(id: number, signal?: AbortSignal) {
    return api.request<ImportRunOut>("GET", `${BASE}/runs/${id}`, { signal });
  },
  switchToTiqora(body: ImportSwitchIn) {
    return api.request<ImportJobAccepted>("POST", `${BASE}/switch`, { body });
  },
  rollback(body: ImportRollbackIn) {
    return api.request<ImportStatus>("POST", `${BASE}/rollback`, { body });
  },
  releaseLock() {
    return api.request<ImportStatus>("POST", `${BASE}/release-lock`);
  },
};

/** Report steps in pipeline order (`importer/pipeline.py` STEP_ORDER plus the class definitions). */
export const IMPORT_STEP_ORDER = [
  "catalog",
  "classes",
  "definitions",
  "cis_pass1",
  "cis_pass2",
  "links",
  "attachments",
  "priority_matrix",
  "service_meta",
  "changes",
  "deleted_in_znuny",
  "incident_states",
] as const;

/** Number of issues the report shows inline; the CSV file has all of them. */
export const IMPORT_ISSUE_DISPLAY_LIMIT = 200;
