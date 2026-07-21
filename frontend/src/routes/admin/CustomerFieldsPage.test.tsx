import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CustomerFieldsPage } from "./CustomerFieldsPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();
const listAvailableCustomerColumns = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminCustomerFields: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
    listAvailableCustomerColumns: (...args: unknown[]) => listAvailableCustomerColumns(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerFieldsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleRow = {
  id: 1,
  source_table: "customer_user",
  column_name: "wpnum",
  tag_name: "wpnum",
  label: "Kundennummer",
  enabled: true,
  created: "2026-07-20T10:00:00Z",
  changed: "2026-07-20T10:00:00Z",
};

describe("CustomerFieldsPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();
    listAvailableCustomerColumns.mockReset();

    list.mockResolvedValue({
      items: [sampleRow],
      total: 1,
      page: 1,
      page_size: 25,
    });
    listAvailableCustomerColumns.mockResolvedValue(["wpnum", "user_email", "user_firstname"]);
    create.mockResolvedValue({ ...sampleRow, id: 2, tag_name: "CustomTag" });
  });

  it("renders customer fields from the list API", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-fields-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-customer-fields-table")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Kundennummer")).toBeInTheDocument();
    });
    // column_name and tag_name are both "wpnum"
    expect(screen.getAllByText("wpnum").length).toBeGreaterThanOrEqual(1);
    expect(list).toHaveBeenCalled();
  });

  it("opens create form and calls create with mapped body", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("admin-new-button"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form")).toBeInTheDocument();
    });

    // Wait for available-columns query (custom field mounts).
    await waitFor(() => {
      expect(listAvailableCustomerColumns).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByTestId("admin-form-column_name"), {
      target: { value: "user_email" },
    });
    fireEvent.change(screen.getByTestId("admin-form-tag_name"), {
      target: { value: "UserEmail" },
    });
    fireEvent.change(screen.getByTestId("admin-form-label"), {
      target: { value: "E-Mail" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1);
    });
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        source_table: "customer_user",
        column_name: "user_email",
        tag_name: "UserEmail",
        label: "E-Mail",
        enabled: true,
      }),
    );
  });
});
