import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { makeItsm, makeTicket } from "./testSupport";
import { TicketItsmChips } from "./TicketItsmChips";

const { ticketItsm, listReferencePriorities, createTicketLink, screenDynamicFields } = vi.hoisted(() => ({
  ticketItsm: vi.fn(),
  listReferencePriorities: vi.fn(),
  createTicketLink: vi.fn(),
  screenDynamicFields: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listReferencePriorities, createTicketLink } };
});
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ticketItsm } };
});
vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { ...actual.phoneApi, screenDynamicFields } };
});

function renderChips(ticket = makeTicket()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TicketItsmChips ticket={ticket} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  ticketItsm.mockReset();
  listReferencePriorities.mockReset().mockResolvedValue([
    { id: 1, name: "1 sehr hoch" },
    { id: 3, name: "3 normal" },
  ]);
  createTicketLink.mockReset().mockResolvedValue(undefined);
  screenDynamicFields.mockReset().mockResolvedValue([]);
});
afterEach(cleanup);

describe("TicketItsmChips", () => {
  it("renders nothing while the ITSM summary says disabled", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ enabled: false, role: "incident", criticality: "5 sehr hoch" }));
    renderChips();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalledWith(7, expect.anything()));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ticket-itsm-chips")).toBeNull();
  });

  it("renders nothing when the summary cannot be loaded", async () => {
    ticketItsm.mockRejectedValue(new ApiError(409, { detail: { code: "itsm_off" } }, "/x"));
    renderChips();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ticket-itsm-chips")).toBeNull();
  });

  it("shows role, criticality and impact", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "major_incident", criticality: "5 sehr hoch", impact: "3 normal" }));
    renderChips();
    expect(await screen.findByTestId("itsm-chip-role")).toHaveTextContent("Major Incident");
    expect(screen.getByTestId("itsm-chip-criticality")).toHaveTextContent("5 sehr hoch");
    expect(screen.getByTestId("itsm-chip-impact")).toHaveTextContent("3 normal");
  });

  it("names the matrix priority only when it differs from the current one", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ role: "incident", matrix_priority_id: 1 }));
    renderChips(makeTicket({ priority_id: 3 }));
    expect(await screen.findByTestId("itsm-chip-matrix")).toHaveTextContent("Matrix: 1 sehr hoch");
    cleanup();

    ticketItsm.mockResolvedValue(makeItsm({ role: "incident", matrix_priority_id: 3 }));
    renderChips(makeTicket({ priority_id: 3 }));
    await screen.findByTestId("itsm-chip-role");
    expect(screen.queryByTestId("itsm-chip-matrix")).toBeNull();
  });

  it("shows the service incident state as a badge", async () => {
    ticketItsm.mockResolvedValue(
      makeItsm({
        role: "incident",
        service: {
          service_id: 1, name: "Kundenportal", service_type: null, criticality: null, description: null, owner_user_id: null,
          owner_name: null, portal_visible: true, znuny_owned: true, min_minutes_between_incidents: null,
          effective_min_minutes_between_incidents: 30, znuny_sla_min_times: {},
          incident_state: {
            service_id: 1, cis_incident: 1, cis_warning: 0,
            state: { key: "3", name: "Störung", functionality: "incident" },
          },
        },
      }),
    );
    renderChips();
    const chip = await screen.findByTestId("itsm-chip-service-state");
    expect(chip).toHaveTextContent("Kundenportal");
    expect(chip.querySelector("[data-state-color='danger']")).not.toBeNull();
  });

  it("warns about a recent incident of the service and links it on request", async () => {
    ticketItsm.mockResolvedValue(
      makeItsm({
        role: "incident",
        recent_incident: {
          ticket_id: 99, tn: "20260701000099", title: "Portal down", type_name: "Incident", minutes_ago: 12,
          window_minutes: 30, created_at: "2026-07-01T10:00:00Z",
        },
      }),
    );
    renderChips();
    const warning = await screen.findByTestId("itsm-flood-warning");
    expect(warning).toHaveTextContent("12");
    expect(warning).toHaveTextContent("#20260701000099");
    fireEvent.click(screen.getByTestId("itsm-flood-link"));
    await waitFor(() =>
      expect(createTicketLink).toHaveBeenCalledWith(7, { target_ticket_id: 99, link_type: "Normal" }),
    );
    expect(await screen.findByTestId("itsm-flood-linked")).toBeTruthy();
    expect(screen.queryByTestId("itsm-flood-link")).toBeNull();
  });

  it("offers no link action without write access", async () => {
    ticketItsm.mockResolvedValue(
      makeItsm({
        role: "incident",
        recent_incident: {
          ticket_id: 99, tn: "1", title: "t", type_name: "Incident", minutes_ago: 5, window_minutes: 30,
          created_at: "2026-07-01T10:00:00Z",
        },
      }),
    );
    renderChips(makeTicket({ permissions: { ro: true, rw: false, note: false, owner: false, priority: false, move_into: false, create: false } }));
    await screen.findByTestId("itsm-flood-warning");
    expect(screen.queryByTestId("itsm-flood-link")).toBeNull();
  });
});
