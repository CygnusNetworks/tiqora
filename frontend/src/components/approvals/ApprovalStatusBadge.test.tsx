import { beforeAll, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { ApprovalStatusBadge } from "./ApprovalStatusBadge";
import { renderApproval, useGerman } from "./approvalsTestHarness";

beforeAll(useGerman);

describe("ApprovalStatusBadge", () => {
  it.each([
    ["pending", "Offen"],
    ["approved", "Freigegeben"],
    ["rejected", "Abgelehnt"],
    ["cancelled", "Zurückgezogen"],
  ])("labels %s as %s", (status, label) => {
    renderApproval(<ApprovalStatusBadge status={status} />);
    const badge = screen.getByTestId("approval-status-badge");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveAttribute("data-status", status);
    expect(screen.queryByTestId("approval-overdue-badge")).toBeNull();
  });

  it("adds a danger chip when overdue", () => {
    renderApproval(<ApprovalStatusBadge status="pending" overdue />);
    const chip = screen.getByTestId("approval-overdue-badge");
    expect(chip).toHaveTextContent("überfällig");
    expect(chip.className).toContain("text-danger");
  });

  it("shows an unknown status as it is", () => {
    renderApproval(<ApprovalStatusBadge status="mystery" />);
    expect(screen.getByTestId("approval-status-badge")).toHaveTextContent("mystery");
  });
});
