/**
 * Background-job endpoints (`backend/src/tiqora/api/v1/jobs.py`) and the
 * ticket CSV export that starts one. Hand-written wrappers like `./phoneApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type Job = Schemas["JobOut"];
export type JobAccepted = Schemas["JobAccepted"];
export type TicketExportJobIn = Schemas["TicketExportJobIn"];
/** Request body as the pages build it: `sort`/`order` have server defaults. */
export type TicketExportJobBody = Partial<TicketExportJobIn>;

export const TERMINAL_JOB_STATUSES: ReadonlySet<Job["status"]> = new Set<Job["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);

export const jobsApi = {
  get(jobId: number, signal?: AbortSignal) {
    return api.request<Job>("GET", `/api/v1/jobs/${jobId}`, { signal });
  },
  cancel(jobId: number) {
    return api.request<Job>("POST", `/api/v1/jobs/${jobId}/cancel`);
  },
  list(opts: { all?: boolean } = {}, signal?: AbortSignal) {
    return api.request<Job[]>("GET", "/api/v1/jobs", {
      query: opts.all ? { all: true } : undefined,
      signal,
    });
  },
  fileUrl(jobId: number, fileId: number): string {
    return `/api/v1/jobs/${jobId}/files/${fileId}`;
  },
  startTicketExport(body: TicketExportJobBody) {
    return api.request<JobAccepted>("POST", "/api/v1/tickets/export-jobs", { body });
  },
};
