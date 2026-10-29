/**
 * Demo / screenshot / e2e data for change management (`/api/v1/itsm/changes*`, the calendar, CAB
 * boards and meetings, templates, freeze windows, the Znuny change archive, the admin routes under
 * `/api/v1/admin/itsm/change` and the portal schedule). Path-scoped like `itsmMock.ts`: answers only
 * its own paths and returns `undefined` for everything else, so `resolveData` falls through.
 *
 * Mode B data (`demoItsmMode() === "tiqora"`): six changes across the phases, among them an
 * emergency change that collides with a freeze window and the normal change ticket 130 whose CAB
 * approval is the one in `approvalsMock.ts` (`itsm.change` / `change_cab` / `subject_ref "1"`, same
 * ticket id). Mode A answers the status and the live Znuny changes only; mode "off" the status. Like
 * the real backend, the other routes are not answered in Mode A (409 there, 404 here).
 *
 * The resolver sees the path and the query string but no request body, so writes answer a fixed
 * record. All example data uses example.org / .test only.
 */
import type {
  AdminCab,
  AdminRiskQuestion,
  ChangeTemplate,
  FreezeWindow,
  LegacyArchiveStatus,
} from "@/lib/changeAdminApi";
import type {
  CabChangeItem,
  CalendarChange,
  CabMeeting,
  Change,
  ChangeCreateIn,
  ChangeApproval,
  ChangeApprovalSnapshot,
  ChangeCalendar,
  ChangeConflict,
  ChangeEvent,
  ChangeImpact,
  ChangeListItem,
  ChangePage,
  ChangeRelatedTicket,
  ChangeRisk,
  ChangeStatus,
  ChangeTask,
  ChangeTaskListItem,
  ChangeTemplateSummary,
  FreezeOccurrence,
  JobAccepted,
  LegacyChange,
  LegacyChangePage,
  LegacyChangeSummary,
  ServiceAvailability,
} from "@/lib/changeApi";
import type { Job } from "@/lib/jobsApi";
import type { PortalChange } from "@/lib/portalChangeApi";
import { DEMO_APPROVAL_CHANGE_TICKET_ID } from "./approvalsMock";
import { demoItsmMode, type DemoItsmMode } from "./itsmMode";

const T0 = "2026-10-12T08:00:00Z";
const T1 = "2026-10-13T09:30:00Z";

/** Background job of the change demo writes (task creation from a template, impact recompute): done at once. */
export const DEMO_CHANGE_JOB_ID = 9461;

const CHANGE_JOB: Job = {
  id: DEMO_CHANGE_JOB_ID,
  kind: "core.itsm_change_tasks",
  status: "succeeded",
  title_key: "jobs.kinds.core.itsm_change_tasks",
  progress_done: 3,
  progress_total: 3,
  progress_message: null,
  cancel_requested: false,
  error: null,
  result: { created: [141, 142, 143], failed: [] },
  files: [],
  created_by: 1,
  created_at: T0,
  started_at: T0,
  finished_at: T0,
};

const JOB_ACCEPTED: JobAccepted = { job_id: DEMO_CHANGE_JOB_ID, status: "queued" };

// ---- services (ids and names as in `reference/services` of mockData.ts: the list and the create dialog read those) ----------------------------------------------------
const SERVICES: Record<number, string> = { 1: "E-Mail", 2: "VPN", 3: "Arbeitsplatz" };

// ---- changes ---------------------------------------------------------------------------------
type ChangeRow = {
  id: number;
  title: string;
  type: Change["change_type"];
  phase: Change["phase"];
  start: string | null;
  end: string | null;
  services: number[];
  owner: number;
  outcome?: string | null;
  cab?: number | null;
  via?: string | null;
  round?: number;
  reason: string | null;
  portal?: boolean;
};

const CHANGES: ChangeRow[] = [
  {
    id: DEMO_APPROVAL_CHANGE_TICKET_ID,
    title: "Switch-Firmware im Rechenzentrum aktualisieren",
    type: "normal",
    phase: "approval",
    start: "2026-10-24T20:00:00Z",
    end: "2026-10-25T00:00:00Z",
    services: [1, 3],
    owner: 1,
    cab: 1,
    via: "cab",
    round: 1,
    reason: "Die aktuelle Firmware hat eine bekannte Sicherheitslücke; der Hersteller stellt ein Update bereit.",
    portal: true,
  },
  {
    id: 131,
    title: "Neues Backup-Fenster für die Datenbankserver",
    type: "standard",
    phase: "scheduled",
    start: "2026-10-20T01:00:00Z",
    end: "2026-10-20T03:00:00Z",
    services: [1],
    owner: 2,
    via: "template",
    round: 0,
    reason: "Die Sicherung läuft künftig nachts statt am frühen Abend.",
  },
  {
    id: 132,
    title: "Notfall: Firewall-Regel gegen Angriffswelle einspielen",
    type: "emergency",
    phase: "implementing",
    start: "2026-10-14T09:00:00Z",
    end: "2026-10-14T11:00:00Z",
    services: [1, 2, 3],
    owner: 1,
    cab: 2,
    via: "cab",
    round: 1,
    reason: "Zugriffe aus einem gesperrten Adressbereich belasten das Kundenportal.",
    portal: true,
  },
  {
    id: 133,
    title: "Mail-Gateway auf neue Version migrieren",
    type: "normal",
    phase: "assessment",
    start: "2026-11-07T18:00:00Z",
    end: "2026-11-07T22:00:00Z",
    services: [2],
    owner: 2,
    reason: "Die installierte Version erhält keine Sicherheitsupdates mehr.",
  },
  {
    id: 134,
    title: "Wartung des Druckservers",
    type: "normal",
    phase: "draft",
    start: null,
    end: null,
    services: [],
    owner: 1,
    reason: null,
  },
  {
    id: 135,
    title: "Zertifikat des Webportals erneuern",
    type: "normal",
    phase: "closed",
    start: "2026-10-05T19:00:00Z",
    end: "2026-10-05T20:00:00Z",
    services: [1],
    owner: 1,
    outcome: "successful",
    cab: 1,
    via: "cab",
    round: 1,
    reason: "Das Zertifikat läuft in zwei Wochen ab.",
    portal: true,
  },
];

/** What a create or adopt write answers: a fresh draft. */
const NEW_CHANGE = CHANGES[4];

const PHASE_TARGETS: Record<Change["phase"], Change["phase"][]> = {
  draft: ["submitted", "cancelled"],
  submitted: ["assessment", "draft", "cancelled"],
  assessment: ["approval", "cancelled"],
  approval: ["cancelled"],
  scheduled: ["implementing", "draft", "cancelled"],
  implementing: ["review"],
  review: ["closed"],
  closed: [],
  rejected: ["draft"],
  cancelled: [],
};

const tn = (id: number) => `20261012${id}`;

// The only ticket states the backend writes (settings `state_open`, `state_closed_*`).
const TICKET_STATE: Record<Change["phase"], [string, string]> = {
  draft: ["open", "open"],
  submitted: ["open", "open"],
  assessment: ["open", "open"],
  approval: ["open", "open"],
  scheduled: ["open", "open"],
  implementing: ["open", "open"],
  review: ["open", "open"],
  closed: ["closed successful", "closed"],
  rejected: ["open", "open"],
  cancelled: ["closed unsuccessful", "closed"],
};

/** Next phases a person may take in the demo (the allowed targets of `phases.py` without the SYSTEM-only ones). */
export function demoAllowedTargets(type: Change["change_type"], phase: Change["phase"]): Change["phase"][] {
  const emergency = type === "emergency";
  const targets = PHASE_TARGETS[phase].filter(
    (p) =>
      // A standard change is pre-approved by its template; an emergency change skips the assessment.
      !(type === "standard" && (p === "submitted" || p === "assessment" || p === "approval")) &&
      !(emergency && (p === "submitted" || p === "assessment")),
  );
  if (emergency && phase === "draft") targets.push("approval");
  if (type === "standard" && phase === "draft") targets.push("scheduled");
  if (type === "standard" && phase === "implementing") targets.push("closed");
  return targets;
}

function changeOut(c: ChangeRow): Change {
  const [state, stateType] = TICKET_STATE[c.phase];
  const targets = demoAllowedTargets(c.type, c.phase);
  const finished = c.phase === "closed";
  return {
    ticket_id: c.id,
    tn: tn(c.id),
    title: c.title,
    queue_id: 1,
    ticket_state: state,
    ticket_state_type: stateType,
    change_type: c.type,
    phase: c.phase,
    outcome: c.outcome ?? null,
    template_id: c.type === "standard" ? 1 : null,
    cab_id: c.cab ?? null,
    planned_start: c.start,
    planned_end: c.end,
    actual_start: finished || c.phase === "implementing" || c.phase === "review" ? c.start : null,
    actual_end: finished ? c.end : null,
    reason: c.reason,
    implementation_plan: c.phase === "draft" ? null : "1. Wartungsmeldung versenden.\n2. Konfiguration sichern.\n3. Änderung einspielen.\n4. Funktion prüfen.",
    backout_plan: c.phase === "draft" ? null : "Gesicherte Konfiguration zurückspielen und die vorherige Version wiederherstellen.",
    test_plan: c.phase === "draft" ? null : "Erreichbarkeit der betroffenen Dienste und Anmeldung prüfen.",
    pir_notes: finished ? "Der Austausch verlief ohne Unterbrechung." : null,
    risk_level: riskCalc(c).level,
    risk_score: riskCalc(c).score,
    risk_override_level: null,
    risk_override_reason: null,
    effective_risk_level: riskCalc(c).level,
    approval_round: c.round ?? 0,
    approval_via: c.via ?? null,
    approved_at: c.phase === "approval" || c.phase === "draft" || c.phase === "submitted" || c.phase === "assessment" ? null : T1,
    portal_visible: c.portal ?? false,
    process_ticket: false,
    service_ids: c.services,
    impact_computed_at: c.phase === "draft" ? null : T0,
    impact_truncated: false,
    drift: [],
    allowed_targets: targets,
    can_write: true,
    can_manage: true,
    created_at: T0,
    created_by: c.owner,
    updated_at: T1,
    updated_by: c.owner,
  };
}

function listItem(c: ChangeRow): ChangeListItem {
  return {
    ticket_id: c.id,
    tn: tn(c.id),
    title: c.title,
    queue_id: 1,
    change_type: c.type,
    phase: c.phase,
    outcome: c.outcome ?? null,
    owner_id: c.owner,
    planned_start: c.start,
    planned_end: c.end,
    risk_level: riskCalc(c).level,
    service_ids: c.services,
    updated_at: T1,
  };
}

// ---- conflicts -------------------------------------------------------------------------------
const FREEZE_OCCURRENCE: FreezeOccurrence = {
  freeze_id: 1,
  name: "Quartalsabschluss",
  start: "2026-10-13T00:00:00Z",
  end: "2026-10-16T00:00:00Z",
  enforcement: "block",
  allow_emergency: true,
  scope: "services",
  service_ids: [1],
};

const FREEZE_WARN_OCCURRENCE: FreezeOccurrence = {
  freeze_id: 2,
  name: "Wochenend-Bereitschaft",
  start: "2026-10-24T00:00:00Z",
  end: "2026-10-26T00:00:00Z",
  enforcement: "warn",
  allow_emergency: true,
  scope: "all",
  service_ids: [],
};

const FREEZES = [FREEZE_OCCURRENCE, FREEZE_WARN_OCCURRENCE];

function conflictsOf(id: number): ChangeConflict[] {
  if (id === 132) {
    return [
      {
        kind: "freeze",
        severity: "warn",
        ref: "freeze:1",
        start: FREEZE_OCCURRENCE.start,
        end: FREEZE_OCCURRENCE.end,
        shared_services: [1],
        shared_service_count: 1,
        shared_cis: [],
        shared_ci_count: 0,
        hidden: false,
      },
    ];
  }
  if (id === DEMO_APPROVAL_CHANGE_TICKET_ID) {
    return [
      {
        kind: "freeze",
        severity: "info",
        ref: "freeze:2",
        start: FREEZE_WARN_OCCURRENCE.start,
        end: FREEZE_WARN_OCCURRENCE.end,
        shared_services: [],
        shared_service_count: 0,
        shared_cis: [],
        shared_ci_count: 0,
        hidden: false,
      },
    ];
  }
  return [];
}

// ---- risk ------------------------------------------------------------------------------------
const RISK_QUESTIONS: AdminRiskQuestion[] = [
  {
    id: 1,
    position: 1,
    question_key: "tested",
    question: "Wurde die Änderung in einer Testumgebung erprobt?",
    help: "Auch ein Test auf einem einzelnen Gerät zählt.",
    answers: [
      { key: "yes", label: "Ja, vollständig", points: 0 },
      { key: "partly", label: "Teilweise", points: 3 },
      { key: "no", label: "Nein", points: 6 },
    ],
    valid: true,
  },
  {
    id: 2,
    position: 2,
    question_key: "downtime",
    question: "Entsteht für Anwender eine Unterbrechung?",
    help: null,
    answers: [
      { key: "none", label: "Keine", points: 0 },
      { key: "short", label: "Unter einer Stunde", points: 3 },
      { key: "long", label: "Länger", points: 8 },
    ],
    valid: true,
  },
];

const THRESHOLDS = { medium_from: 10, high_from: 20, very_high_from: 35 };

/** Default weights of `ItsmChangeSettings`: freeze 10, emergency 5, service criticality (top - 2) * 3. */
const ANSWERS: Record<number, Record<string, string>> = {
  [DEMO_APPROVAL_CHANGE_TICKET_ID]: { tested: "partly", downtime: "long" },
  131: { tested: "yes", downtime: "short" },
  132: { tested: "no", downtime: "long" },
  133: { tested: "partly", downtime: "long" },
  135: { tested: "yes", downtime: "short" },
};

function riskCalc(c: ChangeRow): {
  answers: Record<string, string>;
  answerPoints: number;
  findings: ChangeRisk["findings"];
  score: number | null;
  level: string | null;
} {
  if (c.phase === "draft") return { answers: {}, answerPoints: 0, findings: [], score: null, level: null };
  const answers = ANSWERS[c.id] ?? {};
  const answerPoints = RISK_QUESTIONS.reduce(
    (sum, q) => sum + (q.answers.find((a) => a.key === answers[q.question_key])?.points ?? 0),
    0,
  );
  const findings: ChangeRisk["findings"] = [];
  if (c.id === 132) {
    findings.push({
      check_key: "core.freeze",
      severity: "warn",
      points: 10,
      message_key: "itsm.change.risk.findings.freeze",
      params: { freezes: 1 },
      refs: [],
      hidden_ref_count: 0,
    });
  }
  if (c.services.length > 1) {
    findings.push({
      check_key: "core.service_criticality",
      severity: "warn",
      points: 9,
      message_key: "itsm.change.risk.findings.service_criticality",
      params: { criticality: 5, services: c.services.length },
      refs: [`service:${c.services[0]}`],
      hidden_ref_count: 0,
    });
  }
  if (c.type === "emergency") {
    findings.push({
      check_key: "core.emergency",
      severity: "info",
      points: 5,
      message_key: "itsm.change.risk.findings.emergency",
      params: {},
      refs: [],
      hidden_ref_count: 0,
    });
  }
  const score = answerPoints + findings.reduce((sum, f) => sum + f.points, 0);
  const level =
    score >= THRESHOLDS.very_high_from ? "very_high" : score >= THRESHOLDS.high_from ? "high" : score >= THRESHOLDS.medium_from ? "medium" : "low";
  return { answers, answerPoints, findings, score, level };
}

function riskOf(c: ChangeRow): ChangeRisk {
  const calc = riskCalc(c);
  const answered = c.phase !== "draft";
  return {
    questions: RISK_QUESTIONS,
    answers: calc.answers,
    answer_points: calc.answerPoints,
    findings: calc.findings,
    score: calc.score,
    level: calc.level,
    override_level: null,
    override_reason: null,
    effective_level: calc.level,
    evaluated_at: answered ? T0 : null,
    questionnaire_complete: answered,
    incomplete: !answered,
    blocked: false,
    thresholds: THRESHOLDS,
    can_answer: c.phase === "draft" || c.phase === "submitted" || c.phase === "assessment",
    can_override: true,
  };
}

// ---- impact, tasks, related, events, approval -------------------------------------------------
function impactOf(c: ChangeRow): ChangeImpact {
  return {
    services: c.services.map((s, i) => ({
      service_id: s,
      name: SERVICES[s] ?? `Service ${s}`,
      direct: i === 0,
      incident_functionality: s === 1 ? "warning" : null,
    })),
    cis: [
      {
        ci_id: 201,
        ref: "tiqora:201",
        number: "1002001",
        name: "srv-web-01.example.org",
        class_key: "computer",
        class_name: "Computer",
        direct: true,
        incident_state: onPortalService(c) ? "Eingeschränkt" : "Betriebsbereit",
        incident_functionality: onPortalService(c) ? "warning" : "operational",
      },
      {
        ci_id: 202,
        ref: "tiqora:202",
        number: "1002002",
        name: "sw-core-01.example.org",
        class_key: "network",
        class_name: "Netzwerk",
        direct: false,
        incident_state: "Betriebsbereit",
        incident_functionality: "operational",
      },
    ],
    incidents: onPortalService(c)
      ? [
          {
            ticket_id: 100,
            tn: "20261012000",
            title: "Website nicht erreichbar",
            state: "open",
            queue_id: 1,
            service_id: 1,
          },
        ]
      : [],
    incidents_truncated: false,
    hidden_service_count: 0,
    hidden_ci_count: 1,
    truncated: false,
    computed_at: c.phase === "draft" ? null : T0,
    requested_at: c.phase === "draft" ? null : T0,
    stale: false,
  };
}

const onPortalService = (c: ChangeRow) => c.services.includes(1);

function task(
  id: number,
  position: number,
  title: string,
  kind: ChangeTask["task_kind"],
  state: string,
  stateType: string,
  start: string | null,
  end: string | null,
): ChangeTask {
  return {
    ticket_id: id,
    tn: tn(id),
    title,
    queue_id: 1,
    owner_id: 1,
    task_kind: kind,
    position,
    state,
    state_type: stateType,
    finished: stateType === "closed",
    planned_start: start,
    planned_end: end,
    planned_effort_minutes: 60,
    actual_start: stateType === "closed" ? start : null,
    actual_end: stateType === "closed" ? end : null,
    template_task_key: null,
  };
}

const TASKS: Record<number, ChangeTask[]> = {
  [DEMO_APPROVAL_CHANGE_TICKET_ID]: [
    task(141, 1, "Konfiguration der Switches sichern", "workorder", "closed successful", "closed", "2026-10-24T19:00:00Z", "2026-10-24T20:00:00Z"),
    task(142, 2, "Firmware einspielen", "workorder", "open", "open", "2026-10-24T20:00:00Z", "2026-10-24T23:00:00Z"),
    task(143, 3, "Rückfall auf die vorherige Firmware", "backout", "new", "new", null, null),
  ],
  132: [
    task(144, 1, "Regel auf der Firewall anlegen", "workorder", "open", "open", "2026-10-14T09:00:00Z", "2026-10-14T10:00:00Z"),
    task(145, 2, "Nachbetrachtung vorbereiten", "pir", "new", "new", null, null),
  ],
  131: [task(146, 1, "Zeitplan der Sicherung anpassen", "workorder", "new", "new", "2026-10-20T01:00:00Z", "2026-10-20T02:00:00Z")],
};

function relatedOf(id: number): ChangeRelatedTicket[] {
  if (id !== 132) return [];
  return [
    {
      ticket_id: 100,
      tn: "20261012000",
      title: "Website nicht erreichbar",
      state: "open",
      state_type: "open",
      link_type: "Normal",
      direction: "source",
      role: "incident",
    },
  ];
}

/** Newest first like the API; `before_id` pages backwards, `limit` caps the page. */
function eventsOf(c: ChangeRow, params: URLSearchParams): ChangeEvent[] {
  const events: ChangeEvent[] = [
    { id: 1, created_at: T0, created_by: c.owner, event: "created", from_phase: null, to_phase: null, detail: null },
    { id: 2, created_at: T0, created_by: c.owner, event: "phase", from_phase: null, to_phase: "draft", detail: null },
  ];
  if (c.phase !== "draft") {
    events.push({ id: 3, created_at: T1, created_by: c.owner, event: "phase", from_phase: "draft", to_phase: c.phase, detail: null });
  }
  const before = Number(params.get("before_id")) || Infinity;
  const limit = Number(params.get("limit")) || 50;
  return events
    .filter((e) => e.id < before)
    .reverse()
    .slice(0, limit);
}

const APPROVAL_SNAPSHOT: ChangeApprovalSnapshot = {
  approval_id: 501,
  approval_key: "change_cab",
  status: "pending",
  reason: null,
  current_stage: 1,
  round: 1,
  votes_total: 3,
  votes_approved: 1,
  votes_rejected: 0,
  created_at: T0,
  decided_at: null,
};

function approvalOf(c: ChangeRow): ChangeApproval {
  if (c.id === DEMO_APPROVAL_CHANGE_TICKET_ID) {
    return { approval_id: 501, approval_round: 1, approval_via: "cab", approved_at: null, history_missing: false, snapshot: APPROVAL_SNAPSHOT };
  }
  if (c.via === "cab") {
    return {
      approval_id: 500 + c.id,
      approval_round: 1,
      approval_via: "cab",
      approved_at: T1,
      history_missing: false,
      snapshot: {
        ...APPROVAL_SNAPSHOT,
        approval_id: 500 + c.id,
        status: "approved",
        reason: null,
        current_stage: null,
        votes_approved: 3,
        decided_at: T1,
      },
    };
  }
  return { approval_id: null, approval_round: c.round ?? 0, approval_via: c.via ?? null, approved_at: c.via ? T1 : null, history_missing: false, snapshot: null };
}

// ---- tasks of the logged-in agent, calendar, availability ------------------------------------
function taskListItem(changeId: number, t: ChangeTask): ChangeTaskListItem {
  const c = CHANGES.find((x) => x.id === changeId);
  return {
    ticket_id: t.ticket_id,
    tn: t.tn,
    title: t.title,
    queue_id: t.queue_id,
    owner_id: t.owner_id,
    task_kind: t.task_kind,
    position: t.position,
    state: t.state,
    state_type: t.state_type,
    finished: t.finished,
    planned_start: t.planned_start,
    planned_end: t.planned_end,
    change_ticket_id: changeId,
    change_tn: c ? tn(c.id) : null,
    change_title: c?.title ?? null,
  };
}

const ALL_TASKS: ChangeTaskListItem[] = Object.entries(TASKS).flatMap(([cid, ts]) => ts.map((t) => taskListItem(Number(cid), t)));

function overlaps(start: string | null, end: string | null, from: string | null, to: string | null): boolean {
  if (!from && !to) return true;
  if (!start || !end) return true;
  return (!to || start < to) && (!from || end > from);
}

function calendar(params: URLSearchParams): ChangeCalendar {
  const start = params.get("start") ?? "2026-10-01T00:00:00Z";
  const end = params.get("end") ?? "2026-11-30T00:00:00Z";
  const serviceIds = params.getAll("service_id").map(Number);
  const wantServices = (services: number[]) => serviceIds.length === 0 || services.some((s) => serviceIds.includes(s));
  const changes = CHANGES.filter(
    (c) => c.start && c.end && c.phase !== "draft" && c.phase !== "cancelled" && overlaps(c.start, c.end, start, end) && wantServices(c.services),
  ).map((c) => {
    const conflicts = conflictsOf(c.id);
    const worst: CalendarChange["conflict_severity"] = conflicts.some((x) => x.severity === "block")
      ? "block"
      : conflicts.some((x) => x.severity === "warn")
        ? "warn"
        : conflicts.length
          ? "info"
          : null;
    return { ...listItem(c), conflict_severity: worst, conflict_kinds: [...new Set(conflicts.map((x) => x.kind))] };
  });
  const tasks = params.get("include_tasks") === "false" ? [] : ALL_TASKS.filter((t) => t.planned_start && overlaps(t.planned_start, t.planned_end, start, end));
  return {
    start,
    end,
    changes,
    tasks,
    freezes: FREEZES.filter((f) => overlaps(f.start, f.end, start, end)),
    truncated: false,
    freezes_truncated: false,
  };
}

function availability(params: URLSearchParams): ServiceAvailability[] {
  const start = params.get("start") ?? "2026-10-01T00:00:00Z";
  const end = params.get("end") ?? "2026-11-30T00:00:00Z";
  const minutes = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 60_000);
  return Object.entries(SERVICES).map(([id, name]) => {
    const sid = Number(id);
    const windows: ServiceAvailability["windows"] = [
      ...CHANGES.filter(
        (c) => c.start && c.end && c.services.includes(sid) && c.phase !== "draft" && c.phase !== "cancelled" && overlaps(c.start, c.end, start, end),
      ).map((c) => ({
        kind: "change" as const,
        ref_id: c.id,
        title: c.title,
        tn: tn(c.id),
        start: c.start as string,
        end: c.end as string,
        change_type: c.type,
        phase: c.phase,
        direct: c.services[0] === sid,
        enforcement: null,
      })),
      ...FREEZES.filter(
        (f) => (f.scope === "all" || f.service_ids.includes(sid)) && overlaps(f.start, f.end, start, end),
      ).map((f) => ({
        kind: "freeze" as const,
        ref_id: f.freeze_id,
        title: f.name,
        tn: null,
        start: f.start,
        end: f.end,
        change_type: null,
        phase: null,
        direct: null,
        enforcement: f.enforcement,
      })),
    ];
    const changeWindows = windows.filter((w) => w.kind === "change");
    return {
      service_id: sid,
      service_name: name,
      change_count: changeWindows.length,
      minutes_affected: changeWindows.reduce((sum, w) => sum + minutes(w.start, w.end), 0),
      windows,
    };
  });
}

// ---- templates, CABs, meetings ------------------------------------------------------------------
const TEMPLATE_TASKS: ChangeTemplate["tasks"] = [
  { key: "backup", title: "Konfiguration sichern", task_kind: "workorder", offset_minutes: -60, duration_minutes: 60, position: 1, body: "Aktuelle Konfiguration exportieren und ablegen." },
  { key: "apply", title: "Änderung durchführen", task_kind: "workorder", offset_minutes: 0, duration_minutes: 120, position: 2, body: "" },
  { key: "backout", title: "Rückfall bereithalten", task_kind: "backout", offset_minutes: 120, duration_minutes: 60, position: 3, body: "" },
];

const TEMPLATES: ChangeTemplate[] = [
  {
    id: 1,
    name: "Backup-Zeitplan ändern",
    description: "Wiederkehrende Anpassung der nächtlichen Sicherung.",
    change_type: "standard",
    is_standard: true,
    valid: true,
    broken: false,
    defaults: { title: "Backup-Zeitplan ändern", duration_minutes: 120, service_ids: [1], portal_visible: false, risk_answers: { tested: "yes", downtime: "none" } },
    tasks: TEMPLATE_TASKS,
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
  {
    id: 2,
    name: "Server-Wartung",
    description: "Geplante Wartung mit Sicherung und Rückfallplan.",
    change_type: "normal",
    is_standard: false,
    valid: true,
    broken: false,
    defaults: { portal_visible: false, duration_minutes: 240, cab_id: 1, implementation_plan: "1. Wartungsmeldung versenden.\n2. Wartung durchführen.", backout_plan: "Gesicherten Stand zurückspielen." },
    tasks: TEMPLATE_TASKS.slice(0, 2),
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
];

const templateSummary = (t: ChangeTemplate): ChangeTemplateSummary => ({
  id: t.id,
  name: t.name,
  description: t.description,
  change_type: t.change_type,
  is_standard: t.is_standard,
  defaults: t.defaults,
  task_count: t.tasks.length,
});

const CABS: AdminCab[] = [
  {
    id: 1,
    name: "CAB Netzwerk",
    description: "Freigabe für Änderungen am Netzwerk und an den Rechenzentrumssystemen.",
    rule: "majority",
    quorum: null,
    is_emergency: false,
    valid: true,
    customer_members_allowed: false,
    members: [
      { member_kind: "list", member_ref: "1", label: "CAB Netzwerk", count: 3, missing: false },
    ],
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
  {
    id: 2,
    name: "Notfall-CAB",
    description: "Kleiner Kreis für Notfall-Changes.",
    rule: "any",
    quorum: null,
    is_emergency: true,
    valid: true,
    customer_members_allowed: false,
    members: [
      { member_kind: "user", member_ref: "1", label: "Alex Turner", count: null, missing: false },
      { member_kind: "group", member_ref: "2", label: "users", count: 4, missing: false },
    ],
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
];

const MEETINGS: CabMeeting[] = [
  {
    id: 1,
    cab_id: 1,
    cab_name: "CAB Netzwerk",
    title: "CAB Netzwerk, Oktober",
    starts_at: "2026-10-15T09:00:00Z",
    ends_at: "2026-10-15T10:00:00Z",
    location: "Besprechungsraum 2",
    status: "planned",
    minutes: null,
    agenda_count: 2,
    agenda: [
      {
        position: 1,
        change_ticket_id: DEMO_APPROVAL_CHANGE_TICKET_ID,
        tn: tn(DEMO_APPROVAL_CHANGE_TICKET_ID),
        title: "Switch-Firmware im Rechenzentrum aktualisieren",
        change_type: "normal",
        phase: "approval",
        planned_start: "2026-10-24T20:00:00Z",
        planned_end: "2026-10-25T00:00:00Z",
        note: "Rückfallplan besprechen.",
        hidden: false,
        approval: APPROVAL_SNAPSHOT,
      },
      {
        position: 2,
        change_ticket_id: 133,
        tn: tn(133),
        title: "Mail-Gateway auf neue Version migrieren",
        change_type: "normal",
        phase: "assessment",
        planned_start: "2026-11-07T18:00:00Z",
        planned_end: "2026-11-07T22:00:00Z",
        note: null,
        hidden: false,
        approval: null,
      },
    ],
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
  {
    id: 2,
    cab_id: 1,
    cab_name: "CAB Netzwerk",
    title: "CAB Netzwerk, September",
    starts_at: "2026-09-17T09:00:00Z",
    ends_at: "2026-09-17T10:00:00Z",
    location: null,
    status: "held",
    minutes: "Zertifikatserneuerung freigegeben.\nSperrzeitraum zum Quartalsabschluss vereinbart.",
    agenda_count: 1,
    agenda: [
      {
        position: 1,
        change_ticket_id: 135,
        tn: tn(135),
        title: "Zertifikat des Webportals erneuern",
        change_type: "normal",
        phase: "closed",
        planned_start: "2026-10-05T19:00:00Z",
        planned_end: "2026-10-05T20:00:00Z",
        note: null,
        hidden: false,
        approval: { ...APPROVAL_SNAPSHOT, approval_id: 635, status: "approved", current_stage: null, votes_approved: 3, decided_at: T1 },
      },
    ],
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
];

const FREEZE_WINDOWS: FreezeWindow[] = [
  {
    id: 1,
    name: "Quartalsabschluss",
    starts_at: "2026-10-13T00:00:00Z",
    ends_at: "2026-10-16T00:00:00Z",
    enforcement: "block",
    allow_emergency: true,
    scope: "services",
    service_ids: [1],
    note: "Keine Änderungen am Kundenportal während des Abschlusses.",
    recurrence: { type: "monthly", interval: 3, count: null, until: null },
    valid: true,
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
  {
    id: 2,
    name: "Wochenend-Bereitschaft",
    starts_at: "2026-10-24T00:00:00Z",
    ends_at: "2026-10-26T00:00:00Z",
    enforcement: "warn",
    allow_emergency: true,
    scope: "all",
    service_ids: [],
    note: null,
    recurrence: null,
    valid: true,
    created_at: T0,
    created_by: 1,
    updated_at: T0,
    updated_by: 1,
  },
];

const archiveStatus = (): LegacyArchiveStatus => ({
  archived: LEGACY.length,
  last_archived_at: T0,
  can_start: true,
  blocked_code: null,
  blocked_message: null,
  active_job_id: null,
});

// ---- Znuny changes (read-only) ---------------------------------------------------------------------
const LEGACY: LegacyChange[] = [
  {
    znuny_change_id: 11,
    number: "2024040110000012",
    title: "Core-Switch ersetzen",
    state: "successful",
    category: "3 normal",
    impact: "4 high",
    priority: "4 high",
    description: "Austausch des Core-Switches im Serverraum.",
    justification: "Das Gerät erreicht das Ende der Herstellerunterstützung.",
    manager_login: "aturner",
    builder_login: "mbeispiel",
    requested_time: null,
    planned_start: "2024-04-06T20:00:00Z",
    planned_end: "2024-04-07T02:00:00Z",
    actual_start: "2024-04-06T20:10:00Z",
    actual_end: "2024-04-07T01:30:00Z",
    created_at: "2024-03-20T08:00:00Z",
    changed_at: "2024-04-07T02:00:00Z",
    dynamic_fields: {},
    cab: [
      { kind: "user", login: "aturner" },
      { kind: "user", login: "mbeispiel" },
    ],
    workorders: [
      {
        number: 1,
        title: "Konfiguration sichern",
        work_order_type: "backup",
        state: "closed",
        instruction: "Konfiguration exportieren.",
        report: "Erledigt.",
        agent_login: "mbeispiel",
        planned_start: "2024-04-06T20:00:00Z",
        planned_end: "2024-04-06T21:00:00Z",
        actual_start: "2024-04-06T20:10:00Z",
        actual_end: "2024-04-06T20:50:00Z",
        planned_effort: 1,
        accounted_time: 0.7,
        dynamic_fields: {},
      },
      {
        number: 2,
        title: "Switch austauschen",
        work_order_type: "workorder",
        state: "closed",
        instruction: null,
        report: "Austausch ohne Auffälligkeiten.",
        agent_login: "aturner",
        planned_start: "2024-04-06T21:00:00Z",
        planned_end: "2024-04-07T02:00:00Z",
        actual_start: "2024-04-06T21:00:00Z",
        actual_end: "2024-04-07T01:30:00Z",
        planned_effort: 4,
        accounted_time: 4.5,
        dynamic_fields: {},
      },
    ],
    tickets: [
      {
        ticket_id: 100,
        tn: "20261012000",
        title: "Website nicht erreichbar",
        state: "closed successful",
        link_type: "RelevantTo",
        direction: "source",
        workorder_number: 0,
      },
    ],
    cis: [
      {
        ref: "znuny:202",
        number: "1002002",
        name: "sw-core-01.example.org",
        class_name: "Netzwerk",
        link_type: "RelevantTo",
        direction: "source",
        workorder_number: 2,
      },
    ],
    services: [{ service_id: 1, name: "E-Mail", link_type: "RelevantTo", direction: "source", workorder_number: 2 }],
    hidden_links: 0,
    history: [
      { created_at: "2024-03-20T08:00:00Z", history_type: "ChangeAdd", field: null, old: null, new: null, old_display: null, new_display: null, user_login: "mbeispiel", workorder_number: 0 },
      { created_at: "2024-04-07T02:00:00Z", history_type: "ChangeUpdate", field: "ChangeStateID", old: "9", new: "12", old_display: "in progress", new_display: "successful", user_login: "aturner", workorder_number: 0 },
    ],
    source: "live",
    znuny_url: "https://znuny.example.org/znuny/index.pl?Action=AgentITSMChangeZoom;ChangeID=11",
  },
  {
    znuny_change_id: 12,
    number: "2024050210000021",
    title: "Mail-Relay austauschen",
    state: "requested",
    category: "2 low",
    impact: "3 normal",
    priority: "3 normal",
    description: null,
    justification: null,
    manager_login: null,
    builder_login: "mbeispiel",
    requested_time: null,
    planned_start: null,
    planned_end: null,
    actual_start: null,
    actual_end: null,
    created_at: "2024-05-02T10:00:00Z",
    changed_at: "2024-05-02T10:00:00Z",
    dynamic_fields: {},
    cab: [],
    workorders: [],
    tickets: [],
    cis: [],
    services: [],
    hidden_links: 0,
    history: [],
    source: "live",
    znuny_url: "https://znuny.example.org/znuny/index.pl?Action=AgentITSMChangeZoom;ChangeID=12",
  },
];

function legacySummary(c: LegacyChange): LegacyChangeSummary {
  return {
    znuny_change_id: c.znuny_change_id,
    number: c.number,
    title: c.title,
    state: c.state,
    planned_start: c.planned_start,
    planned_end: c.planned_end,
    changed_at: c.changed_at,
    workorder_count: c.workorders.length,
  };
}

function legacyPage(params: URLSearchParams, source: "live" | "archive"): LegacyChangePage {
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const ticketId = Number(params.get("ticket_id"));
  const ci = params.get("ci");
  const found = LEGACY.filter(
    (c) =>
      (!q || (c.title ?? "").toLowerCase().includes(q) || c.number.includes(q)) &&
      (!ticketId || c.tickets.some((t) => t.ticket_id === ticketId)) &&
      (!ci || c.cis.some((x) => x.ref === ci)),
  );
  const offset = Number(params.get("offset")) || 0;
  const limit = Number(params.get("limit")) || 25;
  return { source, total: found.length, items: found.slice(offset, offset + limit).map(legacySummary) };
}

// ---- status ----------------------------------------------------------------------------------------
export function changeStatus(mode: DemoItsmMode): ChangeStatus {
  if (mode === "off") {
    return { mode, available: false, reason: "itsm_off", legacy: "none", can_manage: false, can_see_legacy: false, setup_complete_change: false };
  }
  if (mode === "znuny_readthrough") {
    return { mode, available: false, reason: "mode_a", legacy: "live", can_manage: true, can_see_legacy: true, setup_complete_change: false };
  }
  return { mode, available: true, reason: "ok", legacy: "archive", can_manage: true, can_see_legacy: true, setup_complete_change: true };
}

// ---- portal ----------------------------------------------------------------------------------------
const PORTAL: PortalChange[] = CHANGES.filter((c) => c.portal && c.start && c.end).map((c) => ({
  tn: tn(c.id),
  title: c.title,
  change_type: c.type,
  phase: c.phase === "closed" ? "closed" : c.phase === "implementing" ? "implementing" : "scheduled",
  planned_start: c.start,
  planned_end: c.end,
  services: c.services.map((s) => SERVICES[s] ?? `Service ${s}`),
}));

// ---- resolver ---------------------------------------------------------------------------------------
const CHANGE_ID = /\/api\/v1\/itsm\/changes\/(\d+)(?:\/([a-z-]+)(?:\/([a-z]+|\d+))?)?$/;

function listChanges(params: URLSearchParams): ChangePage {
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const phases = params.getAll("phase");
  const types = params.getAll("change_type");
  const service = Number(params.get("service_id")) || null;
  const mine = params.get("mine") !== null && params.get("mine") !== "";
  const from = params.get("window_start");
  const to = params.get("window_end");
  let rows = CHANGES.filter(
    (c) =>
      (!q || c.title.toLowerCase().includes(q) || tn(c.id).includes(q)) &&
      (phases.length === 0 || phases.includes(c.phase)) &&
      (types.length === 0 || types.includes(c.type)) &&
      (service === null || c.services.includes(service)) &&
      (!mine || c.owner === 1) &&
      overlaps(c.start, c.end, from, to),
  );
  if (params.get("order") === "planned_start") {
    rows = [...rows].sort((a, b) => (a.start ?? "9999").localeCompare(b.start ?? "9999"));
  }
  const offset = Number(params.get("offset")) || 0;
  const limit = Number(params.get("limit")) || 25;
  return { items: rows.slice(offset, offset + limit).map(listItem), total: rows.length, truncated: false };
}

/** Changes created in this demo session (kept apart from the fixtures so a test can drop them). */
let createdChanges = 0;

/** Drops the changes the demo created (tests only). */
export function resetDemoChanges(): void {
  CHANGES.splice(CHANGES.length - createdChanges, createdChanges);
  createdChanges = 0;
}

/**
 * `POST /itsm/changes`: a stateful create, so the new change shows in the list and its detail page
 * opens. Refuses like the backend where a form can get it wrong: a standard change needs a standard
 * template, a template must match the type, the window must not end before it starts.
 */
function createDemoChange(body: unknown): ChangeReply {
  const data = (body ?? {}) as Partial<ChangeCreateIn>;
  const template = TEMPLATES.find((t) => t.id === data.template_id && t.valid);
  const fail = (status: number, code: string): ChangeReply => ({ status, body: { detail: { code } } });
  if (data.change_type === "standard" && !template?.is_standard) return fail(422, "standard_needs_template");
  if (data.template_id != null && !template) return fail(422, "template_not_found");
  if (template && template.change_type !== data.change_type) return fail(422, "template_type_mismatch");
  if (data.planned_start && data.planned_end && data.planned_end <= data.planned_start) return fail(422, "invalid_window");
  const id = Math.max(...CHANGES.map((c) => c.id)) + 1;
  CHANGES.push({
    id,
    title: (data.title ?? "").trim() || "Neuer Change",
    type: data.change_type ?? "normal",
    phase: "draft",
    start: data.planned_start ?? null,
    end: data.planned_end ?? null,
    services: [...new Set([...(data.service_id != null ? [data.service_id] : []), ...(data.service_ids ?? [])])],
    owner: 1,
    reason: data.reason ?? null,
  });
  createdChanges += 1;
  const row = CHANGES[CHANGES.length - 1];
  return { status: 201, body: { change: changeOut(row), tasks_job_id: template && template.tasks.length > 0 ? DEMO_CHANGE_JOB_ID : null, warnings: [] } };
}

function changeRoutes(path: string, method: string, params: URLSearchParams): unknown | undefined {
  if (path.endsWith("/api/v1/itsm/changes")) {
    if (method === "GET") return listChanges(params);
    if (method === "POST") return { change: changeOut(NEW_CHANGE), tasks_job_id: DEMO_CHANGE_JOB_ID, warnings: [] };
    return undefined;
  }
  const at = path.match(CHANGE_ID);
  if (at) {
    const row = CHANGES.find((c) => c.id === Number(at[1]));
    if (!row) return undefined;
    const sub = at[2];
    const leaf = at[3];
    if (!sub) return method === "GET" || method === "PATCH" ? changeOut(row) : undefined;
    if (sub === "transition" && method === "POST") return changeOut(row);
    if (sub === "events" && method === "GET") return eventsOf(row, params);
    if (sub === "impact") return leaf === "recompute" ? (method === "POST" ? JOB_ACCEPTED : undefined) : impactOf(row);
    if (sub === "conflicts" && method === "GET") return conflictsOf(row.id);
    if (sub === "risk") return riskOf(row);
    if (sub === "approval" && method === "GET") return approvalOf(row);
    if (sub === "related") return relatedOf(row.id);
    if (sub === "tasks") {
      const tasks = TASKS[row.id] ?? [];
      if (method === "GET" || leaf === "order") return tasks;
      return tasks[0] ?? task(150, 1, "Neue Aufgabe", "workorder", "new", "new", null, null);
    }
    return undefined;
  }
  if (path.endsWith("/api/v1/itsm/change-tasks/mine")) {
    return params.get("open_only") === "false" ? ALL_TASKS : ALL_TASKS.filter((t) => !t.finished);
  }
  if (path.endsWith("/api/v1/itsm/change-calendar")) return calendar(params);
  if (path.endsWith("/api/v1/itsm/change-calendar/services")) return availability(params);
  if (path.endsWith("/api/v1/itsm/change-templates")) return TEMPLATES.filter((t) => t.valid).map(templateSummary);
  if (path.endsWith("/api/v1/itsm/freeze-windows")) {
    const from = params.get("start");
    const to = params.get("end");
    return { items: FREEZES.filter((f) => !from || !to || overlaps(f.start, f.end, from, to)), truncated: false };
  }
  if (path.endsWith("/api/v1/itsm/cabs")) return CABS.filter((c) => c.valid);
  if (path.endsWith("/api/v1/itsm/cab-meetings")) {
    if (method !== "GET") return MEETINGS[0];
    const from = params.get("start");
    const to = params.get("end");
    return MEETINGS.filter((m) => !from || !to || overlaps(m.starts_at, m.ends_at, from, to));
  }
  const meeting = path.match(/\/api\/v1\/itsm\/cab-meetings\/(\d+)(\/agenda|\/suggestions)?$/);
  if (meeting) {
    const found = MEETINGS.find((m) => m.id === Number(meeting[1]));
    if (!found) return undefined;
    if (meeting[2] === "/suggestions") {
      const suggestions: CabChangeItem[] = CHANGES.filter((c) => c.id === 133).map((c) => ({ ...listItem(c) }));
      return suggestions;
    }
    return found;
  }
  return undefined;
}

function legacyRoutes(path: string, params: URLSearchParams, mode: DemoItsmMode): unknown | undefined {
  const source = mode === "znuny_readthrough" ? "live" : "archive";
  if (path.endsWith("/api/v1/itsm/legacy-changes")) return legacyPage(params, source);
  const one = path.match(/\/api\/v1\/itsm\/legacy-changes\/(\d+)$/);
  if (one) {
    const found = LEGACY.find((c) => c.znuny_change_id === Number(one[1]));
    return found ? { ...found, source, znuny_url: source === "live" ? found.znuny_url : null } : undefined;
  }
  return undefined;
}

function adminRoutes(path: string, method: string): unknown | undefined {
  const at = path.indexOf("/api/v1/admin/itsm/change/");
  if (at < 0) return undefined;
  const rest = path.slice(at + "/api/v1/admin/itsm/change/".length);
  if (rest === "templates") return method === "POST" ? TEMPLATES[1] : TEMPLATES;
  const template = rest.match(/^templates\/(\d+)$/);
  if (template) {
    if (method === "DELETE") return null;
    return TEMPLATES.find((t) => t.id === Number(template[1]));
  }
  if (rest === "cabs") return method === "POST" ? CABS[0] : CABS;
  const cab = rest.match(/^cabs\/(\d+)(\/members)?$/);
  if (cab) {
    if (method === "DELETE") return null;
    return CABS.find((c) => c.id === Number(cab[1]));
  }
  if (rest === "risk-questions") return method === "POST" ? RISK_QUESTIONS[0] : RISK_QUESTIONS;
  if (rest === "risk-questions/examples") return { created: RISK_QUESTIONS.map((q) => q.id) };
  if (rest === "risk-questions/order") return RISK_QUESTIONS;
  const question = rest.match(/^risk-questions\/(\d+)$/);
  if (question) {
    if (method === "DELETE") return null;
    return RISK_QUESTIONS.find((q) => q.id === Number(question[1]));
  }
  if (rest === "freeze-windows") return method === "POST" ? FREEZE_WINDOWS[0] : FREEZE_WINDOWS;
  const freeze = rest.match(/^freeze-windows\/(\d+)$/);
  if (freeze) {
    if (method === "DELETE") return null;
    return FREEZE_WINDOWS.find((f) => f.id === Number(freeze[1]));
  }
  if (rest === "legacy-archive") return method === "POST" ? JOB_ACCEPTED : archiveStatus();
  return undefined;
}

export function resolveChangeData(
  path: string,
  method: string,
  search = "",
  mode: DemoItsmMode = demoItsmMode(),
): unknown | undefined {
  const params = new URLSearchParams(search);
  if (path.endsWith(`/api/v1/jobs/${DEMO_CHANGE_JOB_ID}`) && method === "GET") return CHANGE_JOB;
  if (path.endsWith("/api/v1/itsm/changes/status")) return changeStatus(mode);
  if (path.includes("/api/v1/itsm/legacy-changes") && mode !== "off") return legacyRoutes(path, params, mode);
  if (mode !== "tiqora") return undefined;
  if (path.endsWith("/api/portal/change-schedule")) return PORTAL;
  const admin = adminRoutes(path, method);
  if (admin !== undefined) return admin;
  if (path.includes("/api/v1/itsm/")) return changeRoutes(path, method, params);
  // Adopt / create-from live under the ticket.
  if (/\/api\/v1\/tickets\/\d+\/change(\/create-from)?$/.test(path) && method !== "GET") {
    return path.endsWith("/create-from")
      ? { change: changeOut(NEW_CHANGE), tasks_job_id: null, warnings: [] }
      : changeOut(NEW_CHANGE);
  }
  return undefined;
}

const MODE_B_ONLY =
  /\/api\/v1\/itsm\/(changes(?!\/status$)|change-tasks|change-calendar|change-templates|freeze-windows|cabs|cab-meetings)|\/api\/v1\/tickets\/\d+\/change(\/create-from)?$|\/api\/portal\/change-schedule$/;

export type ChangeReply = { status: number; body?: unknown };

/**
 * The answer with its status, for the handlers that can send one: the data of `resolveChangeData`
 * (201/202 for the creating writes, 204 for deletes) and, like the backend, 404 `change_unavailable`
 * while ITSM is off and 409 `change_mode_a` in Mode A for the routes that need the Tiqora change
 * management. `undefined`: not a change route.
 */
export function resolveChangeReply(
  path: string,
  method: string,
  search = "",
  mode: DemoItsmMode = demoItsmMode(),
  requestBody?: unknown,
): ChangeReply | undefined {
  if (mode !== "tiqora" && MODE_B_ONLY.test(path)) {
    return mode === "off"
      ? { status: 404, body: { detail: { code: "change_unavailable" } } }
      : { status: 409, body: { detail: { code: "change_mode_a" } } };
  }
  if (mode === "off" && path.includes("/api/v1/itsm/legacy-changes")) {
    return { status: 404, body: { detail: { code: "change_unavailable" } } };
  }
  if (mode === "tiqora" && method === "POST" && path.endsWith("/api/v1/itsm/changes")) return createDemoChange(requestBody);
  const body = resolveChangeData(path, method, search, mode);
  if (body === undefined) return undefined;
  if (body === null) return { status: 204 };
  if (method !== "POST") return { status: 200, body };
  if (/\/(impact\/recompute|legacy-archive)$/.test(path)) return { status: 202, body };
  if (/\/(transition|risk\/evaluate)$/.test(path) || /\/tickets\/\d+\/change$/.test(path)) return { status: 200, body };
  return { status: 201, body };
}
