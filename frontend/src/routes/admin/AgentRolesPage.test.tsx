import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AgentRolesPage } from "./AgentRolesPage";

const listUsers = vi.fn();
const listRoles = vi.fn();
const request = vi.fn();
const listRoleUsers = vi.fn();
const listUserAssignmentCounts = vi.fn();
const listRoleAssignmentCounts = vi.fn();
const assignUserRole = vi.fn();
const revokeUserRole = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminUsers: {
      list: (...args: unknown[]) => listUsers(...args),
    },
    adminRoles: {
      list: (...args: unknown[]) => listRoles(...args),
    },
    request: (...args: unknown[]) => request(...args),
    listRoleUsers: (...args: unknown[]) => listRoleUsers(...args),
    listUserAssignmentCounts: (...args: unknown[]) => listUserAssignmentCounts(...args),
    listRoleAssignmentCounts: (...args: unknown[]) => listRoleAssignmentCounts(...args),
    assignUserRole: (...args: unknown[]) => assignUserRole(...args),
    revokeUserRole: (...args: unknown[]) => revokeUserRole(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AgentRolesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AgentRolesPage", () => {
  beforeEach(() => {
    listUsers.mockReset();
    listRoles.mockReset();
    request.mockReset();
    listRoleUsers.mockReset();
    listUserAssignmentCounts.mockReset();
    listRoleAssignmentCounts.mockReset();
    assignUserRole.mockReset();
    revokeUserRole.mockReset();

    listUsers.mockResolvedValue({
      items: [
        { id: 1, login: "agent1", first_name: "Ann", last_name: "A", valid_id: 1 },
      ],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listRoles.mockResolvedValue({
      items: [
        { id: 5, name: "agent", valid_id: 1, comments: null },
        { id: 6, name: "supervisor", valid_id: 1, comments: null },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    request.mockResolvedValue([{ id: 5, name: "agent", valid_id: 1, comments: null }]);
    listRoleUsers.mockResolvedValue([]);
    listUserAssignmentCounts.mockResolvedValue({});
    listRoleAssignmentCounts.mockResolvedValue({});
    assignUserRole.mockResolvedValue(undefined);
    revokeUserRole.mockResolvedValue(undefined);
  });

  it("renders assigned roles checked and submits assign on toggle", async () => {
    renderPage();

    await screen.findByTestId("admin-agent-roles-page-anchor-1");
    fireEvent.click(screen.getByTestId("admin-agent-roles-page-anchor-1"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("GET", "/api/v1/admin/users/1/roles", expect.anything());
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-agent-roles-page-counterpart-5")).toBeChecked();
    });

    fireEvent.click(screen.getByTestId("admin-agent-roles-page-counterpart-6"));

    await waitFor(() => {
      expect(assignUserRole).toHaveBeenCalledWith(1, { role_id: 6 });
    });
  });

  it("hides invalid agents and roles by default and reveals them via the Gültigkeit filter", async () => {
    listUsers.mockResolvedValue({
      items: [
        { id: 1, login: "agent1", first_name: "Ann", last_name: "A", valid_id: 1 },
        { id: 2, login: "agent2", first_name: "Bob", last_name: "B", valid_id: 2 },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    listRoles.mockResolvedValue({
      items: [
        { id: 5, name: "agent", valid_id: 1, comments: null },
        { id: 7, name: "legacy", valid_id: 2, comments: null },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });

    renderPage();

    await screen.findByTestId("admin-agent-roles-page-anchor-1");
    expect(screen.queryByTestId("admin-agent-roles-page-anchor-2")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-agent-roles-page-anchor-1"));
    await screen.findByTestId("admin-agent-roles-page-counterpart-5");
    expect(
      screen.queryByTestId("admin-agent-roles-page-counterpart-row-7"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-agent-roles-page-valid-all"));
    await screen.findByTestId("admin-agent-roles-page-anchor-2");
    await screen.findByTestId("admin-agent-roles-page-counterpart-row-7");
  });
});
