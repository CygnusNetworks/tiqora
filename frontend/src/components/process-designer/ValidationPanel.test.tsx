import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { ProcessDraft, ValidationIssue } from "@/lib/processDesignerApi";
import type { ProcessShape } from "./canvasModel";
import { ValidationPanel } from "./ValidationPanel";
import { targetOfIssue } from "./validationSelect";

const issue = (over: Partial<ValidationIssue>): ValidationIssue => ({
  code: "activity_unreachable",
  severity: "warning",
  entity_type: "Activity",
  entity_id: "Activity-a",
  field: "",
  params: { name: "Antrag" },
  ...over,
});

function renderPanel(issues: ValidationIssue[] | undefined, onSelect = vi.fn(), over: { error?: boolean; loading?: boolean } = {}) {
  render(
    <I18nextProvider i18n={i18n}>
      <ValidationPanel
        issues={issues}
        loading={over.loading ?? false}
        error={over.error ?? false}
        onSelect={onSelect}
        onRecheck={vi.fn()}
      />
    </I18nextProvider>,
  );
  return onSelect;
}

describe("ValidationPanel", () => {
  it("groups by severity, errors first, with interpolated texts", () => {
    renderPanel([
      issue({}),
      issue({ code: "reference_missing", severity: "error", entity_type: "Process", entity_id: "Process-1", params: { id: "Activity-x", kind: "Activity" } }),
    ]);
    const groups = screen.getAllByTestId(/validation-group-/).map((g) => g.dataset.testid);
    expect(groups).toEqual(["validation-group-error", "validation-group-warning"]);
    expect(within(screen.getByTestId("validation-group-error")).getByText(i18n.t("admin.processDesigner.validation.code.reference_missing", { id: "Activity-x", kind: "Activity" }))).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.processDesigner.validation.code.activity_unreachable"))).toBeInTheDocument();
  });

  it("reports the clicked issue", () => {
    const first = issue({ entity_id: "Activity-b", params: { name: "Genehmigung" } });
    const onSelect = renderPanel([first]);
    fireEvent.click(screen.getByTestId("validation-issue"));
    expect(onSelect).toHaveBeenCalledWith(first);
  });

  it("translates the reason of an impossible condition value instead of showing the code", async () => {
    await i18n.changeLanguage("de");
    renderPanel([
      issue({
        code: "condition_value_impossible",
        severity: "error",
        entity_type: "Transition",
        params: { field: "DynamicField_Farbe", value: "lila", reason: "possible_values" },
      }),
    ]);
    const text = screen.getByTestId("validation-issue").textContent ?? "";
    expect(text).toContain("lila");
    expect(text).not.toContain("possible_values");
    expect(text).toContain(i18n.t("admin.processDesigner.validation.reason.possible_values"));
  });

  it("falls back to the code for an unknown code", () => {
    renderPanel([issue({ code: "brand_new_code" })]);
    expect(screen.getByText("brand_new_code")).toBeInTheDocument();
  });

  it("says so when there is nothing to report and when the check failed", () => {
    renderPanel([]);
    expect(screen.getByTestId("validation-none")).toBeInTheDocument();
  });

  it("shows the load error", () => {
    renderPanel(undefined, vi.fn(), { error: true });
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("admin.processDesigner.validation.loadError"));
  });
});

describe("targetOfIssue", () => {
  const shape = {
    path: {
      "Activity-a": { "Transition-1": { ActivityEntityID: "Activity-b", TransitionAction: ["TransitionAction-9"] } },
      "Activity-b": {},
    },
    layout: {},
    startActivity: "Activity-a",
    startActivityDialog: "ActivityDialog-1",
  } as unknown as ProcessShape;
  const draft = {
    activities: [{ entity_id: "Activity-a", config: { ActivityDialog: { "1": "ActivityDialog-1" } } }],
  } as unknown as ProcessDraft;

  it("selects an activity, a transition (with its source), an action and the owner of a dialog", () => {
    expect(targetOfIssue(issue({}), draft, shape)).toEqual({ selection: { kind: "activity", id: "Activity-a" }, actionId: null });
    expect(targetOfIssue(issue({ entity_type: "Transition", entity_id: "Transition-1" }), draft, shape).selection).toEqual({
      kind: "transition",
      from: "Activity-a",
      transitionId: "Transition-1",
    });
    expect(targetOfIssue(issue({ entity_type: "TransitionAction", entity_id: "TransitionAction-9" }), draft, shape)).toEqual({
      selection: { kind: "transition", from: "Activity-a", transitionId: "Transition-1" },
      actionId: "TransitionAction-9",
    });
    expect(targetOfIssue(issue({ entity_type: "ActivityDialog", entity_id: "ActivityDialog-1" }), draft, shape).selection).toEqual({
      kind: "activity",
      id: "Activity-a",
    });
  });

  it("selects nothing for process-level issues and unplaced entities", () => {
    expect(targetOfIssue(issue({ entity_type: "Process", entity_id: "Process-1" }), draft, shape).selection).toBeNull();
    expect(targetOfIssue(issue({ entity_type: "Transition", entity_id: "Transition-404" }), draft, shape).selection).toBeNull();
  });
});
