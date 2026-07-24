import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RoleGroupsPage } from "./RoleGroupsPage";

const listRoles = vi.fn();
const listGroups = vi.fn();
const request = vi.fn();
const listGroupRoles = vi.fn();
const listRoleAssignmentCounts = vi.fn();
const listGroupAssignmentCounts = vi.fn();
const assignRoleGroup = vi.fn();
const revokeRoleGroup = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminRoles: {
      list: (...args: unknown[]) => listRoles(...args),
    },
    adminGroups: {
      list: (...args: unknown[]) => listGroups(...args),
    },
    request: (...args: unknown[]) => request(...args),
    listGroupRoles: (...args: unknown[]) => listGroupRoles(...args),
    listRoleAssignmentCounts: (...args: unknown[]) => listRoleAssignmentCounts(...args),
    listGroupAssignmentCounts: (...args: unknown[]) => listGroupAssignmentCounts(...args),
    assignRoleGroup: (...args: unknown[]) => assignRoleGroup(...args),
    revokeRoleGroup: (...args: unknown[]) => revokeRoleGroup(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RoleGroupsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("RoleGroupsPage", () => {
  beforeEach(() => {
    listRoles.mockReset();
    listGroups.mockReset();
    request.mockReset();
    listGroupRoles.mockReset();
    listRoleAssignmentCounts.mockReset();
    listGroupAssignmentCounts.mockReset();
    assignRoleGroup.mockReset();
    revokeRoleGroup.mockReset();

    listRoles.mockResolvedValue({
      items: [{ id: 1, name: "agent", valid_id: 1, comments: null }],
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
    listGroupRoles.mockResolvedValue([]);
    listRoleAssignmentCounts.mockResolvedValue({});
    listGroupAssignmentCounts.mockResolvedValue({});
    assignRoleGroup.mockResolvedValue(undefined);
    revokeRoleGroup.mockResolvedValue(undefined);
  });

  it("renders assigned groups checked and submits assign on toggle", async () => {
    renderPage();

    await screen.findByTestId("admin-role-groups-page-anchor-1");
    fireEvent.click(screen.getByTestId("admin-role-groups-page-anchor-1"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("GET", "/api/v1/admin/roles/1/groups", expect.anything());
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-role-groups-page-counterpart-5")).toBeChecked();
    });

    fireEvent.click(screen.getByTestId("admin-role-groups-page-counterpart-6"));

    await waitFor(() => {
      expect(assignRoleGroup).toHaveBeenCalledWith(1, {
        group_id: 6,
        permission_key: "rw",
        permission_value: 1,
      });
    });
  });

  it("hides invalid roles and groups by default and reveals them via the Gültigkeit filter", async () => {
    listRoles.mockResolvedValue({
      items: [
        { id: 1, name: "agent", valid_id: 1, comments: null },
        { id: 2, name: "legacy-role", valid_id: 2, comments: null },
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

    await screen.findByTestId("admin-role-groups-page-anchor-1");
    expect(screen.queryByTestId("admin-role-groups-page-anchor-2")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-role-groups-page-anchor-1"));
    await screen.findByTestId("admin-role-groups-page-counterpart-5");
    expect(
      screen.queryByTestId("admin-role-groups-page-counterpart-row-7"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-role-groups-page-valid-all"));
    await screen.findByTestId("admin-role-groups-page-anchor-2");
    await screen.findByTestId("admin-role-groups-page-counterpart-row-7");
  });
});
