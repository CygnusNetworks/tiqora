/**
 * Client-side decoder for Znuny GenericAgent jobs (read-only admin view).
 *
 * Mirrors the grouping and the *supported subset* of
 * ``tiqora.worker.generic_agent.load_jobs`` / ``apply_actions`` so the admin
 * UI can show, per setting, whether Tiqora actually executes it or silently
 * ignores it (the Perl daemon honored far more keys than this port does).
 *
 * The API delivers ``settings`` as ``{ job_key: string[] }`` — repeated Znuny
 * rows preserved as lists (one ``ScheduleDays`` row per weekday, etc.).
 */

export type EntryKind = "criteria" | "action" | "dynamicfield";

export interface DecodedEntry {
  /** Raw job_key, e.g. "StateIDs" or (for actions) the New-stripped "StateID". */
  key: string;
  /** Original job_key including the "New"/"DynamicField_" prefix. */
  rawKey: string;
  kind: EntryKind;
  values: string[];
  /** True when this port's executor acts on the key; false = read but ignored. */
  executed: boolean;
}

export interface DecodedJob {
  valid: boolean;
  hasSchedule: boolean;
  scheduleDays: number[];
  scheduleHours: number[];
  scheduleMinutes: number[];
  criteria: DecodedEntry[];
  actions: DecodedEntry[];
  dynamicFields: DecodedEntry[];
}

type Settings = Record<string, string[]>;

// --- executor-supported keys (keep in sync with worker/generic_agent.py) ----

const EXECUTED_CRITERIA_IDS = new Set([
  "StateIDs",
  "QueueIDs",
  "PriorityIDs",
  "OwnerIDs",
  "LockIDs",
  "TypeIDs",
]);
const EXECUTED_CRITERIA_TEXT = new Set(["Title", "CustomerID"]);
const TIME_RANGE_PREFIXES = [
  "TicketCreateTime",
  "TicketChangeTime",
  "TicketPendingTime",
  "TicketEscalationTime",
  "TicketEscalationResponseTime",
  "TicketEscalationUpdateTime",
  "TicketEscalationSolutionTime",
];
// New<suffix> actions the port applies. NoteSubject/NoteIsVisibleForCustomer
// only take effect together with NoteBody, but are "used" so we mark them so.
const EXECUTED_ACTION_SUFFIXES = new Set([
  "QueueID",
  "StateID",
  "PriorityID",
  "OwnerID",
  "LockID",
  "Title",
  "NoteBody",
  "NoteSubject",
  "NoteIsVisibleForCustomer",
  "Delete",
]);

function isExecutedCriterion(key: string): boolean {
  if (EXECUTED_CRITERIA_IDS.has(key) || EXECUTED_CRITERIA_TEXT.has(key)) return true;
  return TIME_RANGE_PREFIXES.some(
    (p) => key === `${p}OlderMinutes` || key === `${p}NewerMinutes`,
  );
}

function intList(values: string[]): number[] {
  return values
    .map((v) => Number.parseInt(v, 10))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
}

export function decodeJob(settings: Settings): DecodedJob {
  const criteria: DecodedEntry[] = [];
  const actions: DecodedEntry[] = [];
  const dynamicFields: DecodedEntry[] = [];
  let valid = true;
  let scheduleDays: number[] = [];
  let scheduleHours: number[] = [];
  let scheduleMinutes: number[] = [];

  for (const [rawKey, values] of Object.entries(settings)) {
    if (rawKey === "Valid") {
      valid = values.some((v) => v !== "0" && v !== "");
    } else if (rawKey === "ScheduleDays") {
      scheduleDays = intList(values);
    } else if (rawKey === "ScheduleHours") {
      scheduleHours = intList(values);
    } else if (rawKey === "ScheduleMinutes") {
      scheduleMinutes = intList(values);
    } else if (rawKey.startsWith("DynamicField_")) {
      dynamicFields.push({
        key: rawKey.slice("DynamicField_".length),
        rawKey,
        kind: "dynamicfield",
        values,
        executed: true, // update_dynamic_field runs for every DynamicField_* action
      });
    } else if (rawKey.startsWith("New")) {
      const suffix = rawKey.slice("New".length);
      actions.push({
        key: suffix,
        rawKey,
        kind: "action",
        values,
        executed: EXECUTED_ACTION_SUFFIXES.has(suffix),
      });
    } else {
      criteria.push({
        key: rawKey,
        rawKey,
        kind: "criteria",
        values,
        executed: isExecutedCriterion(rawKey),
      });
    }
  }

  const hasSchedule =
    scheduleDays.length > 0 && scheduleHours.length > 0 && scheduleMinutes.length > 0;

  return {
    valid,
    hasSchedule,
    scheduleDays,
    scheduleHours,
    scheduleMinutes,
    criteria,
    actions,
    dynamicFields,
  };
}

/** Total non-schedule, non-Valid criteria (for the list badge). */
export function criteriaCount(job: DecodedJob): number {
  return job.criteria.length;
}
export function actionCount(job: DecodedJob): number {
  return job.actions.length + job.dynamicFields.length;
}

// --- schedule → human summary --------------------------------------------

const PERL_WEEKDAY_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Mon..Sun in Perl (0=Sun) numbering

/**
 * Short schedule summary, e.g. "Täglich 02:00", "Mo–Fr, stündlich :05".
 * ``weekdayNames`` is indexed by the Perl convention (0=Sun … 6=Sat).
 * Returns null for manual-only jobs (incomplete schedule).
 */
export function scheduleSummary(
  job: DecodedJob,
  weekdayNames: string[],
  labels: { daily: string; hourly: string; every: string },
): string | null {
  if (!job.hasSchedule) return null;

  const pad = (n: number) => String(n).padStart(2, "0");
  const days = new Set(job.scheduleDays);
  const allWeek = [0, 1, 2, 3, 4, 5, 6].every((d) => days.has(d));
  const workweek =
    [1, 2, 3, 4, 5].every((d) => days.has(d)) && !days.has(0) && !days.has(6);

  let dayPart: string;
  if (allWeek) dayPart = labels.daily;
  else if (workweek) dayPart = `${weekdayNames[1]}–${weekdayNames[5]}`;
  else
    dayPart = PERL_WEEKDAY_ORDER.filter((d) => days.has(d))
      .map((d) => weekdayNames[d])
      .join(", ");

  // Time part: single hour+minute → "HH:MM"; many hours → "stündlich :MM".
  const mins = job.scheduleMinutes;
  let timePart: string;
  if (job.scheduleHours.length >= 24 && mins.length === 1) {
    timePart = `${labels.hourly} :${pad(mins[0])}`;
  } else if (job.scheduleHours.length === 1 && mins.length === 1) {
    timePart = `${pad(job.scheduleHours[0])}:${pad(mins[0])}`;
  } else {
    const h = job.scheduleHours.map(pad).join("/");
    const m = mins.map(pad).join("/");
    timePart = `${h}:${m}`;
  }

  return allWeek ? `${dayPart} ${timePart}` : `${dayPart}, ${timePart}`;
}
