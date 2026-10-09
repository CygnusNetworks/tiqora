/**
 * Report endpoints (`backend/src/tiqora/api/v1/reports.py`, admin status in
 * `api/v1/admin/reporting.py`). Hand-written wrappers like `./phoneApi.ts`;
 * the shapes come from the generated schema.
 *
 * `POST /reports/run` answers 200 `{result, job: null}` (inline or cached) or
 * 202 `{result: null, job}` (background run). Errors carry a German
 * `detail.message`: 422 (`message`, `path`), 429 (too many active report
 * jobs), 504 (took too long) — read them with `reportErrorMessage`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api, ApiError } from "./api";
import { apiErrorMessage } from "./avatarApi";
import type { JobAccepted } from "./jobsApi";

export type ReportDefinition = Schemas["ReportDefinition"];
export type ReportResult = Schemas["ReportResultOut"];
export type ReportColumn = Schemas["ResultColumnOut"];
export type ReportRunIn = Schemas["ReportRunIn"];
export type ReportRunOut = Schemas["ReportRunOut"];
export type ReportExportIn = Schemas["ReportExportIn"];
export type BuiltinReport = Schemas["BuiltinReportOut"];
export type ReportCatalogue = Schemas["ReportCatalogueOut"];
export type FactsStatus = Schemas["FactsStatusOut"];
export type ReportingStatus = Schemas["ReportingStatusOut"];
export type ReportBackfillIn = Schemas["ReportBackfillIn"];
export type ReportCell = string | number | null;

export const reportsApi = {
  catalogue(signal?: AbortSignal) {
    return api.request<ReportCatalogue>("GET", "/api/v1/reports", { signal });
  },
  get(key: string, signal?: AbortSignal) {
    return api.request<BuiltinReport>("GET", `/api/v1/reports/${encodeURIComponent(key)}`, {
      signal,
    });
  },
  /** 200 and 202 share the body shape: exactly one of `result` / `job` is set. */
  run(body: ReportRunIn, signal?: AbortSignal) {
    return api.request<ReportRunOut>("POST", "/api/v1/reports/run", { body, signal });
  },
  export(body: ReportExportIn) {
    return api.request<JobAccepted>("POST", "/api/v1/reports/export", { body });
  },
  factsStatus(signal?: AbortSignal) {
    return api.request<FactsStatus>("GET", "/api/v1/reporting/status", { signal });
  },
  adminStatus(signal?: AbortSignal) {
    return api.request<ReportingStatus>("GET", "/api/v1/admin/reporting/status", { signal });
  },
  adminBackfill(body: ReportBackfillIn) {
    return api.request<JobAccepted>("POST", "/api/v1/admin/reporting/backfill", { body });
  },
};

/** The server's German `detail.message` (422/429/504), a failed job's text, or `fallback`. */
export function reportErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return apiErrorMessage(err, fallback);
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
