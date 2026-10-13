import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { makeCi, makeItsm, makeTicket, PERMS } from "./testSupport";
import { TicketCisPanel } from "./TicketCisPanel";

const { ticketItsm, ticketCis, linkCi, unlinkCi, cis, catalog, linkTypes } = vi.hoisted(() => ({
  ticketItsm: vi.fn(),
  ticketCis: vi.fn(),
  linkCi: vi.fn(),
  unlinkCi: vi.fn(),
  cis: vi.fn(),
  catalog: vi.fn(),
  linkTypes: vi.fn(),
}));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, ticketItsm, ticketCis, linkCi, unlinkCi, cis, catalog, linkTypes } };
});

function link(ci: ReturnType<typeof makeCi>) {
  return { ticket_id: 7, ci, link_type: "RelevantTo", direction: "both" as const, created_at: null };
}

function renderPanel(ticket = makeTicket()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TicketCisPanel ticket={ticket} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { invalidate };
}

beforeEach(() => {
  ticketItsm.mockReset().mockResolvedValue(makeItsm());
  ticketCis.mockReset().mockResolvedValue([link(makeCi(201))]);
  linkCi.mockReset().mockResolvedValue(link(makeCi(202)));
  unlinkCi.mockReset().mockResolvedValue(undefined);
  cis.mockReset().mockResolvedValue({ items: [makeCi(201), makeCi(202)], total: 2, truncated: false });
  catalog.mockReset().mockResolvedValue({ classes: [], deployment_states: [], incident_states: [] });
  linkTypes.mockReset().mockResolvedValue([]);
});
afterEach(cleanup);

describe("TicketCisPanel", () => {
  it("renders nothing and does not load CIs while the ITSM is disabled", async () => {
    ticketItsm.mockResolvedValue(makeItsm({ enabled: false }));
    renderPanel();
    await waitFor(() => expect(ticketItsm).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ticket-cis")).toBeNull();
    expect(ticketCis).not.toHaveBeenCalled();
  });

  it("lists number, name, class, deployment and incident state of each linked CI", async () => {
    renderPanel();
    const row = await screen.findByTestId("ticket-ci-201");
    expect(row).toHaveTextContent("100200201");
    expect(row).toHaveTextContent("srv-201.example.org");
    expect(row).toHaveTextContent("Computer");
    expect(row).toHaveTextContent("Produktiv");
    expect(row).toHaveTextContent("Störung");
    expect(row.querySelector("a")).toHaveAttribute("href", "/agent/itsm/cis/znuny/201");
  });

  it("shows the empty state", async () => {
    ticketCis.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByTestId("ticket-cis-empty")).toBeTruthy();
  });

  it("removes a link with its link type and refreshes the ITSM summary", async () => {
    const { invalidate } = renderPanel();
    fireEvent.click(await screen.findByTestId("ticket-ci-remove-201"));
    await waitFor(() => expect(unlinkCi).toHaveBeenCalledWith(7, "znuny", 201, "RelevantTo"));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "itsm"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "cis"] });
  });

  it("links a CI through the picker (search, pick) and closes it", async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId("ticket-cis-add"));
    expect(await screen.findByTestId("ci-picker")).toBeTruthy();
    fireEvent.change(screen.getByTestId("ci-picker-search"), { target: { value: "202" } });
    await waitFor(() => expect(cis).toHaveBeenLastCalledWith(expect.objectContaining({ q: "202" }), expect.anything()));
    fireEvent.click(await screen.findByTestId("ci-picker-hit-202"));
    await waitFor(() => expect(linkCi).toHaveBeenCalledWith(7, { ci: "znuny:202", link_type: null }));
    await waitFor(() => expect(screen.queryByTestId("ci-picker")).toBeNull());
  });

  it("keeps the picker open and shows the error when linking fails", async () => {
    linkCi.mockRejectedValue(new ApiError(404, { detail: { code: "ci_not_found" } }, "/x"));
    renderPanel();
    fireEvent.click(await screen.findByTestId("ticket-cis-add"));
    fireEvent.click(await screen.findByTestId("ci-picker-hit-202"));
    expect(await screen.findByTestId("ci-picker-error")).toBeTruthy();
    expect(screen.getByTestId("ci-picker")).toBeTruthy();
  });

  it("offers 'In Znuny bearbeiten' in Mode A only, and only for http(s) URLs", async () => {
    ticketCis.mockResolvedValue([
      link(makeCi(201, { edit_url: "https://znuny.example.org/index.pl?Action=AgentITSMConfigItemEdit;ConfigItemID=201" })),
      link(makeCi(202, { edit_url: "javascript:alert(1)" })),
    ]);
    renderPanel();
    const edit = await screen.findByTestId("ticket-ci-edit-201");
    expect(edit).toHaveAttribute("href", expect.stringMatching(/^https:\/\/znuny\.example\.org\//));
    expect(edit).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.queryByTestId("ticket-ci-edit-202")).toBeNull();
    cleanup();

    ticketItsm.mockResolvedValue(makeItsm({ mode: "tiqora" }));
    ticketCis.mockResolvedValue([link(makeCi(201, { edit_url: "https://znuny.example.org/x" }))]);
    renderPanel();
    await screen.findByTestId("ticket-ci-201");
    expect(screen.queryByTestId("ticket-ci-edit-201")).toBeNull();
  });

  it("shows an unavailable CMDB as a plain message without link actions", async () => {
    ticketCis.mockRejectedValue(new ApiError(409, { detail: { code: "cmdb_not_installed" } }, "/x"));
    renderPanel();
    expect(await screen.findByTestId("ticket-cis-unavailable")).toHaveTextContent(/CMDB/);
    expect(screen.queryByTestId("ticket-cis-add")).toBeNull();
  });

  it("hides link and remove actions without rw on the ticket", async () => {
    renderPanel(makeTicket({ permissions: { ...PERMS, rw: false } }));
    await screen.findByTestId("ticket-ci-201");
    expect(screen.queryByTestId("ticket-cis-add")).toBeNull();
    expect(screen.queryByTestId("ticket-ci-remove-201")).toBeNull();
  });
});
