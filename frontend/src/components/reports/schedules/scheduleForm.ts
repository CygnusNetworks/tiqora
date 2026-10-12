import type {
  Recurrence,
  ReportSchedule,
  ReportScheduleIn,
  ReportScheduleUpdateIn,
  ScheduleItemIn,
} from "@/lib/reportSchedulesApi";

/**
 * Form state of the schedule editor and its translation to and from the API.
 * Pure: no React, no i18n (errors are i18n keys under `reports.schedules.errors`).
 */

export const MAX_ITEMS = 10;
export const MAX_RECIPIENT_LISTS = 50;

export type RecurrenceKind = Recurrence["kind"];

/** Recurrence with every field present; `toRequest` keeps only those of the kind. */
export type RecurrenceState = {
  kind: RecurrenceKind;
  time: string;
  weekdays: number[];
  workdays_only: boolean;
  day_of_month: number | null;
  month: number | null;
};

export type RecipientsState = {
  user_ids: number[];
  group_ids: number[];
  role_ids: number[];
  emails: string[];
};

export type ScheduleFormState = {
  /** `null` for a schedule that is not saved yet. */
  version: number | null;
  /** Owner of the schedule: "an mich" means exactly this agent. */
  ownerId: number;
  name: string;
  items: ScheduleItemIn[];
  format: ReportScheduleIn["format"];
  recurrence: RecurrenceState;
  timeZone: string;
  language: ReportScheduleIn["language"];
  subject: string;
  body: string;
  onlyIfNonempty: boolean;
  active: boolean;
  recipients: RecipientsState;
};

export type FormDefaults = { ownerId: number; reportKey?: string | null; timeZone: string; language?: "de" | "en" };

export type FormRights = { canShare: boolean; externalAllowed: boolean };

export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;

export function emptyRecurrence(): RecurrenceState {
  return { kind: "weekly", time: "07:00", weekdays: [1], workdays_only: false, day_of_month: null, month: null };
}

export function emptyForm(defaults: FormDefaults): ScheduleFormState {
  return {
    version: null,
    ownerId: defaults.ownerId,
    name: "",
    items: defaults.reportKey ? [{ report_key: defaults.reportKey, overrides: {} }] : [],
    format: "pdf",
    recurrence: emptyRecurrence(),
    timeZone: defaults.timeZone,
    language: defaults.language ?? "de",
    subject: "",
    body: "",
    onlyIfNonempty: false,
    active: true,
    recipients: { user_ids: [defaults.ownerId], group_ids: [], role_ids: [], emails: [] },
  };
}

export function fromSchedule(s: ReportSchedule): ScheduleFormState {
  const r = s.recurrence;
  return {
    version: s.version,
    ownerId: s.owner.user_id,
    name: s.name,
    items: s.items.map((i) => ({ report_key: i.report_key, overrides: i.overrides ?? {} })),
    format: s.format,
    recurrence: r
      ? {
          kind: r.kind,
          time: r.time,
          weekdays: [...(r.weekdays ?? [])],
          workdays_only: r.workdays_only ?? false,
          day_of_month: r.day_of_month ?? null,
          month: r.month ?? null,
        }
      : emptyRecurrence(),
    timeZone: s.time_zone,
    language: s.language,
    subject: s.subject,
    body: s.body,
    onlyIfNonempty: s.only_if_nonempty,
    active: s.active,
    recipients: {
      user_ids: s.recipients.filter((x) => x.kind === "user" && x.id != null).map((x) => x.id as number),
      group_ids: s.recipients.filter((x) => x.kind === "group" && x.id != null).map((x) => x.id as number),
      role_ids: s.recipients.filter((x) => x.kind === "role" && x.id != null).map((x) => x.id as number),
      emails: s.recipients.filter((x) => x.kind === "email" && x.email).map((x) => x.email as string),
    },
  };
}

/** Only the fields that belong to the kind; the server refuses the others. */
export function recurrenceRequest(r: RecurrenceState): Recurrence {
  // `workdays_only` is a required (defaulted) field of the type; false is what the server accepts for the other kinds
  const base = { kind: r.kind, time: r.time };
  switch (r.kind) {
    case "daily":
      return { ...base, workdays_only: r.workdays_only };
    case "weekly":
      return { ...base, workdays_only: false, weekdays: [...r.weekdays].sort((a, b) => a - b) };
    case "monthly":
      return { ...base, workdays_only: false, day_of_month: r.day_of_month };
    case "yearly":
      return { ...base, workdays_only: false, day_of_month: r.day_of_month, month: r.month };
  }
}

/** The request body; carries `version` once the schedule exists (update). */
export function toRequest(f: ScheduleFormState): ReportScheduleIn & { version?: number } {
  const body: ReportScheduleIn = {
    name: f.name.trim(),
    items: f.items.map((i) => ({ report_key: i.report_key, overrides: i.overrides ?? {} })),
    format: f.format,
    recurrence: recurrenceRequest(f.recurrence),
    time_zone: f.timeZone,
    language: f.language,
    subject: f.subject.trim(),
    body: f.body,
    only_if_nonempty: f.onlyIfNonempty,
    active: f.active,
    recipients: {
      user_ids: [...f.recipients.user_ids],
      group_ids: [...f.recipients.group_ids],
      role_ids: [...f.recipients.role_ids],
      emails: [...f.recipients.emails],
    },
  };
  return f.version != null ? { ...body, version: f.version } : body;
}

export function toUpdateRequest(f: ScheduleFormState): ReportScheduleUpdateIn {
  return toRequest(f) as ReportScheduleUpdateIn;
}

/** Whether anyone but the owner would receive the report (needs the share right). */
export function sendsToOthers(r: RecipientsState, ownerId: number): boolean {
  return (
    r.group_ids.length > 0 ||
    r.role_ids.length > 0 ||
    r.emails.length > 0 ||
    r.user_ids.some((id) => id !== ownerId)
  );
}

const TIME_RE = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const EMAIL_RE = /^[^\s@,;<>]+@[^\s@,;<>]+$/;

export function isEmailLike(v: string): boolean {
  return EMAIL_RE.test(v.trim());
}

/** Errors of the recurrence part alone (`time`, `weekdays`, `day_of_month`, `month`). */
export function recurrenceErrors(r: RecurrenceState): Record<string, string> {
  const e: Record<string, string> = {};
  const p = "reports.schedules.errors";
  if (!TIME_RE.test(r.time)) e.time = `${p}.time`;
  if (r.kind === "weekly" && r.weekdays.length === 0) e.weekdays = `${p}.weekdays`;
  if ((r.kind === "monthly" || r.kind === "yearly") && (r.day_of_month == null || r.day_of_month < 1 || r.day_of_month > 31))
    e.day_of_month = `${p}.dayOfMonth`;
  if (r.kind === "yearly" && (r.month == null || r.month < 1 || r.month > 12)) e.month = `${p}.month`;
  return e;
}

/** Field -> i18n key under `reports.schedules.errors`; empty when the form is fine. */
export function validateForm(f: ScheduleFormState, rights: FormRights): Record<string, string> {
  const e: Record<string, string> = {};
  const p = "reports.schedules.errors";
  if (f.name.trim() === "") e.name = `${p}.name`;
  if (f.items.length === 0) e.items = `${p}.items`;
  else if (f.items.length > MAX_ITEMS) e.items = `${p}.itemsMax`;
  Object.assign(e, recurrenceErrors(f.recurrence));
  const rc = f.recipients;
  if (rc.user_ids.length + rc.group_ids.length + rc.role_ids.length + rc.emails.length === 0) {
    e.recipients = `${p}.recipientsEmpty`;
  } else if (rc.emails.some((m) => !isEmailLike(m))) {
    e.recipients = `${p}.email`;
  } else if (sendsToOthers(rc, f.ownerId) && !rights.canShare) {
    e.recipients = `${p}.shareRight`;
  } else if (rc.emails.length > 0 && !rights.externalAllowed) {
    e.recipients = `${p}.externalNotAllowed`;
  }
  return e;
}
