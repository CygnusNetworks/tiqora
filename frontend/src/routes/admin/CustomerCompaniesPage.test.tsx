import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerCompaniesPage } from "./CustomerCompaniesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminCustomerCompanies: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerCompaniesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleCompany = {
  customer_id: "ACME",
  name: "Acme Corp",
  street: "1 Main St",
  city: "Springfield",
  country: "US",
  url: "https://acme.example",
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("CustomerCompaniesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleCompany],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleCompany, customer_id: "GLOBEX", name: "Globex" });
    update.mockResolvedValue(sampleCompany);
    deactivate.mockResolvedValue(undefined);
  });

  it("renders the list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ACME")).toBeInTheDocument();
    });
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("Springfield")).toBeInTheDocument();
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ACME")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-ACME"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-ACME"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-customer_id")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-customer_id")).toHaveValue("ACME");
    expect(screen.getByTestId("admin-form-name")).toHaveValue("Acme Corp");
    expect(screen.getByTestId("admin-form-city")).toHaveValue("Springfield");
  });

  it("submits a create with the entered field values", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ACME")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-customer_id")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-customer_id"), {
      target: { value: "GLOBEX" },
    });
    fireEvent.change(screen.getByTestId("admin-form-name"), { target: { value: "Globex" } });
    fireEvent.change(screen.getByTestId("admin-form-city"), { target: { value: "Metropolis" } });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        customer_id: "GLOBEX",
        name: "Globex",
        street: null,
        city: "Metropolis",
        country: null,
        url: null,
        valid_id: 1,
      });
    });
  });

  it("deactivates a row via the row menu", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ACME")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-ACME"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-ACME"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith("ACME");
    });
  });
});
