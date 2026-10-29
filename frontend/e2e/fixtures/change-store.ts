import { test } from "@playwright/test";
import {
  DEMO_CHANGE_JOB_ID,
  demoAllowedTargets,
  resetDemoChanges,
  resolveChangeData,
} from "../../src/demo/changeMock";
import type { DemoItsmMode } from "../../src/demo/itsmMode";
import type {
  Change,
  ChangeApproval,
  ChangeConflict,
  ChangeEvent,
  ChangeImpact,
  ChangeListItem,
  ChangePhase,
  ChangeRisk,
  ChangeTask,
  ChangeType,
} from "../../src/lib/changeApi";
import { fulfillAnswer, type StoreAnswer } from "./approvals-store";

/**
 * Stateful change management for the e2e mocks. The demo resolver (`src/demo/changeMock.ts`) moves
 * only its fixture changes and answers risk, approval and tasks from the phase; the full flows need
 * records that remember their answers, plans, approval round and tasks. This store owns the changes
 * a test creates (ticket ids from 300) and answers every `/itsm/changes/{id}...` route for them
 * with the backend's rules: the phase table and guard codes of `itsm/change/phases.py`, the 409
 * `transition_refused` shape with `failures: [{code, overridable}]`, the events the transition
 * service writes (`phase`, `conflict_override`, `forced`, `approval_requested`, `approval_outcome`).
 * Everything else (fixtures, calendar, templates, admin) still comes from the demo resolver.
 * One store per test attempt, like `approvals-store.ts`.
 *
 * The CAB's vote and the closing of task tickets happen in other screens (the approval engine, the
 * ticket zoom); the tests stand in for them with `decideApproval` and `finishTasks`.
 */

const FIRST_ID = 300;
const NOW = Date.parse("2026-10-28T09:00:00Z");
const ADMIN_PREFIX = "/api/v1/admin/itsm/change/";

const ok = (body: unknown, status = 200): StoreAnswer => ({ status, body });
const fail = (status: number, code: string, extra: Record<string, unknown> = {}): StoreAnswer => ({
  status,
  body: { detail: { code, ...extra } },
});

type Freeze = {
  id: number;
  name: string;
  start: string;
  end: string;
  enforcement: "block" | "warn";
  allow_emergency: boolean;
  /** empty: every service */
  services: number[];
};

/** Block freezes the flows run into: the demo's, plus a year-end freeze that lets no emergency change through. */
const FREEZES: Freeze[] = [
  { id: 1, name: "Quartalsabschluss", start: "2026-10-13T00:00:00Z", end: "2026-10-16T00:00:00Z", enforcement: "block", allow_emergency: true, services: [1] },
  { id: 2, name: "Wochenend-Bereitschaft", start: "2026-10-24T00:00:00Z", end: "2026-10-26T00:00:00Z", enforcement: "warn", allow_emergency: true, services: [] },
  { id: 3, name: "Jahresabschluss", start: "2026-12-28T00:00:00Z", end: "2027-01-02T00:00:00Z", enforcement: "block", allow_emergency: false, services: [] },
];

const SERVICES: Record<number, string> = { 1: "E-Mail", 2: "VPN", 3: "Arbeitsplatz" };
const THRESHOLDS = { medium_from: 10, high_from: 20, very_high_from: 35 };

type Rec = {
  id: number;
  title: string;
  type: ChangeType;
  phase: ChangePhase;
  outcome: string | null;
  templateId: number | null;
  cabId: number | null;
  start: string | null;
  end: string | null;
  actualStart: string | null;
  actualEnd: string | null;
  reason: string | null;
  plans: { implementation_plan: string | null; backout_plan: string | null; test_plan: string | null };
  pir: string | null;
  answers: Record<string, string>;
  overrideLevel: string | null;
  overrideReason: string | null;
  evaluatedAt: string | null;
  round: number;
  approvalId: number | null;
  via: string | null;
  approvedAt: string | null;
  approvalStatus: "pending" | "approved" | "rejected" | "cancelled" | null;
  portal: boolean;
  services: number[];
  impactRequestedAt: string | null;
  impactComputedAt: string | null;
  tasks: ChangeTask[];
  events: ChangeEvent[];
};

type Failure = { code: string; overridable: boolean };

const PAIRS: Record<string, ChangeType[]> = {
  "draft>submitted": ["normal"],
  "draft>scheduled": ["standard"],
  "draft>approval": ["emergency"],
  "submitted>assessment": ["normal"],
  "submitted>draft": ["normal"],
  "assessment>approval": ["normal"],
  "approval>scheduled": ["normal", "emergency"],
  "approval>rejected": ["normal", "emergency"],
  "approval>assessment": ["normal"],
  "approval>draft": ["emergency"],
  "rejected>draft": ["normal", "emergency"],
  "scheduled>implementing": ["standard", "normal", "emergency"],
  "scheduled>draft": ["standard", "normal", "emergency"],
  "implementing>review": ["normal", "emergency"],
  "implementing>closed": ["standard"],
  "review>closed": ["standard", "normal", "emergency"],
  "draft>cancelled": ["standard", "normal", "emergency"],
  "submitted>cancelled": ["standard", "normal", "emergency"],
  "assessment>cancelled": ["standard", "normal", "emergency"],
  "approval>cancelled": ["standard", "normal", "emergency"],
  "scheduled>cancelled": ["standard", "normal", "emergency"],
};
/** Taken only by the approval outcome. */
const SYSTEM_ONLY = new Set(["approval>scheduled", "approval>rejected", "approval>assessment", "approval>draft"]);
const FINAL = new Set<ChangePhase>(["closed", "cancelled"]);

/** `ticket_state` and its type per phase (the states the backend writes). */
const TICKET_STATE: Partial<Record<ChangePhase, [string, string]>> = {
  closed: ["closed successful", "closed"],
  cancelled: ["closed unsuccessful", "closed"],
};

const clean = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);
const iso = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");

class ChangeStore {
  changes = new Map<number, Rec>();
  private next = FIRST_ID;
  private tick = 0;
  /** Bodies of the admin writes the pages sent (template, freeze window), for the assertions. */
  adminWrites: { method: string; path: string; body: unknown }[] = [];
  /** The signed-in agent is a change manager (the override and force offers depend on it). */
  manager = true;

  constructor() {
    // The demo resolver keeps module state across the tests of a worker.
    resetDemoChanges();
  }

  // ---- test hooks ----------------------------------------------------------------------------
  /** The CAB's decision: the approval engine's outcome job moves an `approval` change on. */
  decideApproval(id: number, status: "approved" | "rejected") {
    const r = this.must(id);
    if (r.phase !== "approval") throw new Error(`change ${id} is in ${r.phase}, not approval`);
    r.approvalStatus = status;
    const to: ChangePhase = status === "approved" ? "scheduled" : "rejected";
    this.event(r, "approval_outcome", { detail: { status, round: r.round } });
    this.event(r, "phase", { from: r.phase, to });
    r.phase = to;
    if (status === "approved") r.approvedAt = this.stamp();
  }

  /** The task tickets were closed in the zoom. */
  finishTasks(id: number) {
    for (const t of this.must(id).tasks) this.finish(t);
  }

  /** A task ticket of the change by title. */
  taskId(id: number, title: string): number {
    const t = this.must(id).tasks.find((x) => x.title === title);
    if (!t) throw new Error(`no task "${title}" on change ${id}`);
    return t.ticket_id;
  }

  /** Ids of the changes this test created, oldest first. */
  ids(): number[] {
    return [...this.changes.keys()];
  }

  // ---- helpers -------------------------------------------------------------------------------
  private must(id: number): Rec {
    const r = this.changes.get(id);
    if (!r) throw new Error(`no change ${id}`);
    return r;
  }

  private stamp(): string {
    this.tick += 1;
    return iso(NOW + this.tick * 60_000);
  }

  private event(r: Rec, event: string, o: { from?: string; to?: string; detail?: Record<string, unknown> | null } = {}) {
    r.events.push({
      id: r.events.length + 1,
      created_at: this.stamp(),
      created_by: 2,
      event,
      from_phase: o.from ?? null,
      to_phase: o.to ?? null,
      detail: o.detail ?? null,
    });
  }

  private finish(t: ChangeTask) {
    t.state = "closed successful";
    t.state_type = "closed";
    t.finished = true;
  }

  private questions() {
    return (resolveChangeData("/api/v1/itsm/changes/131/risk", "GET", "", "tiqora") as ChangeRisk).questions;
  }

  private freezeConflicts(r: Rec): ChangeConflict[] {
    if (!r.start || !r.end) return [];
    const out: ChangeConflict[] = [];
    for (const f of FREEZES) {
      const shared = f.services.length === 0 ? [] : f.services.filter((s) => r.services.includes(s));
      if (f.services.length > 0 && shared.length === 0) continue;
      if (!(r.start < f.end && r.end > f.start)) continue;
      // like detect_conflicts: a block freeze is only a warning for an emergency change it lets through
      let severity: ChangeConflict["severity"] = f.enforcement === "block" ? "block" : "warn";
      if (severity === "block" && r.type === "emergency" && f.allow_emergency) severity = "warn";
      out.push({
        kind: "freeze",
        severity,
        ref: `freeze:${f.id}`,
        start: f.start,
        end: f.end,
        shared_services: shared,
        shared_service_count: shared.length,
        shared_cis: [],
        shared_ci_count: 0,
        hidden: false,
      });
    }
    return out;
  }

  private blockCount(r: Rec): number {
    return this.freezeConflicts(r).filter((c) => c.severity === "block").length;
  }

  private riskCalc(r: Rec) {
    const questions = this.questions();
    const answerPoints = questions.reduce((sum, q) => sum + (q.answers.find((a) => a.key === r.answers[q.question_key])?.points ?? 0), 0);
    const freezes = this.freezeConflicts(r);
    const findings: ChangeRisk["findings"] = [];
    if (freezes.length > 0) {
      const blocking = freezes.filter((c) => c.severity === "block").length;
      findings.push({
        check_key: "core.freeze",
        severity: blocking > 0 ? "block" : "warn",
        points: 10,
        message_key: "itsm.change.risk.findings.freeze",
        params: { count: freezes.length, blocking, truncated: 0 },
        refs: freezes.map((c) => c.ref as string),
        hidden_ref_count: 0,
      });
    }
    if (r.type === "emergency") {
      findings.push({ check_key: "core.emergency", severity: "info", points: 5, message_key: "itsm.change.risk.findings.emergency", params: {}, refs: [], hidden_ref_count: 0 });
    }
    const score = answerPoints + findings.reduce((sum, f) => sum + f.points, 0);
    const level = score >= THRESHOLDS.very_high_from ? "very_high" : score >= THRESHOLDS.high_from ? "high" : score >= THRESHOLDS.medium_from ? "medium" : "low";
    const complete = questions.every((q) => r.answers[q.question_key] !== undefined);
    return { questions, answerPoints, findings, score, level, complete, blocked: findings.some((f) => f.severity === "block") };
  }

  private riskOut(r: Rec): ChangeRisk {
    const c = this.riskCalc(r);
    const evaluated = r.evaluatedAt !== null;
    const level = evaluated ? c.level : null;
    return {
      questions: c.questions,
      answers: r.answers,
      answer_points: c.answerPoints,
      findings: evaluated ? c.findings : [],
      score: evaluated ? c.score : null,
      level,
      override_level: r.overrideLevel,
      override_reason: r.overrideReason,
      effective_level: r.overrideLevel ?? level,
      evaluated_at: r.evaluatedAt,
      questionnaire_complete: c.complete,
      incomplete: !c.complete,
      blocked: evaluated && c.blocked,
      thresholds: THRESHOLDS,
      can_answer: r.phase === "draft" || r.phase === "submitted" || r.phase === "assessment",
      can_override: this.manager,
    };
  }

  private impactOut(r: Rec): ChangeImpact {
    const base = resolveChangeData("/api/v1/itsm/changes/131/impact", "GET", "", "tiqora") as ChangeImpact;
    const computed = r.impactComputedAt !== null;
    return {
      ...base,
      services: r.services.map((s, i) => ({ service_id: s, name: SERVICES[s] ?? `Service ${s}`, direct: i === 0, incident_functionality: null })),
      cis: [],
      incidents: [],
      hidden_service_count: 0,
      hidden_ci_count: 0,
      computed_at: r.impactComputedAt,
      requested_at: r.impactRequestedAt,
      stale: !computed,
    };
  }

  private approvalOut(r: Rec): ChangeApproval {
    const snapshot =
      r.approvalId !== null && r.approvalStatus !== null
        ? {
            approval_id: r.approvalId,
            approval_key: "change_cab",
            status: r.approvalStatus,
            reason: null,
            current_stage: r.approvalStatus === "pending" ? 1 : null,
            round: r.round,
            votes_total: 3,
            votes_approved: r.approvalStatus === "approved" ? 3 : 0,
            votes_rejected: r.approvalStatus === "rejected" ? 2 : 0,
            created_at: iso(NOW),
            decided_at: r.approvalStatus === "pending" ? null : r.approvedAt ?? iso(NOW + 3_600_000),
          }
        : null;
    return { approval_id: r.approvalId, approval_round: r.round, approval_via: r.via, approved_at: r.approvedAt, history_missing: false, snapshot };
  }

  private targets(r: Rec): ChangePhase[] {
    const all = (Object.keys(PAIRS) as string[])
      .filter((k) => k.startsWith(`${r.phase}>`) && PAIRS[k].includes(r.type) && !SYSTEM_ONLY.has(k))
      .map((k) => k.split(">")[1] as ChangePhase);
    // standard_requires_review is off: a standard change closes straight from the implementation
    const fromDemo = new Set(demoAllowedTargets(r.type, r.phase));
    return all.filter((p) => fromDemo.has(p));
  }

  private changeOut(r: Rec): Change {
    const calc = this.riskCalc(r);
    const evaluated = r.evaluatedAt !== null;
    const [state, stateType] = TICKET_STATE[r.phase] ?? ["open", "open"];
    const level = evaluated ? calc.level : null;
    return {
      ticket_id: r.id,
      tn: `20261028${r.id}`,
      title: r.title,
      queue_id: 1,
      ticket_state: state,
      ticket_state_type: stateType,
      change_type: r.type,
      phase: r.phase,
      outcome: r.outcome,
      template_id: r.templateId,
      cab_id: r.cabId,
      planned_start: r.start,
      planned_end: r.end,
      actual_start: r.actualStart,
      actual_end: r.actualEnd,
      reason: r.reason,
      implementation_plan: r.plans.implementation_plan,
      backout_plan: r.plans.backout_plan,
      test_plan: r.plans.test_plan,
      pir_notes: r.pir,
      risk_level: level,
      risk_score: evaluated ? calc.score : null,
      risk_override_level: r.overrideLevel,
      risk_override_reason: r.overrideReason,
      effective_risk_level: r.overrideLevel ?? level,
      approval_round: r.round,
      approval_via: r.via,
      approved_at: r.approvedAt,
      portal_visible: r.portal,
      process_ticket: false,
      service_ids: r.services,
      impact_computed_at: r.impactComputedAt,
      impact_truncated: false,
      drift: [],
      allowed_targets: this.targets(r),
      can_write: true,
      can_manage: this.manager,
      created_at: iso(NOW),
      created_by: 2,
      updated_at: iso(NOW + this.tick * 60_000),
      updated_by: 2,
    };
  }

  private listItem(r: Rec): ChangeListItem {
    const c = this.changeOut(r);
    return {
      ticket_id: r.id,
      tn: c.tn,
      title: r.title,
      queue_id: 1,
      change_type: r.type,
      phase: r.phase,
      outcome: r.outcome,
      owner_id: 2,
      planned_start: r.start,
      planned_end: r.end,
      risk_level: c.effective_risk_level,
      service_ids: r.services,
      updated_at: c.updated_at,
    };
  }

  // ---- create --------------------------------------------------------------------------------
  private create(b: Record<string, unknown>): StoreAnswer {
    const type = (b.change_type as ChangeType | undefined) ?? "normal";
    const templateId = typeof b.template_id === "number" ? b.template_id : null;
    const templates = resolveChangeData("/api/v1/admin/itsm/change/templates", "GET") as {
      id: number;
      valid: boolean;
      is_standard: boolean;
      change_type: ChangeType;
      defaults: { risk_answers?: Record<string, string>; implementation_plan?: string; backout_plan?: string; cab_id?: number; portal_visible?: boolean };
      tasks: { title: string; task_kind: ChangeTask["task_kind"]; offset_minutes: number; duration_minutes: number; position: number; key: string }[];
    }[];
    const template = templates.find((t) => t.id === templateId && t.valid) ?? null;
    if (type === "standard" && !template?.is_standard) return fail(422, "standard_needs_template");
    if (templateId !== null && !template) return fail(422, "template_not_found");
    if (template && template.change_type !== type) return fail(422, "template_type_mismatch");
    const start = (b.planned_start as string | null | undefined) ?? null;
    const end = (b.planned_end as string | null | undefined) ?? null;
    if (start && end && end <= start) return fail(422, "window_invalid");

    const id = this.next++;
    const d = template?.defaults;
    const services = [...new Set([...(typeof b.service_id === "number" ? [b.service_id] : []), ...((b.service_ids as number[] | undefined) ?? [])])];
    const r: Rec = {
      id,
      title: clean(b.title) ?? "Neuer Change",
      type,
      phase: "draft",
      outcome: null,
      templateId: template?.id ?? null,
      cabId: (typeof b.cab_id === "number" ? b.cab_id : d?.cab_id) ?? null,
      start,
      end,
      actualStart: null,
      actualEnd: null,
      reason: clean(b.reason),
      plans: { implementation_plan: d?.implementation_plan ?? null, backout_plan: d?.backout_plan ?? null, test_plan: null },
      pir: null,
      answers: { ...(d?.risk_answers ?? {}) },
      overrideLevel: null,
      overrideReason: null,
      evaluatedAt: null,
      round: 0,
      approvalId: null,
      via: type === "standard" ? "template" : null,
      approvedAt: null,
      approvalStatus: null,
      portal: d?.portal_visible ?? false,
      services,
      impactRequestedAt: services.length > 0 ? iso(NOW) : null,
      impactComputedAt: null,
      tasks: [],
      events: [],
    };
    this.changes.set(id, r);
    this.event(r, "created");
    this.event(r, "phase", { to: "draft" });
    // the tasks job of the template is done by the time the dialog polls it
    for (const t of template?.tasks ?? []) {
      const tid = 400 + id * 10 + t.position;
      r.tasks.push({
        ticket_id: tid,
        tn: `20261028${tid}`,
        title: t.title,
        queue_id: 1,
        owner_id: 2,
        task_kind: t.task_kind,
        position: t.position,
        state: "new",
        state_type: "new",
        finished: false,
        planned_start: null,
        planned_end: null,
        planned_effort_minutes: t.duration_minutes,
        actual_start: null,
        actual_end: null,
        template_task_key: t.key,
      });
    }
    return ok({ change: this.changeOut(r), tasks_job_id: r.tasks.length > 0 ? DEMO_CHANGE_JOB_ID : null, warnings: [] }, 201);
  }

  // ---- update --------------------------------------------------------------------------------
  private update(r: Rec, b: Record<string, unknown>): StoreAnswer {
    if (FINAL.has(r.phase)) return fail(409, "change_final");
    const locked = (name: string): StoreAnswer | null => (r.phase === "approval" ? fail(409, "change_locked_in_approval", { field: name }) : null);
    const changed: string[] = [];
    for (const name of ["reason", "implementation_plan", "backout_plan", "test_plan"] as const) {
      if (b[name] === undefined || b[name] === null) continue;
      const value = clean(b[name]);
      const current = name === "reason" ? r.reason : r.plans[name];
      if (value === current) continue;
      if (name !== "reason") {
        const l = locked(name);
        if (l) return l;
        r.plans[name] = value;
      } else r.reason = value;
      changed.push(name);
    }
    let start = r.start;
    let end = r.end;
    if (b.clear_window === true) start = end = null;
    else {
      start = (b.planned_start as string | undefined) ?? start;
      end = (b.planned_end as string | undefined) ?? end;
    }
    if (start && end && end <= start) return fail(422, "window_invalid");
    if (start !== r.start || end !== r.end) {
      const l = locked("window");
      if (l) return l;
      r.start = start;
      r.end = end;
      changed.push("window");
    }
    if ("cab_id" in b && b.cab_id !== r.cabId) {
      const l = locked("cab_id");
      if (l) return l;
      r.cabId = (b.cab_id as number | null) ?? null;
      changed.push("cab_id");
    }
    if (typeof b.portal_visible === "boolean" && b.portal_visible !== r.portal) {
      r.portal = b.portal_visible;
      changed.push("portal_visible");
    }
    if (changed.length > 0) this.event(r, "updated", { detail: { fields: changed } });
    return ok(this.changeOut(r));
  }

  // ---- transition ----------------------------------------------------------------------------
  private refused(failures: Failure[]): StoreAnswer {
    return fail(409, "transition_refused", { failures });
  }

  /** `check_transition` of `phases.py` for a person (no system steps), with the transition service's extra guards. */
  private guards(r: Rec, to: ChangePhase, b: Record<string, unknown>): Failure[] {
    const pair = `${r.phase}>${to}`;
    const allowed = PAIRS[pair];
    if (!allowed || !allowed.includes(r.type) || !this.targets(r).includes(to)) return [{ code: "transition_not_allowed", overridable: false }];
    if (SYSTEM_ONLY.has(pair)) return [{ code: "system_only", overridable: false }];
    const f: Failure[] = [];
    const hasWindow = r.start !== null && r.end !== null;
    const blocks = this.blockCount(r);
    const overrideReason = clean(b.override_reason);
    const force = b.force === true;
    if (to === "approval" || to === "scheduled" || to === "implementing") {
      if (!hasWindow) f.push({ code: "window_missing", overridable: false });
      if (blocks > 0 && !(this.manager && overrideReason)) f.push({ code: "block_conflict", overridable: true });
    }
    const plansComplete = !!r.plans.implementation_plan && !!r.plans.backout_plan;
    const needsPlans = r.type !== "standard" && ["assessment>approval", "draft>approval"].includes(pair);
    if (needsPlans && !plansComplete) f.push({ code: "plans_missing", overridable: false });
    if (r.type === "normal" && pair === "assessment>approval") {
      if (!this.riskCalc(r).complete) f.push({ code: "questionnaire_incomplete", overridable: false });
      if (r.impactComputedAt === null) f.push({ code: "impact_pending", overridable: false });
    }
    const openTasks = r.tasks.filter((t) => !t.finished).length;
    if ((pair === "implementing>review" || pair === "implementing>closed") && openTasks > 0 && !(this.manager && force)) {
      f.push({ code: "tasks_open", overridable: true });
    }
    if (to === "closed") {
      if (!b.outcome) f.push({ code: "outcome_missing", overridable: false });
      if (pair === "review>closed" && r.type !== "standard" && !(clean(b.pir_notes) ?? r.pir)) f.push({ code: "pir_missing", overridable: false });
    }
    // forcing past a guard needs a reason, listed with the others
    if (this.manager && force && openTasks > 0 && r.phase === "implementing" && !clean(b.reason)) f.push({ code: "reason_missing", overridable: false });
    return f;
  }

  private transition(r: Rec, b: Record<string, unknown>): StoreAnswer {
    const to = b.to as ChangePhase | undefined;
    if (!to) return fail(422, "validation_error");
    const failures = this.guards(r, to, b);
    if (failures.length > 0) return this.refused(failures);

    const from = r.phase;
    const openTasks = r.tasks.filter((t) => !t.finished).length;
    const blocks = this.blockCount(r);
    if (blocks > 0 && this.manager && clean(b.override_reason) && ["approval", "scheduled", "implementing"].includes(to)) {
      this.event(r, "conflict_override", { detail: { block_conflicts: blocks, reason: clean(b.override_reason) } });
    }
    if (b.force === true && this.manager && openTasks > 0 && from === "implementing") {
      this.event(r, "forced", { detail: { open_tasks: openTasks, reason: clean(b.reason) } });
    }
    // the risk is evaluated again before the CAB (and before a scheduling without it)
    if (to === "approval" || (from === "assessment" && to === "scheduled")) r.evaluatedAt = this.stamp();
    if (clean(b.pir_notes)) r.pir = clean(b.pir_notes);
    if (to === "approval") {
      r.round += 1;
      r.approvalId = 700 + r.id + (r.round - 1) * 1000;
      r.via = "cab";
      r.approvalStatus = "pending";
      this.event(r, "approval_requested", { detail: { round: r.round } });
    }
    if (from === "approval" && to === "cancelled") r.approvalStatus = "cancelled";
    if (from === "scheduled" && to === "draft") {
      r.via = r.type === "standard" ? "template" : null;
      r.approvedAt = null;
      r.approvalStatus = r.approvalStatus ? "cancelled" : null;
    }
    if (to === "implementing") r.actualStart = this.stamp();
    if (to === "closed") {
      r.outcome = (b.outcome as string | undefined) ?? null;
      r.actualEnd = this.stamp();
    }
    if (to === "scheduled" && r.type === "standard") r.approvedAt = this.stamp();
    const reason = clean(b.reason);
    this.event(r, "phase", { from, to, detail: reason ? { reason } : null });
    r.phase = to;
    return ok(this.changeOut(r));
  }

  // ---- routes --------------------------------------------------------------------------------
  /** Answers like the backend for the changes this store owns; `undefined` leaves the route to the demo resolver. */
  handle(path: string, method: string, search: string, body: unknown, mode: DemoItsmMode): StoreAnswer | undefined {
    const b = (body ?? {}) as Record<string, unknown>;
    if (mode === "tiqora" && path.includes(ADMIN_PREFIX) && method !== "GET") {
      this.adminWrites.push({ method, path: path.slice(path.indexOf(ADMIN_PREFIX)), body });
      return undefined;
    }
    if (mode !== "tiqora") return undefined;

    if (path.endsWith("/api/v1/itsm/changes")) {
      if (method === "POST") return this.create(b);
      if (method === "GET" && this.changes.size > 0) return this.list(search);
      return undefined;
    }
    const m = path.match(/\/api\/v1\/itsm\/changes\/(\d+)(?:\/([a-z-]+)(?:\/([a-z]+|\d+))?)?$/);
    if (!m) return undefined;
    const r = this.changes.get(Number(m[1]));
    if (!r) return undefined;
    const sub = m[2];
    const leaf = m[3];
    if (!sub) {
      if (method === "GET") return ok(this.changeOut(r));
      if (method === "PATCH") return this.update(r, b);
      return undefined;
    }
    if (sub === "transition" && method === "POST") return this.transition(r, b);
    if (sub === "events" && method === "GET") {
      const params = new URLSearchParams(search);
      const before = Number(params.get("before_id")) || Infinity;
      const limit = Number(params.get("limit")) || 50;
      return ok([...r.events].filter((e) => e.id < before).reverse().slice(0, limit));
    }
    if (sub === "conflicts" && method === "GET") return ok(this.freezeConflicts(r));
    if (sub === "approval" && method === "GET") return ok(this.approvalOut(r));
    if (sub === "related") return ok([]);
    if (sub === "impact") {
      if (leaf === "recompute" && method === "POST") {
        r.impactRequestedAt = r.impactRequestedAt ?? this.stamp();
        r.impactComputedAt = this.stamp();
        return ok({ job_id: DEMO_CHANGE_JOB_ID, status: "queued" }, 202);
      }
      return ok(this.impactOut(r));
    }
    if (sub === "risk") return this.risk(r, leaf, method, b);
    if (sub === "tasks") return this.tasksRoute(r, leaf, method, b);
    return undefined;
  }

  private risk(r: Rec, leaf: string | undefined, method: string, b: Record<string, unknown>): StoreAnswer | undefined {
    if (method === "GET" && !leaf) return ok(this.riskOut(r));
    if (FINAL.has(r.phase)) return fail(409, "change_final");
    if (leaf === "answers" && method === "PUT") {
      const answers = (b.answers ?? {}) as Record<string, string>;
      const known = new Set(this.questions().map((q) => q.question_key));
      if (Object.keys(answers).some((k) => !known.has(k))) return fail(422, "unknown_question");
      r.answers = { ...r.answers, ...answers };
      this.event(r, "updated", { detail: { fields: ["risk_answers"] } });
      return ok(this.riskOut(r));
    }
    if (leaf === "evaluate" && method === "POST") {
      r.evaluatedAt = this.stamp();
      return ok(this.riskOut(r));
    }
    if (leaf === "override" && method === "PUT") {
      if (!this.manager) return fail(403, "forbidden");
      const reason = clean(b.reason);
      if (!reason) return fail(422, "reason_missing");
      r.overrideLevel = (b.level as string | null | undefined) ?? null;
      r.overrideReason = r.overrideLevel ? reason : null;
      this.event(r, "risk_override", { detail: { level: r.overrideLevel, reason } });
      return ok(this.riskOut(r));
    }
    return undefined;
  }

  private tasksRoute(r: Rec, leaf: string | undefined, method: string, b: Record<string, unknown>): StoreAnswer | undefined {
    const sorted = () => [...r.tasks].sort((a, c) => a.position - c.position);
    if (method === "GET") return ok(sorted());
    if (FINAL.has(r.phase)) return fail(409, "change_final");
    if (leaf === "order" && method === "PUT") {
      const order = (b.order ?? []) as number[];
      if (order.length !== r.tasks.length || order.some((x) => !r.tasks.some((t) => t.ticket_id === x))) return fail(422, "order_mismatch");
      order.forEach((tid, i) => {
        r.tasks.find((t) => t.ticket_id === tid)!.position = i + 1;
      });
      return ok(sorted());
    }
    if (!leaf && method === "POST") {
      const title = clean(b.title);
      if (!title) return fail(422, "validation_error");
      const tid = 400 + r.id * 10 + 50 + r.tasks.length;
      const task: ChangeTask = {
        ticket_id: tid,
        tn: `20261028${tid}`,
        title,
        queue_id: 1,
        owner_id: 2,
        task_kind: (b.task_kind as ChangeTask["task_kind"] | undefined) ?? "workorder",
        position: r.tasks.length + 1,
        state: "new",
        state_type: "new",
        finished: false,
        planned_start: null,
        planned_end: null,
        planned_effort_minutes: null,
        actual_start: null,
        actual_end: null,
        template_task_key: null,
      };
      r.tasks.push(task);
      this.event(r, "task_added", { detail: { ticket_id: tid } });
      return ok(task, 201);
    }
    return undefined;
  }

  private list(search: string): StoreAnswer | undefined {
    const params = new URLSearchParams(search);
    const q = (params.get("q") ?? "").trim().toLowerCase();
    const phases = params.getAll("phase");
    const types = params.getAll("change_type");
    const own = [...this.changes.values()].filter(
      (r) => (!q || r.title.toLowerCase().includes(q)) && (phases.length === 0 || phases.includes(r.phase)) && (types.length === 0 || types.includes(r.type)),
    );
    const base = resolveChangeData("/api/v1/itsm/changes", "GET", search, "tiqora") as { items: ChangeListItem[]; total: number; truncated: boolean };
    return ok({ ...base, items: [...own.map((r) => this.listItem(r)), ...base.items], total: base.total + own.length });
  }
}

let current: { info: unknown; store: ChangeStore } | null = null;

/** The change store of the running test attempt (a retry starts from the demo records again). */
export function changeStore(): ChangeStore {
  const info = test.info();
  if (!current || current.info !== info) current = { info, store: new ChangeStore() };
  return current.store;
}

export { fulfillAnswer };
export type { ChangeStore };
