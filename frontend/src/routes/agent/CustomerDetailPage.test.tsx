import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { CustomerDetailPage } from "./CustomerDetailPage";

const { navigate, getCustomer, listTickets, phoneConfig, dial, customerCryptoKeys, auth } = vi.hoisted(() => ({
  auth: { canUseDirectory: true },
  navigate: vi.fn(),
  getCustomer: vi.fn(),
  listTickets: vi.fn(),
  phoneConfig: vi.fn(),
  dial: vi.fn(),
  customerCryptoKeys: {
    list: vi.fn(),
    uploadPgp: vi.fn(),
    uploadSmime: vi.fn(),
    deletePgp: vi.fn(),
    deleteSmime: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ login: "jane.doe" }),
  Link: ({ children, ...rest }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a data-to={String(rest.to)} data-testid={rest["data-testid"] as string | undefined}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const real = actual.api;
  return {
    ...actual,
    api: {
      getCustomer,
      listTickets,
      customerCryptoKeys,
      customerVcardUrl: real.customerVcardUrl.bind(real),
      companyVcardsUrl: real.companyVcardsUrl.bind(real),
    },
  };
});

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { phoneConfig, dial } };
});

vi.mock("@/components/agent/TicketTable", () => ({ TicketTable: () => null }));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, login: "agent", can_use_customer_directory: auth.canUseDirectory },
  }),
}));

beforeEach(() => {
  auth.canUseDirectory = true;
  navigate.mockReset();
  getCustomer.mockReset().mockResolvedValue({
    login: "jane.doe",
    email: "jane@example.com",
    customer_id: "CUST1",
    first_name: "Jane",
    last_name: "Doe",
    phone: "+49 228 555-0101",
    mobile: "0171 1234567",
    company_name: null,
  });
  listTickets.mockReset().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 1 });
  phoneConfig.mockReset().mockResolvedValue({ dial_scheme: "tel" });
  dial.mockReset();
  for (const fn of Object.values(customerCryptoKeys)) fn.mockReset();
  customerCryptoKeys.list.mockResolvedValue(keys({ pgp_enabled: false, smime_enabled: false }));
});

describe("CustomerDetailPage actions", () => {
  it("puts the ticket actions first and lists only this person's tickets", async () => {
    listTickets.mockImplementation((params: { state_type?: string }) =>
      Promise.resolve(
        params.state_type === "open"
          ? { items: [], total: 2, offset: 0, limit: 1 }
          : {
              items: [{ id: 42, tn: "2026100710000142", title: "VPN bricht ab", state: "open", age_seconds: 7200 }],
              total: 1,
              offset: 0,
              limit: 8,
            },
      ),
    );
    renderPage();
    expect(await screen.findByTestId("customer-email-ticket-jane.doe")).toHaveAttribute(
      "data-to",
      "/agent/tickets/new",
    );
    expect(screen.getByTestId("customer-phone-ticket-jane.doe")).toBeInTheDocument();
    expect(await screen.findByTestId("customer-ticket-42")).toHaveTextContent("VPN bricht ab");
    expect(screen.getByTestId("customer-open-count")).toHaveTextContent(/^2 /);
    expect(listTickets).toHaveBeenCalledWith(
      expect.objectContaining({ customer_user_id: "jane.doe" }),
      expect.anything(),
    );
    expect(listTickets).not.toHaveBeenCalledWith(
      expect.objectContaining({ customer_id: "CUST1" }),
      expect.anything(),
    );
  });

  it("offers editing only with the customer edit permission", async () => {
    renderPage();
    await screen.findByTestId("customer-name");
    expect(screen.queryByTestId("customer-edit")).toBeNull();
  });
});

describe("CustomerDetailPage click-to-call", () => {
  it("renders phone and mobile as dial links that open an outbound phone ticket", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <CustomerDetailPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    const mobile = await screen.findByTestId("customer-call-mobile-jane.doe");
    expect(mobile).toHaveAttribute("href", "tel:01711234567");
    expect(screen.getByTestId("customer-call-phone-jane.doe")).toHaveAttribute("href", "tel:+492285550101");
    mobile.addEventListener("click", (e) => e.preventDefault());
    fireEvent.click(mobile);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { type: "phone", direction: "outbound", customer: "jane.doe", number: "0171 1234567" },
    });
  });
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const pgpKey = {
  fingerprint: "A".repeat(40),
  key_id: "AAAAAAAAAAAAAAAA",
  short_id: "AAAAAAAA",
  znuny_key_id: "AAAAAAAA",
  uids: ["Jane Doe <jane@example.com>"],
  emails: ["jane@example.com"],
  created: null,
  expires: "2030-01-01T00:00:00Z",
  status: "good",
  has_secret: false,
  bits: 2048,
  algorithm: "RSA",
  subkey_ids: [],
};

function keys(over: Record<string, unknown> = {}) {
  return {
    pgp_enabled: true,
    smime_enabled: true,
    can_edit: true,
    pgp_keys: [pgpKey],
    smime_certificates: [],
    pgp_key_id: "AAAAAAAAAAAAAAAA",
    smime_filename: null,
    problems: [],
    ...over,
  };
}

describe("CustomerDetailPage keys", () => {
  it("lists the customer's keys and uploads a certificate", async () => {
    customerCryptoKeys.list.mockResolvedValue(keys());
    customerCryptoKeys.uploadSmime.mockResolvedValue(
      keys({
        smime_certificates: [
          {
            filename: "abcd1234.0",
            valid: true,
            hash: "abcd1234",
            subject: "CN=jane",
            issuer: "CN=ca",
            fingerprint: "AA:BB",
            serial: "01",
            not_before: null,
            not_after: "2030-01-01T00:00:00Z",
            emails: ["jane@example.com"],
            status: "valid",
            has_private: false,
            is_ca: false,
          },
        ],
      }),
    );
    renderPage();
    expect(await screen.findByTestId("customer-keys-card")).toBeInTheDocument();
    expect(screen.getAllByTestId("customer-keys-pgp-row")).toHaveLength(1);
    expect(screen.getByText(/Jane Doe <jane@example.com>/)).toBeInTheDocument();

    const pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n";
    const file = new File([pem], "c.pem");
    // jsdom's File has no arrayBuffer().
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => new TextEncoder().encode(pem).buffer,
    });
    fireEvent.change(screen.getByTestId("customer-keys-smime-upload-file"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByTestId("customer-keys-smime-upload-submit"));
    await waitFor(() =>
      expect(customerCryptoKeys.uploadSmime).toHaveBeenCalledWith(
        "jane.doe",
        expect.stringContaining("BEGIN CERTIFICATE"),
      ),
    );
    expect(await screen.findAllByTestId("customer-keys-smime-row")).toHaveLength(1);
  });

  it("is read-only without edit permission", async () => {
    customerCryptoKeys.list.mockResolvedValue(keys({ can_edit: false }));
    renderPage();
    expect(await screen.findByTestId("customer-keys-readonly")).toBeInTheDocument();
    expect(screen.queryByTestId("customer-keys-pgp-delete")).toBeNull();
    expect(screen.queryByTestId("customer-keys-smime-upload-file")).toBeNull();
  });

  it("renders nothing while PGP and S/MIME are disabled", async () => {
    customerCryptoKeys.list.mockResolvedValue(keys({ pgp_enabled: false, smime_enabled: false }));
    renderPage();
    await screen.findByTestId("customer-detail-page");
    await waitFor(() => expect(customerCryptoKeys.list).toHaveBeenCalled());
    expect(screen.queryByTestId("customer-keys-card")).toBeNull();
  });
});

describe("CustomerDetailPage vCard downloads", () => {
  function renderPage() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <CustomerDetailPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
  }

  it("offers only the customer vCard when there is no company", async () => {
    renderPage();
    const link = await screen.findByTestId("customer-vcard");
    expect(link).toHaveAttribute("href", "/api/v1/customers/jane.doe/vcard");
    expect(link).toHaveAttribute("download");
    expect(screen.queryByTestId("company-vcards")).toBeNull();
  });

  it("adds the company vCard link, URL-encoded, when the customer has a company", async () => {
    getCustomer.mockResolvedValue({
      login: "jane.doe",
      email: "jane@example.com",
      customer_id: "CUST 1/x",
      first_name: "Jane",
      last_name: "Doe",
      phone: null,
      mobile: null,
      company_name: "ACME",
    });
    renderPage();
    const link = await screen.findByTestId("company-vcards");
    expect(link).toHaveAttribute("href", "/api/v1/customers/companies/CUST%201%2Fx/vcards");
    expect(link).toHaveAttribute("download");
  });

  it("hides the company vCards without the customer-directory permission", async () => {
    auth.canUseDirectory = false;
    getCustomer.mockResolvedValue({
      login: "jane.doe",
      email: "jane@example.com",
      customer_id: "CUST1",
      first_name: "Jane",
      last_name: "Doe",
      phone: null,
      mobile: null,
      company_name: "ACME",
    });
    renderPage();
    await screen.findByTestId("customer-vcard");
    expect(screen.queryByTestId("company-vcards")).toBeNull();
  });
});

describe("CustomerDetailPage click-to-dial via the PBX", () => {
  async function renderOriginate() {
    phoneConfig.mockResolvedValue({ dial_scheme: "tel", originate: true });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <CustomerDetailPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    const mobile = await screen.findByTestId("customer-call-mobile-jane.doe");
    await waitFor(() => expect(phoneConfig).toHaveBeenCalled());
    // let the phone-config query settle so the link is in originate mode
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    return mobile;
  }

  it("stays on the page and shows the error when the dial is refused", async () => {
    dial.mockRejectedValue(
      new ApiError(409, { detail: "no phone extension set for this agent" }, "/api/v1/phone/dial"),
    );
    const mobile = await renderOriginate();
    fireEvent.click(mobile);
    expect(await screen.findByRole("alert")).toHaveTextContent("no phone extension set for this agent");
    expect(dial).toHaveBeenCalledWith({ number: "0171 1234567", ticket_id: null, name: "Jane Doe" });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("opens the outbound phone ticket after a successful dial", async () => {
    dial.mockResolvedValue({ extension: "60", number: "01711234567" });
    const mobile = await renderOriginate();
    fireEvent.click(mobile);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/tickets/new",
        search: { type: "phone", direction: "outbound", customer: "jane.doe", number: "0171 1234567" },
      }),
    );
  });

  it("keeps the plain tel: behaviour when originate is off", async () => {
    phoneConfig.mockResolvedValue({ dial_scheme: "tel", originate: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <CustomerDetailPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    const mobile = await screen.findByTestId("customer-call-mobile-jane.doe");
    mobile.addEventListener("click", (e) => e.preventDefault());
    fireEvent.click(mobile);
    expect(dial).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});
