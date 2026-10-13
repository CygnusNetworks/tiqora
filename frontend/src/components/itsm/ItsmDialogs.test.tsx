import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ItsmDialogs } from "./ItsmDialogs";
import { makeTicket, PERMS } from "./testSupport";

const { screenDynamicFields, patchTicket, fetchOptions } = vi.hoisted(() => ({
  screenDynamicFields: vi.fn(),
  patchTicket: vi.fn(),
  fetchOptions: vi.fn(),
}));
vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return { ...actual, phoneApi: { ...actual.phoneApi, screenDynamicFields } };
});
vi.mock("@/lib/fieldOptionsApi", () => ({ fieldOptionsApi: { fetch: fetchOptions } }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { patchTicket } };
});

const ITSM_DEFS = [
  { name: "ITSMReviewRequired", label: "Review erforderlich", field_type: "Dropdown", required: false, possible_values: { Yes: "Ja", No: "Nein" } },
];
const DECISION_DEFS = [
  { name: "ITSMDecisionResult", label: "Entscheidung", field_type: "Dropdown", required: false, possible_values: { Approved: "Genehmigt", Rejected: "Abgelehnt" } },
];

function renderDialogs(opts: {
  dialogs?: { itsm_fields: boolean; decision: boolean };
  allowed?: ReadonlySet<string> | null;
  ticket?: ReturnType<typeof makeTicket>;
} = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ItsmDialogs
          ticket={opts.ticket ?? makeTicket()}
          dialogs={opts.dialogs ?? { itsm_fields: true, decision: true }}
          allowedActions={opts.allowed ?? null}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { invalidate };
}

beforeEach(() => {
  screenDynamicFields.mockReset().mockImplementation(async (screen: string) =>
    screen === "AgentTicketDecision" ? DECISION_DEFS : screen === "AgentTicketAddtlITSMField" ? ITSM_DEFS : [],
  );
  patchTicket.mockReset().mockResolvedValue(undefined);
  fetchOptions.mockReset().mockResolvedValue({ options: {} });
});
afterEach(cleanup);

describe("ItsmDialogs", () => {
  it("shows only the buttons of screens that have configured fields", () => {
    renderDialogs({ dialogs: { itsm_fields: true, decision: false } });
    expect(screen.getByTestId("itsm-open-itsm_fields")).toHaveTextContent(/ITSM fields|ITSM-Felder/);
    expect(screen.queryByTestId("itsm-open-decision")).toBeNull();
  });

  it("shows no button without rw or when the screen's action ACL forbids it", () => {
    renderDialogs({ ticket: makeTicket({ permissions: { ...PERMS, rw: false } }) });
    expect(screen.queryByTestId("itsm-open-itsm_fields")).toBeNull();
    cleanup();
    renderDialogs({ allowed: new Set(["AgentTicketDecision"]) });
    expect(screen.queryByTestId("itsm-open-itsm_fields")).toBeNull();
    expect(screen.getByTestId("itsm-open-decision")).toBeTruthy();
  });

  it("opens the ITSM fields dialog with the AgentTicketAddtlITSMField screen", async () => {
    renderDialogs();
    fireEvent.click(screen.getByTestId("itsm-open-itsm_fields"));
    expect(await screen.findByTestId("itsm-dialog-itsm_fields")).toBeTruthy();
    expect(screenDynamicFields).toHaveBeenCalledWith("AgentTicketAddtlITSMField", expect.anything());
    expect(screenDynamicFields).not.toHaveBeenCalledWith("AgentTicketDecision", expect.anything());
    expect(await screen.findByTestId("ticket-df-save")).toBeTruthy();
  });

  it("opens the decision dialog with the AgentTicketDecision screen", async () => {
    renderDialogs();
    fireEvent.click(screen.getByTestId("itsm-open-decision"));
    expect(await screen.findByTestId("itsm-dialog-decision")).toBeTruthy();
    expect(screenDynamicFields).toHaveBeenCalledWith("AgentTicketDecision", expect.anything());
    expect(await screen.findByTestId("ticket-df-save")).toBeTruthy();
  });

  it("saves through the ticket PATCH, closes, and refreshes the ticket queries", async () => {
    const { invalidate } = renderDialogs({ dialogs: { itsm_fields: false, decision: true } });
    fireEvent.click(screen.getByTestId("itsm-open-decision"));
    const select = await screen.findByTestId("ticket-df-ITSMDecisionResult");
    fireEvent.click(select);
    fireEvent.click(await screen.findByText("Genehmigt"));
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { dynamic_fields: { ITSMDecisionResult: ["Approved"] } }));
    await waitFor(() => expect(screen.queryByTestId("itsm-dialog-decision")).toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets"] });
  });

  it("says so while the fields load and when the screen has none to edit", async () => {
    let answer: (defs: unknown[]) => void = () => {};
    screenDynamicFields.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    renderDialogs();
    fireEvent.click(screen.getByTestId("itsm-open-itsm_fields"));
    expect(await screen.findByTestId("ticket-df-loading")).toBeTruthy();
    answer([]);
    expect(await screen.findByTestId("ticket-df-empty")).toBeTruthy();
    fireEvent.click(screen.getByTestId("ticket-df-cancel"));
    await waitFor(() => expect(screen.queryByTestId("itsm-dialog-itsm_fields")).toBeNull());
  });

  it("closes on cancel without saving", async () => {
    renderDialogs();
    fireEvent.click(screen.getByTestId("itsm-open-itsm_fields"));
    fireEvent.click(await screen.findByTestId("ticket-df-cancel"));
    await waitFor(() => expect(screen.queryByTestId("itsm-dialog-itsm_fields")).toBeNull());
    expect(patchTicket).not.toHaveBeenCalled();
  });
});
