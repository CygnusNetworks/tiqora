import type { Schemas } from "@tiqora/api-client";

export type ApprovalDefinition = Schemas["ApprovalDefinition"];
export type ApprovalStepSpec = Schemas["StepSpec"];

/** The API accepts at most this many steps. */
export const MAX_APPROVAL_STEPS = 10;

export type StepProblem = "name" | "approvers" | "quorum" | "escalation";

/**
 * Renumbers the stages to 1..k without gaps and orders the steps by stage (stable); `keys` (one per
 * step, optional) are reordered the same way.
 */
export function normalizeStagesKeyed<K>(
  steps: ApprovalStepSpec[],
  keys: K[],
): { steps: ApprovalStepSpec[]; keys: K[] } {
  const stages = [...new Set(steps.map((s) => s.stage ?? 1))].sort((a, b) => a - b);
  const order = steps
    .map((s, i) => ({ s, i }))
    .sort((a, b) => (a.s.stage ?? 1) - (b.s.stage ?? 1) || a.i - b.i);
  return {
    steps: order.map(({ s }) => ({ ...s, stage: stages.indexOf(s.stage ?? 1) + 1 })),
    keys: order.map(({ i }) => keys[i]),
  };
}

/** Renumbers the stages to 1..k without gaps, in ascending order of the stage numbers given. */
export function normalizeStages(steps: ApprovalStepSpec[]): ApprovalStepSpec[] {
  return normalizeStagesKeyed(steps, steps.map(() => 0)).steps;
}

/** What is still missing in one step; an empty list means the step can be saved. */
export function stepProblems(step: ApprovalStepSpec): StepProblem[] {
  const out: StepProblem[] = [];
  if (step.name.trim() === "") out.push("name");
  if (step.approvers.length === 0 || step.approvers.some((a) => a.ref.trim() === "")) out.push("approvers");
  if (step.rule === "quorum" && !(step.quorum != null && step.quorum >= 1)) out.push("quorum");
  if (step.on_timeout === "escalate" && (step.escalation ?? []).filter((e) => e.ref.trim() !== "").length === 0) {
    out.push("escalation");
  }
  return out;
}

/** True when every step of the definition is complete. */
export function definitionComplete(value: ApprovalDefinition): boolean {
  return value.steps.length > 0 && value.steps.every((s) => stepProblems(s).length === 0);
}
