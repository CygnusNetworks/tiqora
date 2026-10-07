import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { CustomerEditDrawer, type CustomerDrawerState } from "./CustomerEditDrawer";

const { navigate, createCustomer, updateCustomer, callerLookup } = vi.hoisted(() => ({
  navigate: vi.fn(),
  createCustomer: vi.fn(),
  updateCustomer: vi.fn(),
  callerLookup: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      createCustomer,
      updateCustomer,
      searchCustomerDirectoryCompanies: vi.fn().mockResolvedValue([]),
    },
  };
});

vi.mock("@/lib/phoneApi", () => ({ phoneApi: { callerLookup } }));

const onSaved = vi.fn();
const onClose = vi.fn();

function renderDrawer(state: CustomerDrawerState) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerEditDrawer state={state} onClose={onClose} onSaved={onSaved} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const type = (testId: string, value: string) =>
  fireEvent.change(screen.getByTestId(testId), { target: { value } });

beforeEach(() => {
  void i18n.changeLanguage("de");
  navigate.mockReset();
  onSaved.mockReset();
  onClose.mockReset();
  createCustomer.mockReset().mockImplementation((body: { login: string }) =>
    Promise.resolve({ ...body }),
  );
  updateCustomer.mockReset().mockImplementation((login: string) => Promise.resolve({ login }));
  callerLookup.mockReset().mockResolvedValue({ customers: [], companies: [], open_tickets: [] });
});

describe("CustomerEditDrawer", () => {
  it("creates at the preset company with the e-mail as login, then opens an e-mail ticket", async () => {
    renderDrawer({ mode: "create", company: { customer_id: "SWL", name: "Stadtwerke" } });
    expect(screen.getByText("bei Stadtwerke")).toBeInTheDocument();
    type("customer-drawer-last-name", "Brenner");
    type("customer-drawer-email", "i.brenner@example.com");
    expect(screen.getByTestId("customer-drawer-login")).toHaveValue("i.brenner@example.com");
    fireEvent.click(screen.getByTestId("customer-drawer-save-email"));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("i.brenner@example.com"));
    expect(createCustomer).toHaveBeenCalledWith(
      expect.objectContaining({
        login: "i.brenner@example.com",
        email: "i.brenner@example.com",
        last_name: "Brenner",
        customer_id: "SWL",
        phone: null,
      }),
    );
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { type: "email", customer: "i.brenner@example.com" },
    });
  });

  it("uses the login as customer number without a company", async () => {
    renderDrawer({ mode: "create", prefill: { phone: "0228 555" } });
    type("customer-drawer-last-name", "Richter");
    type("customer-drawer-email", "paul@example.com");
    fireEvent.click(screen.getByTestId("customer-drawer-save"));
    await waitFor(() => expect(createCustomer).toHaveBeenCalled());
    expect(createCustomer.mock.calls[0][0]).toMatchObject({
      customer_id: "paul@example.com",
      phone: "0228 555",
    });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("saves edits with PUT and keeps the login fixed", async () => {
    renderDrawer({
      mode: "edit",
      customer: {
        login: "hvoss",
        email: "h.voss@example.com",
        customer_id: "SWL",
        company_name: "Stadtwerke",
        first_name: "Hanna",
        last_name: "Voss",
        phone: "0228 1",
        mobile: null,
      },
    });
    expect(screen.getByTestId("customer-drawer-login")).toBeDisabled();
    type("customer-drawer-phone", "");
    fireEvent.click(screen.getByTestId("customer-drawer-save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("hvoss"));
    expect(updateCustomer).toHaveBeenCalledWith(
      "hvoss",
      expect.objectContaining({ customer_id: "SWL", last_name: "Voss", phone: null }),
    );
  });

  it("names the owner of an e-mail address that is taken", async () => {
    createCustomer.mockRejectedValue(
      new ApiError(
        409,
        { detail: { message: "Customer user e-mail already exists", login: "jebert" } },
        "/api/v1/customers",
      ),
    );
    renderDrawer({ mode: "create" });
    type("customer-drawer-last-name", "Ebert");
    type("customer-drawer-email", "j.ebert@example.com");
    fireEvent.click(screen.getByTestId("customer-drawer-save"));
    expect(await screen.findByTestId("customer-drawer-error")).toHaveTextContent("jebert");
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("warns when the phone number already belongs to someone else", async () => {
    callerLookup.mockResolvedValue({
      customers: [{ login: "jebert", name: "Jonas Ebert", company: "Stadtwerke" }],
      companies: [],
      open_tickets: [],
    });
    renderDrawer({ mode: "create" });
    type("customer-drawer-phone", "0228 555 0150");
    expect(await screen.findByTestId("customer-drawer-phone-taken")).toHaveTextContent(
      "Jonas Ebert",
    );
  });
});
