import { beforeAll, describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { DefinitionEditor } from "./DefinitionEditor";
import {
  definitionComplete,
  normalizeStages,
  stepProblems,
  type ApprovalDefinition,
  type ApprovalStepSpec,
} from "./definitionLogic";
import { mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const step = (name: string, stage: number, over: Partial<ApprovalStepSpec> = {}): ApprovalStepSpec => ({
  name,
  stage,
  rule: "any",
  quorum: null,
  approvers: [{ kind: "user", ref: "2" }],
  due_hours: null,
  on_timeout: "notify",
  on_no_approver: "reject",
  escalation: [],
  allow_delegation: true,
  allow_email: true,
  ...over,
});

let latest: ApprovalDefinition = { steps: [] };

function Harness({ initial, allowAutoApprove }: { initial: ApprovalDefinition; allowAutoApprove?: boolean }) {
  const [value, setValue] = useState(initial);
  latest = value;
  return <DefinitionEditor value={value} onChange={setValue} subjectType="ticket" allowCustomers={false} allowAutoApprove={allowAutoApprove} />;
}

const stages = () => latest.steps.map((s) => s.stage);
const names = () => latest.steps.map((s) => s.name);

async function pick(testId: string, optionValue: string | number) {
  fireEvent.click(screen.getByTestId(testId));
  fireEvent.click(await screen.findByTestId(`${testId}-menu-option-${optionValue}`));
}

describe("normalizeStages", () => {
  it("closes gaps and keeps the order of equal stages", () => {
    const out = normalizeStages([step("a", 4), step("b", 2), step("c", 4), step("d", 9)]);
    expect(out.map((s) => [s.name, s.stage])).toEqual([
      ["b", 1],
      ["a", 2],
      ["c", 2],
      ["d", 3],
    ]);
  });
});

describe("stepProblems", () => {
  it.each([
    [step("", 1), ["name"]],
    [step("x", 1, { approvers: [] }), ["approvers"]],
    [step("x", 1, { approvers: [{ kind: "group", ref: "" }] }), ["approvers"]],
    [step("x", 1, { rule: "quorum", quorum: null }), ["quorum"]],
    [step("x", 1, { rule: "quorum", quorum: 0 }), ["quorum"]],
    [step("x", 1, { rule: "quorum", quorum: 2 }), []],
    [step("x", 1, { on_timeout: "escalate" }), ["escalation"]],
    [step("x", 1, { on_timeout: "escalate", escalation: [{ kind: "user", ref: "3" }] }), []],
    [step("x", 1), []],
  ] as [ApprovalStepSpec, string[]][])("%j", (s, expected) => {
    expect(stepProblems(s)).toEqual(expected);
  });

  it("definitionComplete needs at least one step and all steps complete", () => {
    expect(definitionComplete({ steps: [] })).toBe(false);
    expect(definitionComplete({ steps: [step("a", 1), step("", 2)] })).toBe(false);
    expect(definitionComplete({ steps: [step("a", 1), step("b", 2)] })).toBe(true);
  });
});

describe("DefinitionEditor", () => {
  it("shows one card per step with its stage", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("Prüfung", 1), step("Leitung", 2)] }} />);
    const cards = screen.getAllByTestId("definition-step");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByTestId("step-0-stage-label")).toHaveTextContent("Stufe 1");
    expect(within(cards[1]).getByTestId("step-1-stage-label")).toHaveTextContent("Stufe 2");
    expect(screen.getByTestId("step-0-name")).toHaveValue("Prüfung");
  });

  it("adds a step as a new stage and removes one without leaving a gap", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 2)] }} />);
    fireEvent.click(screen.getByTestId("definition-add-step"));
    expect(stages()).toEqual([1, 2, 3]);
    expect(latest.steps[2]).toMatchObject({ name: "", rule: "any", allow_delegation: true, allow_email: true });
    fireEvent.click(screen.getByTestId("step-1-remove"));
    expect(names()).toEqual(["a", ""]);
    expect(stages()).toEqual([1, 2]);
  });

  it("cannot remove the only step", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1)] }} />);
    expect(screen.getByTestId("step-0-remove")).toBeDisabled();
  });

  it("puts a step into a parallel stage and renumbers the rest contiguously", async () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 2), step("c", 3)] }} />);
    await pick("step-2-stage", 1);
    expect(names()).toEqual(["a", "c", "b"]);
    expect(stages()).toEqual([1, 1, 2]);
  });

  it("renumbers when a step is sent to a new last stage", async () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 2), step("c", 3)] }} />);
    await pick("step-0-stage", 4);
    expect(names()).toEqual(["b", "c", "a"]);
    expect(stages()).toEqual([1, 2, 3]);
  });

  it("moves a step down across stages", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 2)] }} />);
    expect(screen.getByTestId("step-0-up")).toBeDisabled();
    fireEvent.click(screen.getByTestId("step-0-down"));
    expect(names()).toEqual(["b", "a"]);
    expect(stages()).toEqual([1, 2]);
  });

  it("moves a step within a stage without changing stages", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 1)] }} />);
    fireEvent.click(screen.getByTestId("step-1-up"));
    expect(names()).toEqual(["b", "a"]);
    expect(stages()).toEqual([1, 1]);
  });

  it("requires a quorum for the rule Mindestanzahl", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1)] }} />);
    expect(screen.queryByTestId("step-0-quorum")).toBeNull();
    fireEvent.click(screen.getByTestId("step-0-rule-quorum"));
    expect(latest.steps[0]).toMatchObject({ rule: "quorum", quorum: 1 });
    fireEvent.change(screen.getByTestId("step-0-quorum"), { target: { value: "" } });
    expect(latest.steps[0].quorum).toBeNull();
    expect(screen.getByTestId("step-0-error-quorum")).toHaveTextContent("Bitte geben Sie an, wie viele Freigaben nötig sind.");
    fireEvent.change(screen.getByTestId("step-0-quorum"), { target: { value: "2" } });
    expect(latest.steps[0].quorum).toBe(2);
    expect(screen.queryByTestId("step-0-error-quorum")).toBeNull();
    fireEvent.click(screen.getByTestId("step-0-rule-all"));
    expect(latest.steps[0]).toMatchObject({ rule: "all", quorum: null });
    expect(screen.queryByTestId("step-0-quorum")).toBeNull();
  });

  it("shows inline problems for name and approvers", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("", 1, { approvers: [] })] }} />);
    expect(screen.getByTestId("step-0-error-name")).toHaveTextContent("Bitte geben Sie dem Schritt einen Namen.");
    expect(screen.getByTestId("step-0-error-approvers")).toHaveTextContent(
      "Bitte nennen Sie mindestens einen Freigeber und füllen Sie jede Zeile aus.",
    );
    fireEvent.change(screen.getByTestId("step-0-name"), { target: { value: "Prüfung" } });
    expect(screen.queryByTestId("step-0-error-name")).toBeNull();
  });

  it("asks for escalation targets only when the deadline escalates", async () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1)] }} />);
    expect(screen.queryByText("Eskalation an")).toBeNull();
    await pick("step-0-timeout", "escalate");
    expect(latest.steps[0].on_timeout).toBe("escalate");
    expect(screen.getByText("Eskalation an")).toBeInTheDocument();
    expect(screen.getByTestId("step-0-error-escalation")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("step-0-escalation-add"));
    expect(latest.steps[0].escalation).toEqual([{ kind: "user", ref: "" }]);
    fireEvent.click(screen.getByTestId("step-0-escalation-ref-0"));
    fireEvent.click(await screen.findByTestId("step-0-escalation-ref-0-menu-option-3"));
    expect(latest.steps[0].escalation).toEqual([{ kind: "user", ref: "3" }]);
    expect(screen.queryByTestId("step-0-error-escalation")).toBeNull();
  });

  it("offers approve-on-timeout only where allowed", async () => {
    mockApi();
    const { unmount } = renderApproval(<Harness initial={{ steps: [step("a", 1)] }} />);
    fireEvent.click(screen.getByTestId("step-0-timeout"));
    await screen.findByTestId("step-0-timeout-menu-option-reject");
    expect(screen.queryByTestId("step-0-timeout-menu-option-approve")).toBeNull();
    unmount();
    renderApproval(<Harness initial={{ steps: [step("a", 1)] }} allowAutoApprove />);
    fireEvent.click(screen.getByTestId("step-0-timeout"));
    expect(await screen.findByTestId("step-0-timeout-menu-option-approve")).toBeInTheDocument();
  });

  it("shows a stored approve-on-timeout as a disabled, explained option", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1, { on_timeout: "approve" })] }} />);
    expect(screen.getByTestId("step-0-timeout")).toHaveTextContent("Automatisch freigeben (nur in Vorlagen)");
    expect(latest.steps[0].on_timeout).toBe("approve");
  });

  it("keeps each step's own state when a step is removed", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1), step("b", 2, { approvers: [{ kind: "customer_user", ref: "" }] })] }} />);
    fireEvent.change(screen.getByTestId("step-1-approvers-ref-0-search"), { target: { value: "kunde" } });
    fireEvent.click(screen.getByTestId("step-0-remove"));
    expect(screen.getByTestId("step-0-approvers-ref-0-search")).toHaveValue("kunde");
  });

  it("switches forwarding and e-mail decisions and sets the deadline", () => {
    mockApi();
    renderApproval(<Harness initial={{ steps: [step("a", 1)] }} />);
    fireEvent.click(screen.getByRole("switch", { name: "Weiterleiten erlaubt" }));
    fireEvent.click(screen.getByRole("switch", { name: "Entscheidung per E-Mail-Link" }));
    expect(latest.steps[0]).toMatchObject({ allow_delegation: false, allow_email: false });
    fireEvent.change(screen.getByTestId("step-0-due"), { target: { value: "48" } });
    expect(latest.steps[0].due_hours).toBe(48);
    fireEvent.change(screen.getByTestId("step-0-due"), { target: { value: "" } });
    expect(latest.steps[0].due_hours).toBeNull();
  });
});
