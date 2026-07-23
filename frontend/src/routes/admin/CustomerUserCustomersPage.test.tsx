import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerUserCustomersPage } from "./CustomerUserCustomersPage";

const listCustomerUsers = vi.fn();
const listCompanies = vi.fn();
const request = vi.fn();
const listCustomerCompanyUsers = vi.fn();
const assignCustomerCompany = vi.fn();
const revokeCustomerCompany = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminCustomerUsers: {
      list: (...args: unknown[]) => listCustomerUsers(...args),
    },
    adminCustomerCompanies: {
      list: (...args: unknown[]) => listCompanies(...args),
    },
    request: (...args: unknown[]) => request(...args),
    listCustomerCompanyUsers: (...args: unknown[]) => listCustomerCompanyUsers(...args),
    assignCustomerCompany: (...args: unknown[]) => assignCustomerCompany(...args),
    revokeCustomerCompany: (...args: unknown[]) => revokeCustomerCompany(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerUserCustomersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("CustomerUserCustomersPage", () => {
  beforeEach(() => {
    listCustomerUsers.mockReset();
    listCompanies.mockReset();
    request.mockReset();
    listCustomerCompanyUsers.mockReset();
    assignCustomerCompany.mockReset();
    revokeCustomerCompany.mockReset();

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
      page_size: 50,
    });
    listCompanies.mockResolvedValue({
      items: [
        { customer_id: "C1", name: "Acme", valid_id: 1 },
        { customer_id: "C2", name: "Globex", valid_id: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 50,
    });
    request.mockResolvedValue([{ customer_id: "C1", name: "Acme", valid_id: 1 }]);
    listCustomerCompanyUsers.mockResolvedValue([]);
    assignCustomerCompany.mockResolvedValue(undefined);
    revokeCustomerCompany.mockResolvedValue(undefined);
  });

  it("renders assigned companies checked and submits assign on toggle", async () => {
    renderPage();

    await screen.findByTestId("admin-customer-user-customers-page-anchor-alice");
    fireEvent.click(screen.getByTestId("admin-customer-user-customers-page-anchor-alice"));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "GET",
        "/api/v1/admin/customer-users/alice/companies",
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-customer-user-customers-page-counterpart-C1"),
      ).toBeChecked();
    });

    fireEvent.click(screen.getByTestId("admin-customer-user-customers-page-counterpart-C2"));

    await waitFor(() => {
      expect(assignCustomerCompany).toHaveBeenCalledWith("alice", "C2");
    });
  });

  it("hides invalid companies by default and reveals them via the Gültigkeit filter", async () => {
    listCompanies.mockResolvedValue({
      items: [
        { customer_id: "C1", name: "Acme", valid_id: 1 },
        { customer_id: "C2", name: "Globex", valid_id: 1 },
        { customer_id: "C9", name: "Defunct Inc", valid_id: 2 },
      ],
      total: 3,
      page: 1,
      page_size: 50,
    });

    renderPage();

    await screen.findByTestId("admin-customer-user-customers-page-anchor-alice");
    fireEvent.click(screen.getByTestId("admin-customer-user-customers-page-anchor-alice"));

    await screen.findByTestId("admin-customer-user-customers-page-counterpart-C1");
    expect(
      screen.queryByTestId("admin-customer-user-customers-page-counterpart-row-C9"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-customer-user-customers-page-valid-all"));
    await screen.findByTestId("admin-customer-user-customers-page-counterpart-row-C9");
  });
});
