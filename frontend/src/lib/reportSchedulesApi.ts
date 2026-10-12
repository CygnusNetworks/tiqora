/**
 * Report schedule endpoints (`backend/src/tiqora/api/v1/report_schedules.py`).
 * The list answers lightweight rows; the full detail (availability, recipient
 * problems) is only on `get`. Wrappers like `./reportsApi.ts`.
 */
import type { Schemas } from "@tiqora/api-client";
import { api } from "./api";

export type ReportSchedule = Schemas["ReportScheduleOut"];
export type ReportScheduleListItem = Schemas["ReportScheduleListItemOut"];
export type ReportScheduleIn = Schemas["ReportScheduleIn"];
export type ReportScheduleUpdateIn = Schemas["ReportScheduleUpdateIn"];
export type ScheduleItemIn = Schemas["ScheduleItemIn"];
export type ScheduleItem = Schemas["ScheduleItemOut"];
export type ScheduleRecipient = Schemas["ScheduleRecipientOut"];
export type ScheduleRecipientsIn = Schemas["ScheduleRecipientsIn"];
export type Recurrence = Schemas["Recurrence"];
export type ReportRun = Schemas["ScheduleRunOut"];
export type ReportRunFile = Schemas["ScheduleRunFileOut"];
export type ScheduleRunAccepted = Schemas["ScheduleRunAccepted"];
export type RecurrencePreview = Schemas["RecurrencePreviewOut"];

export const reportSchedulesApi = {
  /** `all` (admins): also other people's schedules. */
  list(all = false, signal?: AbortSignal) {
    return api.request<ReportScheduleListItem[]>("GET", "/api/v1/report-schedules", {
      signal,
      query: all ? { all: true } : undefined,
    });
  },
  get(id: number, signal?: AbortSignal) {
    return api.request<ReportSchedule>("GET", `/api/v1/report-schedules/${id}`, { signal });
  },
  create(body: ReportScheduleIn) {
    return api.request<ReportSchedule>("POST", "/api/v1/report-schedules", { body });
  },
  /** Full replace; `body.version` is the version the editor loaded (409 on a stale one). */
  update(id: number, body: ReportScheduleUpdateIn) {
    return api.request<ReportSchedule>("PUT", `/api/v1/report-schedules/${id}`, { body });
  },
  remove(id: number) {
    return api.request<void>("DELETE", `/api/v1/report-schedules/${id}`);
  },
  /** `ownerOnly` (default): a test send to the owner; `false` sends to all recipients. */
  runNow(id: number, ownerOnly = true) {
    return api.request<ScheduleRunAccepted>("POST", `/api/v1/report-schedules/${id}/run`, {
      body: { owner_only: ownerOnly },
    });
  },
  runs(id: number, limit?: number, signal?: AbortSignal) {
    return api.request<ReportRun[]>("GET", `/api/v1/report-schedules/${id}/runs`, {
      signal,
      query: limit ? { limit } : undefined,
    });
  },
  /** Download link of a file a run produced. */
  fileUrl(runId: number, fileId: number) {
    return `/api/v1/report-runs/${runId}/files/${fileId}`;
  },
  /** Next dates and a German/English sentence for a recurrence; saves nothing. */
  previewRecurrence(recurrence: Recurrence, timeZone?: string | null, signal?: AbortSignal) {
    return api.request<RecurrencePreview>("POST", "/api/v1/report-schedules/preview-recurrence", {
      body: { recurrence, time_zone: timeZone ?? null },
      signal,
    });
  },
};
