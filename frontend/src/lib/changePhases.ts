/**
 * Display model of the change phases (mirror of `backend/src/tiqora/itsm/change/phases.py`).
 * The backend decides which step is allowed (`allowed_targets` of `ChangeOut`); this only draws the
 * path a change of each type normally takes.
 */
import type { ChangePhase, ChangeType, RiskLevel } from "./changeApi";

export const CHANGE_PHASES: readonly ChangePhase[] = [
  "draft",
  "submitted",
  "assessment",
  "approval",
  "scheduled",
  "implementing",
  "review",
  "closed",
  "rejected",
  "cancelled",
];

export const CHANGE_TYPES: readonly ChangeType[] = ["standard", "normal", "emergency"];

export const RISK_LEVELS: readonly RiskLevel[] = ["low", "medium", "high", "very_high"];

/** The regular path per type; a standard change is pre-approved by its template, an emergency change skips the assessment. */
export const PHASE_PATH: Record<ChangeType, readonly ChangePhase[]> = {
  standard: ["draft", "scheduled", "implementing", "closed"],
  normal: ["draft", "submitted", "assessment", "approval", "scheduled", "implementing", "review", "closed"],
  emergency: ["draft", "approval", "scheduled", "implementing", "review", "closed"],
};

export type PhaseStepState = "done" | "current" | "upcoming";

export type PhaseStep = { phase: ChangePhase; state: PhaseStepState };

/**
 * Steps of the phase bar. Off-path phases are drawn as an extra last step: `rejected` after the
 * approval it came from (everything up to the approval counts as done), `cancelled` after the
 * steps of the path (nothing is known about how far it got, so none counts as done). A standard
 * change that is in `review` (setting `standard_requires_review`) gets that step before `closed`.
 */
export function phaseSteps(changeType: ChangeType, phase: ChangePhase): PhaseStep[] {
  const path = [...PHASE_PATH[changeType]];
  if (phase === "review" && !path.includes("review")) path.splice(path.indexOf("closed"), 0, "review");
  if (phase === "rejected" || phase === "cancelled") {
    const reached = phase === "rejected" ? path.indexOf("approval") : -1;
    return [
      ...path.map((p, i): PhaseStep => ({ phase: p, state: i <= reached ? "done" : "upcoming" })),
      { phase, state: "current" },
    ];
  }
  const at = path.indexOf(phase);
  return path.map((p, i): PhaseStep => ({ phase: p, state: i < at ? "done" : i === at ? "current" : "upcoming" }));
}

export function isRiskLevel(value: string | null | undefined): value is RiskLevel {
  return (RISK_LEVELS as readonly string[]).includes(value ?? "");
}
