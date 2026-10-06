import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { CustomerDirectoryAccessDialog } from "./CustomerDirectoryAccessDialog";

const { getFeatureGrants, setFeatureGrants, groups, roles, users } = vi.hoisted(() => ({
  getFeatureGrants: vi.fn(),
  setFeatureGrants: vi.fn(),
  groups: vi.fn(),
  roles: vi.fn(),
  users: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      getFeatureGrants,
      setFeatureGrants,
      adminGroups: { list: groups },
      adminRoles: { list: roles },
      adminUsers: { list: users },
    },
  };
});

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 500 });

beforeEach(() => {
  void i18n.changeLanguage("de");
  getFeatureGrants.mockReset().mockResolvedValue({ user_ids: [5], group_ids: [], role_ids: [3] });
  setFeatureGrants.mockReset().mockImplementation((_f, body) => Promise.resolve(body));
  groups.mockReset().mockResolvedValue(page([{ id: 1, name: "users" }, { id: 2, name: "support" }]));
  roles.mockReset().mockResolvedValue(page([{ id: 3, name: "Hotline" }]));
  users.mockReset().mockResolvedValue(
    page([{ id: 5, login: "anna", first_name: "Anna", last_name: "Berger" }]),
  );
});

describe("CustomerDirectoryAccessDialog", () => {
  it("shows the current grants and saves agents, groups and roles", async () => {
    const onClose = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <CustomerDirectoryAccessDialog onClose={onClose} />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("directory-access-user-5")).toBeChecked();
    expect(screen.getByTestId("directory-access-role-3")).toBeChecked();
    expect(screen.getByTestId("directory-access-group-2")).not.toBeChecked();

    fireEvent.click(screen.getByTestId("directory-access-group-2"));
    fireEvent.click(screen.getByTestId("directory-access-role-3"));
    fireEvent.click(screen.getByTestId("directory-access-save"));

    await waitFor(() =>
      expect(setFeatureGrants).toHaveBeenCalledWith("customer_directory", {
        user_ids: [5],
        group_ids: [2],
        role_ids: [],
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
