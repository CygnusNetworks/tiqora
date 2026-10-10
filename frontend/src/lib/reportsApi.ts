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
import type { TFunction } from "i18next";
import { api, ApiError } from "./api";
import { apiErrorMessage } from "./avatarApi";
import type { JobAccepted } from "./jobsApi";

export type ReportDefinition = Schemas["ReportDefinition"];
export type ReportResult = Schemas["ReportResultOut"];
export type ReportColumn = Schemas["ResultColumnOut"];
export type ReportRunIn = Schemas["ReportRunIn"];
export type ReportRunOut = Schemas["ReportRunOut"];
export type ReportExportIn = Schemas["ReportExportIn"];
/** A catalogue entry: built-in or saved (`kind`); `definition` is `null` for a broken saved report. */
export type ReportItem = Schemas["ReportOut"];
/** A built-in report: the i18n keys and the definition are always present. */
export type BuiltinReport = ReportItem & {
  kind: "builtin";
  definition: ReportDefinition;
  category: string;
  title_key: string;
  description_key: string;
};
export function isBuiltinReport(r: ReportItem): r is BuiltinReport {
  return r.kind === "builtin" && r.definition != null && r.category != null && r.title_key != null && r.description_key != null;
}
export type SavedReportIn = Schemas["SavedReportIn"];
export type SavedReportUpdateIn = Schemas["SavedReportUpdateIn"];
export type ReportFile = Schemas["ReportFileOut"];
export type ShareGroup = Schemas["ShareGroupOut"];
export type ReportCatalogue = Schemas["ReportCatalogueOut"];
export type FactsStatus = Schemas["FactsStatusOut"];
export type ReportingStatus = Schemas["ReportingStatusOut"];
export type ReportBackfillIn = Schemas["ReportBackfillIn"];
export type ReportSource = Schemas["ReportSourceOut"];
export type ReportCell = string | number | null;
/** Time buckets of a date dimension (`Grain` in backend/src/tiqora/reporting/definition.py). */
export type Grain = "day" | "week" | "month" | "quarter" | "year";
export const GRAINS: readonly Grain[] = ["day", "week", "month", "quarter", "year"];

export const reportsApi = {
  /** `all` (admins): also other people's private saved reports. */
  catalogue(signal?: AbortSignal, opts: { all?: boolean } = {}) {
    return api.request<ReportCatalogue>("GET", "/api/v1/reports", {
      signal,
      query: opts.all ? { all: true } : undefined,
    });
  },
  create(body: SavedReportIn) {
    return api.request<ReportItem>("POST", "/api/v1/reports", { body });
  },
  /** Full replace; `body.version` is the version the editor loaded (409 on a stale one). */
  update(id: number, body: SavedReportUpdateIn) {
    return api.request<ReportItem>("PUT", `/api/v1/reports/${id}`, { body });
  },
  remove(id: number) {
    return api.request<void>("DELETE", `/api/v1/reports/${id}`);
  },
  /** Download link of a report as a JSON/YAML file (`GET /reports/{key}/file`). */
  fileUrl(key: string, format: "json" | "yaml") {
    return `/api/v1/reports/${encodeURIComponent(key)}/file?format=${format}`;
  },
  /** Parses an exported report file (JSON or YAML text) into a name and definition; saves nothing. */
  importFile(content: string) {
    return api.request<ReportFile>("POST", "/api/v1/reports/import-file", { body: { content } });
  },
  shareGroups(signal?: AbortSignal) {
    return api.request<ShareGroup[]>("GET", "/api/v1/reference/share-groups", { signal });
  },
  get(key: string, signal?: AbortSignal) {
    return api.request<ReportItem>("GET", `/api/v1/reports/${encodeURIComponent(key)}`, {
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
  /** Sources with their dimensions and measures (incl. each measure's aggregation). */
  sources(signal?: AbortSignal) {
    return api.request<ReportSource[]>("GET", "/api/v1/reporting/sources", { signal });
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

/** Saved reports are addressed by their numeric id (no leading zero), built-ins by `builtin.<name>`. */
export function isSavedKey(key: string): boolean {
  return /^[1-9][0-9]{0,17}$/.test(key);
}

/** A saved report's own name, a built-in's translated title (the key as a last resort). */
export function reportTitle(report: ReportItem, t: TFunction): string {
  if (report.kind === "saved") return report.name ?? report.key;
  return report.title_key ? t(report.title_key) : report.key;
}

export function reportDescription(report: ReportItem, t: TFunction): string {
  if (report.kind === "saved") return report.description ?? "";
  return report.description_key ? t(report.description_key) : "";
}
