import { beforeAll, describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { ApprovalAuditList } from "./ApprovalAuditList";
import { demoDetail, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

describe("ApprovalAuditList", () => {
  it("starts collapsed and names the number of entries", () => {
    renderApproval(<ApprovalAuditList events={demoDetail(501).events} />);
    expect(screen.getByTestId("approval-audit-toggle")).toHaveTextContent("Verlauf (3)");
    expect(screen.getByTestId("approval-audit-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("approval-audit-list")).toBeNull();
  });

  it("expands to German action labels with the actor", () => {
    renderApproval(<ApprovalAuditList events={demoDetail(501).events} />);
    fireEvent.click(screen.getByTestId("approval-audit-toggle"));
    expect(screen.getAllByTestId("approval-audit-action").map((e) => e.textContent)).toEqual([
      "Angefordert",
      "Benachrichtigt",
      "Abgestimmt",
    ]);
    const first = screen.getAllByTestId("approval-audit-item")[0];
    expect(first).toHaveTextContent("Max Beispiel");
  });

  it("labels the escalation actions of the overdue demo request", () => {
    renderApproval(<ApprovalAuditList events={demoDetail(503).events} />);
    fireEvent.click(screen.getByTestId("approval-audit-toggle"));
    expect(screen.getAllByTestId("approval-audit-action").map((e) => e.textContent)).toEqual([
      "Angefordert",
      "Frist überschritten",
      "Eskaliert",
    ]);
  });

  it("falls back to the raw action and shows an empty hint", () => {
    renderApproval(
      <ApprovalAuditList events={[{ at: "2026-10-12T08:00:00Z", action: "brand_new", actor_label: "System" }]} />,
    );
    fireEvent.click(screen.getByTestId("approval-audit-toggle"));
    expect(screen.getByTestId("approval-audit-action")).toHaveTextContent("brand_new");
  });

  it("says so when there are no entries", () => {
    renderApproval(<ApprovalAuditList events={[]} />);
    fireEvent.click(screen.getByTestId("approval-audit-toggle"));
    expect(screen.getByText("Noch keine Einträge.")).toBeInTheDocument();
  });
});
