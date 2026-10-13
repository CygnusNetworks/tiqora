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
  job_id: null,
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
  files: [],
  created_by: 1,
  created_at: "2026-10-12T09:00:00Z",
  started_at: "2026-10-12T09:00:00Z",
  finished_at: "2026-10-12T09:01:10Z",
};

const STATUS: ImportStatus = {
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

export function resolveItsmImport(path: string, method: string): unknown | undefined {
  if (path.endsWith(`/api/v1/jobs/${DEMO_ITSM_IMPORT_JOB_ID}`) && method === "GET") return JOB;
  const m = path.match(/\/api\/v1\/admin\/itsm\/import(\/.*)?$/);
  if (!m) return undefined;
  const rest = m[1] ?? "";
  if (rest === "/status" && method === "GET") return STATUS;
  if (rest === "/runs" && method === "GET") return [DRY_RUN];
  if (rest === "/runs" && method === "POST") return { job_id: DEMO_ITSM_IMPORT_JOB_ID, status: "queued" };
  if (/^\/runs\/\d+$/.test(rest) && method === "GET") return DRY_RUN;
  if (rest === "/switch" && method === "POST") return { job_id: DEMO_ITSM_IMPORT_JOB_ID, status: "queued" };
  if ((rest === "/rollback" || rest === "/release-lock") && method === "POST") return STATUS;
  return undefined;
}
