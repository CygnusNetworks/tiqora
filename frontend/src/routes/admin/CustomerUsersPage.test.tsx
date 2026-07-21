import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerUsersPage } from "./CustomerUsersPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();
const bulkUpdateCustomerUsers = vi.fn();
const companyList = vi.fn();
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminCustomerUsers: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
    adminCustomerCompanies: {
      list: (...args: unknown[]) => companyList(...args),
    },
    bulkUpdateCustomerUsers: (...args: unknown[]) => bulkUpdateCustomerUsers(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerUsersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleRow = {
  id: 42,
  login: "alice@example.com",
  email: "alice@example.com",
  customer_id: "ACME",
  title: null,
  first_name: "Alice",
  last_name: "Wonder",
  phone: null,
  fax: null,
  mobile: null,
  street: null,
  zip: null,
  city: null,
  country: null,
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("CustomerUsersPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();
    bulkUpdateCustomerUsers.mockReset();
    companyList.mockReset();
    navigate.mockReset();

    list.mockResolvedValue({
      items: [sampleRow, { ...sampleRow, id: 43, login: "bob@example.com" }],
      total: 2,
      page: 1,
      page_size: 100,
    });
    bulkUpdateCustomerUsers.mockResolvedValue({ updated: 2 });
    companyList.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
  });

  it("enables search and bulk validity actions", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-users-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-customer-users-search")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    fireEvent.click(screen.getByTestId("admin-row-select-43"));

    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

    await waitFor(() => {
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledWith({
        ids: expect.arrayContaining([42, 43]),
        valid_id: 2,
      });
    });
  });

  it("bulk valid action calls api with valid_id 1", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-valid"));

    await waitFor(() => {
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledWith({
        ids: [42],
        valid_id: 1,
      });
    });
  });

  it("offers an Alle page-size option that requests a large page", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-users-page-size")).toBeInTheDocument();
    });
    const select = screen.getByTestId("admin-customer-users-page-size");
    expect(select.querySelector('[data-testid="admin-customer-users-page-size-all"]')).not.toBeNull();
    // Option label is i18n "All" / "Alle"
    const allOption = Array.from(select.querySelectorAll("option")).find(
      (o) => o.getAttribute("value") === "100000",
    );
    expect(allOption).toBeTruthy();

    list.mockClear();
    fireEvent.change(select, { target: { value: "100000" } });
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ pageSize: 100_000, page: 1 }),
        expect.anything(),
      );
    });
  });

  it("bulk GDPR action navigates to /admin/gdpr with selected logins", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    fireEvent.click(screen.getByTestId("admin-row-select-43"));
    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-gdpr"));

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({
        to: "/admin/gdpr",
        search: {
          logins: expect.stringMatching(/alice@example\.com/),
        },
      });
    });
    const call = navigate.mock.calls[0][0] as { search: { logins: string } };
    const logins = call.search.logins.split(",");
    expect(logins).toEqual(expect.arrayContaining(["alice@example.com", "bob@example.com"]));
    expect(logins).toHaveLength(2);
  });

  it("sorts by login header and toggles the indicator", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-sort-login")).toBeInTheDocument();
    });

    list.mockClear();
    const header = screen.getByTestId("admin-sort-login");
    fireEvent.click(header);

    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "login", order: "asc" }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▲/);

    list.mockClear();
    fireEvent.click(header);
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "login", order: "desc" }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▼/);

    // Name column maps to first_name sort key.
    list.mockClear();
    fireEvent.click(screen.getByTestId("admin-sort-first_name"));
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "first_name", order: "asc" }),
        expect.anything(),
      );
    });
  });
});
