import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";
import { ApprovalsOverviewPage } from "./ApprovalsOverviewPage";

beforeAll(useGerman);

const OVERVIEW = "GET /api/v1/admin/approvals";
const page = () => resolveApprovalsData("/api/v1/admin/approvals", "GET");

describe("ApprovalsOverviewPage", () => {
  it("lists the requests with status, settings link and overdue chip", async () => {
    mockApi({ [OVERVIEW]: page });
    renderApproval(<ApprovalsOverviewPage />);
    const rows = await screen.findAllByTestId("admin-approval-row");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getAllByTestId("approval-status-badge")[0]).toHaveAttribute("data-status", "pending");
    expect(screen.getAllByTestId("approval-overdue-badge")).toHaveLength(1);
    expect(screen.getByTestId("admin-approvals-settings-link")).toHaveAttribute("href", "/admin/settings/approvals");
    expect(screen.getByTestId("admin-approvals-settings-link")).toHaveTextContent("Einstellungen");
  });

  it("sends the filters to the server", async () => {
    const overview = vi.fn(page);
    mockApi({ [OVERVIEW]: overview });
    renderApproval(<ApprovalsOverviewPage />);
    await screen.findAllByTestId("admin-approval-row");
    fireEvent.change(screen.getByTestId("admin-approvals-filter-ticket"), { target: { value: " 20261012130 " } });
    fireEvent.click(screen.getByTestId("admin-approvals-filter-overdue"));
    await waitFor(() => {
      const q = (overview.mock.calls.at(-1) as unknown as [{ query: Record<string, unknown> }])[0].query;
      expect(q).toMatchObject({ ticket_number: "20261012130", overdue: true, limit: 25, offset: 0 });
      expect(q.status).toBeUndefined();
    });
  });

  it("shows the steps of a row and withdraws an open request without a body", async () => {
    const cancel = vi.fn(() => demoDetail(501));
    mockApi({
      [OVERVIEW]: page,
      "GET /api/v1/approvals/501": () => demoDetail(501),
      "POST /api/v1/approvals/501/cancel": cancel,
    });
    renderApproval(<ApprovalsOverviewPage />);
    const first = (await screen.findAllByTestId("admin-approval-toggle"))[0];
    fireEvent.click(first);
    expect(await screen.findByTestId("admin-approval-detail")).toBeInTheDocument();
    expect(await screen.findAllByTestId("approval-approver")).not.toHaveLength(0);
    fireEvent.click(screen.getByTestId("admin-approval-cancel"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
    expect((cancel.mock.calls[0] as unknown[])[0]).toBeUndefined();
  });

  it("offers no withdraw button for a decided request", async () => {
    mockApi({ [OVERVIEW]: page, "GET /api/v1/approvals/502": () => demoDetail(502) });
    renderApproval(<ApprovalsOverviewPage />);
    const toggles = await screen.findAllByTestId("admin-approval-toggle");
    fireEvent.click(toggles[1]);
    await screen.findByTestId("admin-approval-detail");
    expect(screen.queryByTestId("admin-approval-cancel")).toBeNull();
  });

  it("shows subject type titles and filters by a registered type", async () => {
    const overview = vi.fn(page);
    mockApi({ [OVERVIEW]: overview });
    renderApproval(<ApprovalsOverviewPage />);
    const rows = await screen.findAllByTestId("admin-approval-row");
    // both subject types show their translated title (`itsm.change` -> "Change")
    expect(rows[1]).toHaveTextContent("· Ticket ·");
    expect(rows[0]).toHaveTextContent("· Change ·");
    fireEvent.click(screen.getByTestId("admin-approvals-filter-subject"));
    fireEvent.click(await screen.findByTestId("admin-approvals-filter-subject-menu-option-ticket"));
    await waitFor(() => {
      const q = (overview.mock.calls.at(-1) as unknown as [{ query: Record<string, unknown> }])[0].query;
      expect(q.subject_type).toBe("ticket");
    });
  });

  it("pages through the requests 25 at a time", async () => {
    const items = (resolveApprovalsData("/api/v1/admin/approvals", "GET") as { items: unknown[] }).items;
    const overview = vi.fn((init: { query?: { offset?: number } } | undefined) => ({
      items,
      total: 60,
      limit: 25,
      offset: init?.query?.offset ?? 0,
    }));
    mockApi({ [OVERVIEW]: overview as never });
    renderApproval(<ApprovalsOverviewPage />);
    await screen.findAllByTestId("admin-approval-row");
    expect(screen.getByTestId("admin-approvals-paging")).toHaveTextContent("Seite 1 von 3");
    expect(screen.getByTestId("admin-approvals-prev")).toBeDisabled();
    fireEvent.click(screen.getByTestId("admin-approvals-next"));
    await waitFor(() => expect(screen.getByTestId("admin-approvals-paging")).toHaveTextContent("Seite 2 von 3"));
    expect((overview.mock.calls.at(-1) as unknown as [{ query: { offset: number } }])[0].query.offset).toBe(25);
  });
});
