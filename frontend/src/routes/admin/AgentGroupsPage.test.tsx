import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AgentGroupsPage } from "./AgentGroupsPage";

const listUsers = vi.fn();
const listGroups = vi.fn();
const request = vi.fn();
const listGroupUsers = vi.fn();
const listUserAssignmentCounts = vi.fn();
const listGroupAssignmentCounts = vi.fn();
const assignUserGroup = vi.fn();
const revokeUserGroup = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminUsers: {
      list: (...args: unknown[]) => listUsers(...args),
    },
    adminGroups: {
      list: (...args: unknown[]) => listGroups(...args),
    },
    request: (...args: unknown[]) => request(...args),
    listGroupUsers: (...args: unknown[]) => listGroupUsers(...args),
    listUserAssignmentCounts: (...args: unknown[]) => listUserAssignmentCounts(...args),
    listGroupAssignmentCounts: (...args: unknown[]) => listGroupAssignmentCounts(...args),
    assignUserGroup: (...args: unknown[]) => assignUserGroup(...args),
    revokeUserGroup: (...args: unknown[]) => revokeUserGroup(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AgentGroupsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AgentGroupsPage", () => {
  beforeEach(() => {
    listUsers.mockReset();
    listGroups.mockReset();
    request.mockReset();
    listGroupUsers.mockReset();
    listUserAssignmentCounts.mockReset();
    listGroupAssignmentCounts.mockReset();
    assignUserGroup.mockReset();
    revokeUserGroup.mockReset();

    listUsers.mockResolvedValue({
      items: [
        { id: 1, login: "agent1", first_name: "Ann", last_name: "A", valid_id: 1 },
      ],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listGroups.mockResolvedValue({
      items: [
        { id: 5, name: "users", valid_id: 1, comments: null },
        { id: 6, name: "stats", valid_id: 1, comments: null },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    request.mockResolvedValue([{ id: 5, name: "users", valid_id: 1, comments: null }]);
    listGroupUsers.mockResolvedValue([]);
    listUserAssignmentCounts.mockResolvedValue({});
    listGroupAssignmentCounts.mockResolvedValue({});
    assignUserGroup.mockResolvedValue(undefined);
    revokeUserGroup.mockResolvedValue(undefined);
  });

  it("renders assigned groups checked and submits assign on toggle", async () => {
    renderPage();

    await screen.findByTestId("admin-agent-groups-page-anchor-1");
    fireEvent.click(screen.getByTestId("admin-agent-groups-page-anchor-1"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("GET", "/api/v1/admin/users/1/groups", expect.anything());
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-agent-groups-page-counterpart-5")).toBeChecked();
    });

    fireEvent.click(screen.getByTestId("admin-agent-groups-page-counterpart-6"));

    await waitFor(() => {
      expect(assignUserGroup).toHaveBeenCalledWith(1, { group_id: 6, permission_key: "rw" });
    });
  });

  it("hides invalid agents and groups by default and reveals them via the Gültigkeit filter", async () => {
    listUsers.mockResolvedValue({
      items: [
        { id: 1, login: "agent1", first_name: "Ann", last_name: "A", valid_id: 1 },
        { id: 2, login: "agent2", first_name: "Bob", last_name: "B", valid_id: 2 },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    listGroups.mockResolvedValue({
      items: [
        { id: 5, name: "users", valid_id: 1, comments: null },
        { id: 7, name: "legacy", valid_id: 2, comments: null },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });

    renderPage();

    await screen.findByTestId("admin-agent-groups-page-anchor-1");
    expect(screen.queryByTestId("admin-agent-groups-page-anchor-2")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-agent-groups-page-anchor-1"));
    await screen.findByTestId("admin-agent-groups-page-counterpart-5");
    expect(
      screen.queryByTestId("admin-agent-groups-page-counterpart-row-7"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-agent-groups-page-valid-all"));
    await screen.findByTestId("admin-agent-groups-page-anchor-2");
    await screen.findByTestId("admin-agent-groups-page-counterpart-row-7");
  });
});
