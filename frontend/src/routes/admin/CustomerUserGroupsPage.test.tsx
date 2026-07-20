import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerUserGroupsPage } from "./CustomerUserGroupsPage";

const listCustomerUsers = vi.fn();
const listGroups = vi.fn();
const listCustomerUserGroups = vi.fn();
const assignCustomerUserGroup = vi.fn();
const revokeCustomerUserGroup = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminCustomerUsers: {
      list: (...args: unknown[]) => listCustomerUsers(...args),
    },
    adminGroups: {
      list: (...args: unknown[]) => listGroups(...args),
    },
    listCustomerUserGroups: (...args: unknown[]) => listCustomerUserGroups(...args),
    assignCustomerUserGroup: (...args: unknown[]) => assignCustomerUserGroup(...args),
    revokeCustomerUserGroup: (...args: unknown[]) => revokeCustomerUserGroup(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerUserGroupsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("CustomerUserGroupsPage", () => {
  beforeEach(() => {
    listCustomerUsers.mockReset();
    listGroups.mockReset();
    listCustomerUserGroups.mockReset();
    assignCustomerUserGroup.mockReset();
    revokeCustomerUserGroup.mockReset();

    listCustomerUsers.mockResolvedValue({
      items: [
        {
          id: 1,
          login: "alice",
          first_name: "Alice",
          last_name: "A",
          email: "a@ex.com",
          customer_id: "C1",
          valid_id: 1,
        },
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
    listCustomerUserGroups.mockResolvedValue([
      { id: 5, name: "users", valid_id: 1, comments: null },
    ]);
    assignCustomerUserGroup.mockResolvedValue(undefined);
    revokeCustomerUserGroup.mockResolvedValue(undefined);
  });

  it("renders assigned groups and submits PUT assign on toggle", async () => {
    renderPage();

    await screen.findByRole("option", { name: /alice/i });
    fireEvent.change(screen.getByTestId("admin-customer-user-groups-select"), {
      target: { value: "alice" },
    });

    await waitFor(() => {
      expect(listCustomerUserGroups).toHaveBeenCalledWith("alice");
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-user-group-toggle-5")).toBeChecked();
    });

    fireEvent.click(screen.getByTestId("admin-customer-user-group-toggle-6"));

    await waitFor(() => {
      expect(assignCustomerUserGroup).toHaveBeenCalledWith("alice", {
        group_id: 6,
        permission_key: "rw",
        permission_value: 1,
      });
    });
  });
});
