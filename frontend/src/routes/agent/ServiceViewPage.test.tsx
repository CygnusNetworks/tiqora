import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { makeCi } from "@/components/itsm/testSupport";
import { ServiceViewPage } from "./ServiceViewPage";

const { listReferenceServices, listTickets, status, service, serviceCis, search } = vi.hoisted(() => ({
  listReferenceServices: vi.fn(),
  listTickets: vi.fn(),
  status: vi.fn(),
  service: vi.fn(),
  serviceCis: vi.fn(),
  search: { current: {} as Record<string, unknown> },
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useSearch: () => search.current,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listReferenceServices, listTickets } };
});
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, status, service, serviceCis } };
});
vi.mock("@/components/agent/TicketTable", () => ({ TicketTable: () => <div data-testid="ticket-table" /> }));

const statusOf = (mode: string) => ({ mode, source: "znuny", ci_available: mode !== "off", ci_read_only: true, ci_source: "znuny", unavailable_code: null });
const SERVICE = {
  service_id: 8,
  name: "Kundenportal",
  service_type: "Kundenservice",
  criticality: "5 sehr hoch",
  description: "Portal für Kunden",
  owner_user_id: 1,
  owner_name: "Alex Turner",
  portal_visible: true,
  znuny_owned: true,
  min_minutes_between_incidents: null,
  effective_min_minutes_between_incidents: 30,
  znuny_sla_min_times: {},
  incident_state: {
    service_id: 8,
    state: { key: "43", name: "Störung", functionality: "incident" },
    cis_incident: 1,
    cis_warning: 0,
  },
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ServiceViewPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  search.current = { service_id: 8 };
  listReferenceServices.mockReset().mockResolvedValue([{ id: 8, name: "Kundenportal" }]);
  listTickets.mockReset().mockResolvedValue({ items: [], total: 0 });
  status.mockReset().mockResolvedValue(statusOf("znuny_readthrough"));
  service.mockReset().mockResolvedValue(SERVICE);
  serviceCis.mockReset().mockResolvedValue([makeCi(1), makeCi(2)]);
});
afterEach(cleanup);

describe("ServiceViewPage ITSM panel", () => {
  it("shows incident state, owner, type, criticality, description and the service's CIs", async () => {
    renderPage();
    const panel = await screen.findByTestId("service-itsm");
    expect(panel).toHaveTextContent("Störung");
    expect(screen.getByTestId("service-itsm-owner")).toHaveTextContent("Alex Turner");
    expect(screen.getByTestId("service-itsm-type")).toHaveTextContent("Kundenservice");
    expect(screen.getByTestId("service-itsm-criticality")).toHaveTextContent("5 sehr hoch");
    expect(screen.getByTestId("service-itsm-description")).toHaveTextContent("Portal für Kunden");
    expect(await screen.findByTestId("service-ci-1")).toHaveTextContent("srv-1.example.org");
    expect(screen.getByTestId("service-ci-1").querySelector("a")).toHaveAttribute("href", "/agent/itsm/cis/znuny/1");
    expect(service).toHaveBeenCalledWith(8, expect.anything());
    expect(serviceCis).toHaveBeenCalledWith(8, undefined, expect.anything());
    expect(screen.getByTestId("ticket-table")).toBeInTheDocument();
  });

  it("says when no CI is allocated", async () => {
    serviceCis.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByTestId("service-itsm-no-cis")).toBeInTheDocument();
  });

  it("does not show or load anything ITSM while the ITSM is off", async () => {
    status.mockResolvedValue(statusOf("off"));
    renderPage();
    await waitFor(() => expect(status).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("service-itsm")).toBeNull();
    expect(service).not.toHaveBeenCalled();
    expect(serviceCis).not.toHaveBeenCalled();
    expect(screen.getByTestId("ticket-table")).toBeInTheDocument();
  });

  it("shows nothing before a service is picked", async () => {
    search.current = {};
    renderPage();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("service-itsm")).toBeNull();
    expect(service).not.toHaveBeenCalled();
  });

  it("reports a service whose ITSM data fails to load without hiding the ticket list", async () => {
    service.mockRejectedValue(new ApiError(404, { detail: { code: "service_not_found" } }, "/x"));
    renderPage();
    expect(await screen.findByTestId("service-itsm-error")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-table")).toBeInTheDocument();
  });
});
