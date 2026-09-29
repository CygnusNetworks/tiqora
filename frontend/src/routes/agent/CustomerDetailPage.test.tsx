import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { CustomerDetailPage } from "./CustomerDetailPage";

const { navigate, getCustomer, listTickets, phoneConfig } = vi.hoisted(() => ({
  navigate: vi.fn(),
  getCustomer: vi.fn(),
  listTickets: vi.fn(),
  phoneConfig: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ login: "jane.doe" }),
  Link: ({ children, ...rest }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a data-to={String(rest.to)}>{children}</a>
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getCustomer, listTickets } };
});

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { phoneConfig } };
});

vi.mock("@/components/agent/TicketTable", () => ({ TicketTable: () => null }));

beforeEach(() => {
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
    const mobile = await screen.findByTestId("customer-dial-mobile");
    expect(mobile).toHaveAttribute("href", "tel:01711234567");
    expect(screen.getByTestId("customer-dial-phone")).toHaveAttribute("href", "tel:+492285550101");
    mobile.addEventListener("click", (e) => e.preventDefault());
    fireEvent.click(mobile);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { type: "phone", direction: "outbound", customer: "jane.doe", number: "0171 1234567" },
    });
  });
});
