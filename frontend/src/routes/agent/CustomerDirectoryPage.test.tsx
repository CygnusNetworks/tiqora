import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { CustomerDirectorySearch } from "./CustomerDirectoryPage";
import { CustomerDirectoryPage } from "./CustomerDirectoryPage";

const { state, navigate, listCustomerDirectory, getCustomerShortlist, searchCompanies } =
  vi.hoisted(() => ({
    state: {
      canUse: true,
      canEdit: false,
      search: {} as CustomerDirectorySearch,
    },
    navigate: vi.fn(),
    listCustomerDirectory: vi.fn(),
    getCustomerShortlist: vi.fn(),
    searchCompanies: vi.fn(),
  }));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => state.search,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 7,
      login: "agent",
      can_use_customer_directory: state.canUse,
      can_edit_customers: state.canEdit,
    },
  }),
}));

// The right-hand panels and the drawer have their own tests.
vi.mock("@/components/customers/CustomerPanel", () => ({
  CustomerPanel: ({ login }: { login: string }) => <div data-testid="stub-customer">{login}</div>,
}));
vi.mock("@/components/customers/CompanyPanel", () => ({
  CompanyPanel: ({ customerId }: { customerId: string }) => (
    <div data-testid="stub-company">{customerId}</div>
  ),
}));
vi.mock("@/components/customers/CustomerEditDrawer", () => ({
  CustomerEditDrawer: ({ state: s }: { state: unknown }) => (
    <div data-testid="stub-drawer">{JSON.stringify(s)}</div>
  ),
}));
vi.mock("@/components/customers/CustomerCallStrip", () => ({
  CustomerCallStrip: () => null,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const real = actual.api;
  return {
    ...actual,
    api: {
      listCustomerDirectory,
      getCustomerShortlist,
      searchCustomerDirectoryCompanies: searchCompanies,
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

function short(login: string, first: string, last: string, extra: Record<string, unknown> = {}) {
  return {
    login,
    email: `${login}@example.com`,
    customer_id: "ACME",
    company_name: "ACME",
    first_name: first,
    last_name: last,
    phone: null,
    mobile: null,
    last_at: new Date().toISOString(),
    last_channel: "Phone",
    ticket_count: 4,
    ...extra,
  };
}

/** The search object the page last navigated to (navigate gets an updater). */
function lastSearch(): CustomerDirectorySearch {
  const call = navigate.mock.calls.at(-1)?.[0] as {
    search: (prev: CustomerDirectorySearch) => CustomerDirectorySearch;
  };
  return call.search(state.search);
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
  state.canEdit = false;
  state.search = {};
  navigate.mockReset();
  getCustomerShortlist.mockReset().mockResolvedValue({
    favorites: [],
    recent: [short("hanna", "Hanna", "Voss")],
    frequent: [short("lena", "Lena", "Brandt", { company_name: "Kanzlei", customer_id: "KBS" })],
  });
  listCustomerDirectory.mockReset().mockResolvedValue({
    items: [entry("laura", "Laura", "Gomez"), entry("kevin", "Kevin", "Wu", null)],
    total: 2,
    page: 1,
    page_size: 30,
  });
  searchCompanies.mockReset().mockResolvedValue([{ customer_id: "NW", name: "Northwind" }]);
});

describe("CustomerDirectoryPage", () => {
  it("explains the missing permission and loads nothing", () => {
    state.canUse = false;
    renderPage();
    expect(screen.getByTestId("customer-directory-denied")).toBeInTheDocument();
    expect(getCustomerShortlist).not.toHaveBeenCalled();
    expect(listCustomerDirectory).not.toHaveBeenCalled();
  });

  it("shows the agent's recent and frequent customers instead of a list", async () => {
    renderPage();
    const recent = await screen.findByTestId("customer-recent-hanna");
    expect(recent).toHaveTextContent("Hanna Voss");
    expect(recent).toHaveTextContent("Telefon");
    expect(screen.getByTestId("customer-frequent-lena")).toHaveTextContent("4 Tickets");
    // No search term, no directory listing.
    expect(listCustomerDirectory).not.toHaveBeenCalled();
    expect(screen.getByText("Wähle links einen Kunden oder suche nach Name, E-Mail oder Telefonnummer.")).toBeInTheDocument();

    fireEvent.click(recent);
    expect(lastSearch()).toEqual({ tab: undefined, sel: "hanna" });
  });

  it("lists favorites above recent and frequent, and Enter opens the first", async () => {
    getCustomerShortlist.mockResolvedValue({
      favorites: [
        { ...short("mia", "Mia", "Klein", { company_name: "Stadtwerke", customer_id: "SW" }) },
      ],
      recent: [short("hanna", "Hanna", "Voss")],
      frequent: [],
    });
    renderPage();
    const fav = await screen.findByTestId("customer-favorite-mia");
    expect(fav).toHaveTextContent("Mia Klein");
    expect(fav).toHaveTextContent("Stadtwerke");
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Favoriten", "Zuletzt"]);
    fireEvent.keyDown(screen.getByTestId("customer-directory-search"), { key: "Enter" });
    expect(lastSearch()).toEqual({ tab: undefined, sel: "mia" });
  });

  it("searches people from the URL term and offers the hits as vCard", async () => {
    state.search = { q: "acme" };
    renderPage();
    const row = await screen.findByTestId("customer-row-laura");
    expect(row).toHaveTextContent("Laura Gomez");
    expect(listCustomerDirectory).toHaveBeenCalledWith(
      { search: "acme", valid: "valid", page: 1, pageSize: 30 },
      expect.anything(),
    );
    expect(screen.getByTestId("customer-directory-export-list")).toHaveAttribute(
      "href",
      "/api/v1/customer-directory/vcards?search=acme&valid=valid",
    );
    fireEvent.click(row);
    expect(lastSearch()).toMatchObject({ q: "acme", sel: "laura" });
  });

  it("Enter in the search field opens the first hit", async () => {
    state.search = { q: "acme" };
    renderPage();
    await screen.findByTestId("customer-row-laura");
    fireEvent.keyDown(screen.getByTestId("customer-directory-search"), { key: "Enter" });
    expect(lastSearch()).toMatchObject({ sel: "laura" });
  });

  it("offers to create the searched customer only to agents who may edit", async () => {
    listCustomerDirectory.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 30 });
    state.search = { q: "Ilka Brenner" };
    renderPage();
    await screen.findByText("Niemand gefunden.");
    expect(screen.queryByTestId("customer-create-from-search")).not.toBeInTheDocument();
    expect(screen.queryByTestId("customer-new")).not.toBeInTheDocument();
  });

  it("prefills the create form from the search term", async () => {
    state.canEdit = true;
    listCustomerDirectory.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 30 });
    state.search = { q: "Ilka Brenner" };
    renderPage();
    fireEvent.click(await screen.findByTestId("customer-create-from-search"));
    expect(JSON.parse(screen.getByTestId("stub-drawer").textContent ?? "")).toEqual({
      mode: "create",
      prefill: { first_name: "Ilka", last_name: "Brenner" },
    });
  });

  it("shows the selected customer on the right", async () => {
    state.search = { sel: "hanna" };
    renderPage();
    expect(screen.getByTestId("stub-customer")).toHaveTextContent("hanna");
    await screen.findByTestId("customer-recent-hanna");
    expect(screen.getByTestId("customer-recent-hanna")).toHaveAttribute("aria-current", "true");
  });

  it("searches companies in their own tab and opens one", async () => {
    state.search = { tab: "companies", q: "north" };
    renderPage();
    const row = await screen.findByTestId("company-row-NW");
    expect(searchCompanies).toHaveBeenCalledWith("north", expect.anything());
    expect(listCustomerDirectory).not.toHaveBeenCalled();
    fireEvent.click(row);
    expect(lastSearch()).toMatchObject({ tab: "companies", company: "NW" });
  });

  it("lists the companies of the agent's own customers without a term", async () => {
    state.search = { tab: "companies", company: "KBS" };
    renderPage();
    expect(await screen.findByTestId("company-row-KBS")).toHaveTextContent("Kanzlei");
    expect(screen.getByTestId("company-row-ACME")).toBeInTheDocument();
    expect(screen.getByTestId("stub-company")).toHaveTextContent("KBS");
  });

  it("writes the typed term to the URL", async () => {
    renderPage();
    fireEvent.change(screen.getByTestId("customer-directory-search"), {
      target: { value: "voss" },
    });
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(lastSearch()).toMatchObject({ q: "voss" });
  });
});
