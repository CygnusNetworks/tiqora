import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { CustomerDirectorySearch } from "./CustomerDirectoryPage";
import { CustomerDirectoryPage } from "./CustomerDirectoryPage";

const { state, navigate, listCustomerDirectory, exportCustomerVcards, downloadText } = vi.hoisted(
  () => ({
    state: { canUse: true, search: {} as Record<string, unknown> },
    navigate: vi.fn(),
    listCustomerDirectory: vi.fn(),
    exportCustomerVcards: vi.fn(),
    downloadText: vi.fn(),
  }),
);

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => state.search,
  Link: ({ children, ...rest }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a data-to={String(rest.to)} data-testid={rest["data-testid"] as string | undefined}>
      {children}
    </a>
  ),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 7, login: "agent", can_use_customer_directory: state.canUse } }),
}));

vi.mock("@/lib/cryptoFiles", () => ({ downloadText }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const real = actual.api;
  return {
    ...actual,
    api: {
      listCustomerDirectory,
      exportCustomerVcards,
      searchCustomerDirectoryCompanies: vi.fn().mockResolvedValue([]),
      customerVcardUrl: real.customerVcardUrl.bind(real),
      companyVcardsUrl: real.companyVcardsUrl.bind(real),
      customerDirectoryVcardsUrl: real.customerDirectoryVcardsUrl.bind(real),
    },
  };
});

function entry(login: string, first: string, last: string, company: string | null = "ACME") {
  return {
    login,
    email: `${login}@example.com`,
    customer_id: company ? "ACME" : login,
    company_name: company,
    title: null,
    first_name: first,
    last_name: last,
    phone: "+49 228 1",
    mobile: null,
    city: "Bonn",
    valid_id: 1,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerDirectoryPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  void i18n.changeLanguage("de");
  state.canUse = true;
  state.search = {};
  navigate.mockReset();
  exportCustomerVcards.mockReset().mockResolvedValue("BEGIN:VCARD\r\nEND:VCARD\r\n");
  downloadText.mockReset();
  listCustomerDirectory.mockReset().mockResolvedValue({
    items: [entry("laura", "Laura", "Gomez"), entry("kevin", "Kevin", "Wu", null)],
    total: 2,
    page: 1,
    page_size: 50,
  });
});

describe("CustomerDirectoryPage", () => {
  it("explains the missing permission and loads nothing", () => {
    state.canUse = false;
    renderPage();
    expect(screen.getByTestId("customer-directory-denied")).toBeInTheDocument();
    expect(listCustomerDirectory).not.toHaveBeenCalled();
  });

  it("lists contacts name first, linked to the customer centre, with vCard links", async () => {
    renderPage();
    const open = await screen.findByTestId("customer-directory-open-laura");
    expect(open).toHaveAttribute("data-to", "/agent/customers/$login");
    expect(open).toHaveTextContent("Laura Gomez");
    expect(screen.getByText("Ohne Firma")).toBeInTheDocument();
    expect(screen.getByTestId("customer-directory-vcard-laura")).toHaveAttribute(
      "href",
      "/api/v1/customers/laura/vcard",
    );
    expect(screen.getByTestId("customer-directory-export-list")).toHaveAttribute(
      "href",
      "/api/v1/customer-directory/vcards?valid=valid",
    );
    expect(listCustomerDirectory).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, pageSize: 50, valid: "valid" }),
      expect.anything(),
    );
  });

  it("downloads the selected contacts as one file", async () => {
    renderPage();
    await screen.findByTestId("customer-directory-open-laura");
    fireEvent.click(screen.getByTestId("admin-row-select-laura"));
    fireEvent.click(screen.getByTestId("admin-row-select-kevin"));
    const bar = screen.getByTestId("customer-directory-selection-bar");
    fireEvent.click(within(bar).getByTestId("customer-directory-export-selection"));
    await waitFor(() => expect(exportCustomerVcards).toHaveBeenCalledWith(["laura", "kevin"]));
    await waitFor(() =>
      expect(downloadText).toHaveBeenCalledWith(
        "kontakte.vcf",
        expect.stringContaining("BEGIN:VCARD"),
        expect.stringContaining("text/vcard"),
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("customer-directory-selection-bar")).toBeNull());
  });

  it("shows the company row with its export when filtered by company", async () => {
    state.search = { company: "ACME", company_name: "ACME GmbH" } satisfies CustomerDirectorySearch;
    renderPage();
    const banner = await screen.findByTestId("customer-directory-company-banner");
    expect(banner).toHaveTextContent("ACME GmbH");
    expect(within(banner).getByTestId("customer-directory-export-company")).toHaveAttribute(
      "href",
      "/api/v1/customers/companies/ACME/vcards",
    );
    expect(listCustomerDirectory).toHaveBeenCalledWith(
      expect.objectContaining({ customerId: "ACME" }),
      expect.anything(),
    );
    fireEvent.click(screen.getByTestId("customer-directory-company-clear"));
    expect(navigate).toHaveBeenCalled();
  });

  it("disables the list export above the cap", async () => {
    listCustomerDirectory.mockResolvedValue({
      items: [entry("laura", "Laura", "Gomez")],
      total: 5000,
      page: 1,
      page_size: 50,
    });
    renderPage();
    expect(await screen.findByTestId("customer-directory-export-list-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("customer-directory-export-list")).toBeNull();
  });
});
