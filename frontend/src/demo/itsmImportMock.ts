/**
 * Demo / screenshot data for the Znuny ITSM importer (`/api/v1/admin/itsm/import/*`).
 * Path-scoped like `itsmMock.ts`: answers only its own paths, `undefined` for everything else.
 * Shows a Mode A site (Znuny runs the ITSM) with one finished dry run. Example data only.
 */
import type { ImportRunOut, ImportStatus } from "@/lib/itsmImportApi";
import type { Job } from "@/lib/jobsApi";

export const DEMO_ITSM_IMPORT_JOB_ID = 9701;

const DRY_RUN: ImportRunOut = {
  id: 1,
  kind: "dry_run",
  status: "done",
  started_at: "2026-10-12T09:00:00Z",
  finished_at: "2026-10-12T09:01:10Z",
  started_by_name: "admin@example.org",
  job_id: DEMO_ITSM_IMPORT_JOB_ID,
  profile: "ITSM 6.5",
  message: null,
  issue_count: 1,
  issues: [
    {
      step: "links",
      code: "link_target_missing",
      explanation: "Das Ziel einer Verknüpfung existiert nicht mehr.",
      object_ref: "CI 4711",
      message: "Ticket 99 wurde in Znuny gelöscht.",
    },
  ],
  steps: {
    catalog: { created: 12, updated: 0, unchanged: 0, skipped: 0, problems: 0 },
    classes: { created: 3, updated: 0, unchanged: 0, skipped: 0, problems: 0 },
    cis_pass1: { created: 41, updated: 0, unchanged: 0, skipped: 0, problems: 0 },
    cis_pass2: { created: 41, updated: 0, unchanged: 0, skipped: 0, problems: 0 },
    links: { created: 58, updated: 0, unchanged: 0, skipped: 1, problems: 1 },
  },
};

const JOB: Job = {
  id: DEMO_ITSM_IMPORT_JOB_ID,
  kind: "core.itsm_import",
  status: "succeeded",
  title_key: "jobs.kinds.core.itsm_import",
  progress_done: 5,
  progress_total: 5,
  progress_message: null,
  cancel_requested: false,
  error: null,
  result: null,
  files: [{ id: 1, name: "itsm-import-bericht.csv", content_type: "text/csv; charset=utf-8", size_bytes: 2048 }],
  created_by: 1,
  created_at: "2026-10-12T09:00:00Z",
  started_at: "2026-10-12T09:00:00Z",
  finished_at: "2026-10-12T09:01:10Z",
};

const MODE_A_STATUS: ImportStatus = {
  mode: "znuny_readthrough",
  znuny_itsm_present: true,
  profile: "ITSM 6.5",
  can_import: true,
  can_switch: true,
  blocked_reason: null,
  active_job_id: null,
  readonly_flag: false,
  switched: null,
  znuny_changes_since_switch: null,
  znuny_counts: { cis: 41, classes: 3, changes: 12, workorders: 30 },
  last_run: DRY_RUN,
};

const SWITCHED_AT = "2026-10-12T10:00:00Z";
/** The switch's final import: everything was there already, two CIs got a new version. */
const SWITCH_RUN: ImportRunOut = {
  ...DRY_RUN,
  id: 2,
  kind: "switch",
  started_at: SWITCHED_AT,
  finished_at: "2026-10-12T10:02:00Z",
  issue_count: 0,
  issues: [],
  steps: {
    catalog: { created: 0, updated: 0, unchanged: 12, skipped: 0, problems: 0 },
    classes: { created: 0, updated: 0, unchanged: 3, skipped: 0, problems: 0 },
    cis_pass1: { created: 0, updated: 0, unchanged: 41, skipped: 0, problems: 0 },
    cis_pass2: { created: 0, updated: 2, unchanged: 39, skipped: 0, problems: 0 },
    links: { created: 0, updated: 0, unchanged: 58, skipped: 0, problems: 0 },
    incident_states: { created: 0, updated: 1, unchanged: 1, skipped: 0, problems: 0 },
  },
};

/** Switched to Tiqora, with 3 CIs changed in Znuny since (drift warning). */
const SWITCHED_STATUS: ImportStatus = {
  ...MODE_A_STATUS,
  mode: "tiqora",
  can_import: false,
  can_switch: false,
  blocked_reason: "Die ITSM-Daten werden bereits in Tiqora geführt.",
  readonly_flag: true,
  switched: { by: 1, run_id: SWITCH_RUN.id, switched_at: SWITCHED_AT },
  znuny_changes_since_switch: 3,
  last_run: SWITCH_RUN,
};

/** The demo has no server: the switch and the way back flip this flag. */
let switched = false;

/** Back to the Mode A start state (tests). */
export function resetItsmImportMock(): void {
  switched = false;
}

export function resolveItsmImport(path: string, method: string): unknown | undefined {
  if (path.endsWith(`/api/v1/jobs/${DEMO_ITSM_IMPORT_JOB_ID}`) && method === "GET") return JOB;
  const m = path.match(/\/api\/v1\/admin\/itsm\/import(\/.*)?$/);
  if (!m) return undefined;
  const rest = m[1] ?? "";
  if (rest === "/status" && method === "GET") return switched ? SWITCHED_STATUS : MODE_A_STATUS;
  if (rest === "/runs" && method === "GET") return switched ? [SWITCH_RUN, DRY_RUN] : [DRY_RUN];
  if (rest === "/runs" && method === "POST") return { job_id: DEMO_ITSM_IMPORT_JOB_ID, status: "queued" };
  if (/^\/runs\/\d+$/.test(rest) && method === "GET") return DRY_RUN;
  if (rest === "/switch" && method === "POST") {
    switched = true;
    return { job_id: DEMO_ITSM_IMPORT_JOB_ID, status: "queued" };
  }
  if (rest === "/rollback" && method === "POST") {
    switched = false;
    return MODE_A_STATUS;
  }
  if (rest === "/release-lock" && method === "POST") return switched ? SWITCHED_STATUS : MODE_A_STATUS;
  return undefined;
}
