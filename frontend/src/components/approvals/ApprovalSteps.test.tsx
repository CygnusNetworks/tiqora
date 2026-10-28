import { beforeAll, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { ApprovalSteps } from "./ApprovalSteps";
import { demoDetail, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

describe("ApprovalSteps", () => {
  it("groups the CAB demo request into two stages with rule line and progress", () => {
    renderApproval(<ApprovalSteps detail={demoDetail(501)} />);
    const stages = screen.getAllByTestId("approval-stage");
    expect(stages.map((s) => s.getAttribute("data-stage"))).toEqual(["1", "2"]);
    expect(within(stages[0]).getByText("Stufe 1")).toBeInTheDocument();
    expect(within(stages[1]).getByText("Stufe 2")).toBeInTheDocument();
    const cab = within(stages[0]).getByTestId("approval-step");
    expect(within(cab).getByTestId("approval-step-rule")).toHaveTextContent("Mehrheit nötig (2 Freigaben)");
    expect(within(cab).getByTestId("approval-step-progress")).toHaveTextContent("1 von 2 Freigaben");
    expect(within(cab).getAllByTestId("approval-approver")).toHaveLength(3);
  });

  it("shows source label, vote chip and comment of an approver", () => {
    renderApproval(<ApprovalSteps detail={demoDetail(501)} />);
    const first = screen.getAllByTestId("approval-approver")[0];
    expect(within(first).getByText("Erika Muster")).toBeInTheDocument();
    expect(within(first).getByTestId("approval-approver-source")).toHaveTextContent("über Freigabegruppe CAB Netzwerk");
    expect(within(first).getByTestId("approval-status-badge")).toHaveTextContent("Freigegeben");
    expect(within(first).getByTestId("approval-vote-comment")).toHaveTextContent("Rückfallplan ist ausreichend.");
  });

  it("words the source of a group, a role and an unnamed kind", () => {
    const detail = demoDetail(501);
    const [a, b, c] = detail.steps[0].approvers;
    a.source_kind = "group";
    a.source_label = "Betriebsleitung";
    b.source_kind = "role";
    b.source_label = "Netzwerk";
    c.source_kind = "user";
    c.source_label = null;
    renderApproval(<ApprovalSteps detail={detail} />);
    const texts = screen.getAllByTestId("approval-approver-source").map((e) => e.textContent);
    expect(texts).toEqual(["über Gruppe Betriebsleitung", "über Rolle Netzwerk", "Direkt benannt"]);
  });

  it("shows a stage that has not started without a count and without approvers", () => {
    renderApproval(<ApprovalSteps detail={demoDetail(501)} />);
    const [, waiting] = screen.getAllByTestId("approval-step");
    expect(within(waiting).getByTestId("approval-step-rule")).toHaveTextContent("Eine Freigabe genügt");
    expect(within(waiting).getByTestId("approval-step-starts-after")).toHaveTextContent("Startet nach Stufe 1");
    expect(within(waiting).getByTestId("approval-step-approvers-later")).toHaveTextContent(
      "Die Freigeber werden bestimmt, sobald diese Stufe beginnt.",
    );
    expect(within(waiting).queryByTestId("approval-approver")).toBeNull();
    expect(within(waiting).queryByTestId("approval-step-progress")).toBeNull();
    expect(waiting.textContent).not.toMatch(/\b0 Freigaben|\(0/);
  });

  it.each([
    ["majority", null, "Mehrheit nötig"],
    ["quorum", 2, "2 Freigaben nötig"],
    ["all", null, "Alle Freigeber müssen zustimmen"],
  ])("a waiting %s step names no voter count it cannot know", (rule, quorum, text) => {
    const detail = demoDetail(501);
    Object.assign(detail.steps[1], { rule, quorum, required: null });
    renderApproval(<ApprovalSteps detail={detail} />);
    const line = within(screen.getAllByTestId("approval-step")[1]).getByTestId("approval-step-rule");
    expect(line).toHaveTextContent(text);
    expect(line.textContent).not.toContain("(");
  });

  it("stacks on phones and goes side by side from md up", () => {
    const detail = demoDetail(501);
    detail.steps[1].stage = 1; // two parallel steps in one stage
    renderApproval(<ApprovalSteps detail={detail} />);
    expect(screen.getAllByTestId("approval-stage")).toHaveLength(1);
    const grid = within(screen.getByTestId("approval-stage")).getAllByTestId("approval-step")[0].parentElement!;
    expect(grid.className).toContain("grid-cols-1");
    expect(grid.className).toContain("md:grid-flow-col");
    expect(within(grid).getAllByTestId("approval-step")).toHaveLength(2);
  });

  it("names the substitute who voted", () => {
    const detail = demoDetail(501);
    detail.steps[0].approvers[0].vote!.on_behalf = true;
    detail.steps[0].approvers[0].vote!.decided_by_label = "Alex Turner";
    renderApproval(<ApprovalSteps detail={detail} />);
    expect(screen.getByTestId("approval-on-behalf")).toHaveTextContent("Alex Turner hat in Vertretung abgestimmt");
  });

  it("marks an overdue deadline in the danger colour", () => {
    renderApproval(<ApprovalSteps detail={demoDetail(503)} />);
    const due = screen.getByTestId("approval-step-due");
    expect(due.className).toContain("text-danger");
    expect(due).toHaveTextContent("überfällig");
    expect(screen.getByTestId("approval-overdue-badge")).toBeInTheDocument();
    expect(screen.getAllByTestId("approval-approver-source").map((e) => e.textContent)).toContain("Eskalation");
  });

  it("keeps a future deadline neutral", () => {
    renderApproval(<ApprovalSteps detail={demoDetail(501)} />);
    const due = screen.getByTestId("approval-step-due");
    expect(due.className).not.toContain("text-danger");
    expect(due).not.toHaveTextContent("überfällig");
  });

  it.each([
    ["any", null, "Eine Freigabe genügt"],
    ["all", null, "Alle Freigeber müssen zustimmen"],
    ["quorum", 2, "2 Freigaben nötig"],
  ])("writes the %s rule in plain words", (rule, required, text) => {
    const detail = demoDetail(502);
    detail.steps[0].rule = rule;
    detail.steps[0].required = required;
    renderApproval(<ApprovalSteps detail={detail} />);
    expect(screen.getByTestId("approval-step-rule")).toHaveTextContent(text);
  });
});
