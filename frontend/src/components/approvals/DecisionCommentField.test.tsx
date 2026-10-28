import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { DecisionCommentField } from "./DecisionCommentField";
import { renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const show = (props: Partial<Parameters<typeof DecisionCommentField>[0]> = {}) => {
  const onChange = vi.fn();
  renderApproval(
    <DecisionCommentField value="" onChange={onChange} decision={null} showError={false} testIdPrefix="x" {...props} />,
  );
  return onChange;
};

describe("DecisionCommentField", () => {
  it.each([
    [null, "Kommentar (bei Ablehnung erforderlich)"],
    ["approved", "Kommentar (optional)"],
    ["rejected", "Begründung der Ablehnung"],
  ] as const)("labels the box for decision %s", (decision, label) => {
    show({ decision });
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  });

  it("counts, passes typing on and caps at 2000", () => {
    const onChange = show({ value: "abc" });
    expect(screen.getByTestId("x-counter")).toHaveTextContent("3 / 2000");
    expect(screen.getByTestId("x-comment")).toHaveAttribute("maxlength", "2000");
    fireEvent.change(screen.getByTestId("x-comment"), { target: { value: "abcd" } });
    expect(onChange).toHaveBeenCalledWith("abcd");
  });

  it("shows the missing-reason error only for a rejection without text once asked", () => {
    show({ decision: "rejected", showError: true });
    expect(screen.getByTestId("x-comment-error")).toHaveTextContent("Bitte geben Sie eine Begründung für die Ablehnung an.");
    expect(screen.getByTestId("x-comment")).toHaveAttribute("aria-invalid", "true");
  });

  it.each([
    ["approved", "", true],
    ["rejected", "Zu riskant", true],
    ["rejected", "", false],
  ] as const)("no error for %s with %j (showError %s)", (decision, value, showError) => {
    show({ decision, value, showError });
    expect(screen.getByTestId("x-comment-error")).toHaveTextContent("");
  });
});
