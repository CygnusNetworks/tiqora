/** Shared fixtures of the change detail tests. */
import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { Change, ChangeApproval, ChangeImpact, ChangeRisk, ChangeTask } from "@/lib/changeApi";

export function makeChange(over: Partial<Change> = {}): Change {
  return {
    ticket_id: 130,
    tn: "20261012130",
    title: "Switch-Firmware aktualisieren",
    queue_id: 1,
    ticket_state: "open",
    ticket_state_type: "open",
    change_type: "normal",
    phase: "assessment",
    outcome: null,
    template_id: null,
    cab_id: 1,
    planned_start: "2026-10-24T20:00:00Z",
    planned_end: "2026-10-25T00:00:00Z",
    actual_start: null,
    actual_end: null,
    reason: "Bekannte Sicherheitslücke",
    implementation_plan: "Firmware einspielen",
    backout_plan: "Alte Firmware zurückspielen",
    test_plan: "Ping auf alle Ports",
    pir_notes: null,
    risk_score: 12,
    risk_level: "medium",
    risk_override_level: null,
    risk_override_reason: null,
    effective_risk_level: "medium",
    approval_via: null,
    approval_round: 0,
    approved_at: null,
    impact_computed_at: "2026-10-12T08:00:00Z",
    impact_truncated: false,
    portal_visible: false,
    service_ids: [1],
    allowed_targets: ["approval", "cancelled"],
    drift: [],
    can_write: true,
    can_manage: false,
    process_ticket: false,
    created_at: "2026-10-12T08:00:00Z",
    created_by: 1,
    updated_at: "2026-10-12T08:00:00Z",
    updated_by: 1,
    ...over,
  };
}

export function makeTask(id: number, over: Partial<ChangeTask> = {}): ChangeTask {
  return {
    ticket_id: id,
    tn: `2026101${id}`,
    title: `Aufgabe ${id}`,
    queue_id: 1,
    owner_id: 1,
    state: "open",
    state_type: "open",
    finished: false,
    task_kind: "workorder",
    position: 1,
    planned_start: null,
    planned_end: null,
    actual_start: null,
    actual_end: null,
    planned_effort_minutes: null,
    template_task_key: null,
    ...over,
  };
}

export function makeRisk(over: Partial<ChangeRisk> = {}): ChangeRisk {
  return {
    questions: [
      {
        id: 1,
        question_key: "tested",
        question: "Wurde die Änderung getestet?",
        help: null,
        position: 1,
        valid: true,
        answers: [
          { key: "yes", label: "Ja", points: 0 },
          { key: "no", label: "Nein", points: 6 },
        ],
      },
    ],
    answers: { tested: "yes" },
    answer_points: 0,
    questionnaire_complete: true,
    score: 12,
    level: "medium",
    override_level: null,
    override_reason: null,
    effective_level: "medium",
    evaluated_at: "2026-10-12T08:00:00Z",
    findings: [],
    blocked: false,
    incomplete: false,
    thresholds: { medium_from: 10, high_from: 20, very_high_from: 35 },
    can_answer: true,
    can_override: false,
    ...over,
  };
}

export function makeImpact(over: Partial<ChangeImpact> = {}): ChangeImpact {
  return {
    computed_at: "2026-10-12T08:00:00Z",
    requested_at: "2026-10-12T08:00:00Z",
    stale: false,
    truncated: false,
    cis: [],
    hidden_ci_count: 0,
    hidden_service_count: 0,
    services: [],
    incidents: [],
    incidents_truncated: false,
    ...over,
  };
}

export function makeApproval(over: Partial<ChangeApproval> = {}): ChangeApproval {
  return {
    approval_id: 501,
    approval_round: 1,
    approval_via: "cab",
    approved_at: null,
    history_missing: false,
    snapshot: {
      approval_id: 501,
      approval_key: "change_cab",
      status: "pending",
      reason: null,
      round: 1,
      current_stage: 1,
      created_at: "2026-10-12T08:00:00Z",
      decided_at: null,
      votes_total: 3,
      votes_approved: 1,
      votes_rejected: 0,
    },
    ...over,
  };
}

/** A change API error as the backend sends it: `{detail: {code, ...}}`. */
export function changeError(status: number, detail: Record<string, unknown>): ApiError {
  return new ApiError(status, { detail }, "/api/v1/itsm/changes");
}

export function renderWithClient(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
  return { ...result, qc };
}
