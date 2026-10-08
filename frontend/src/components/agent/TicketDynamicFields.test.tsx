import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError, type TicketDetail } from "@/lib/api";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { TicketDynamicFields } from "./TicketDynamicFields";

const { screenDynamicFields, fetchOptions, patchTicket } = vi.hoisted(() => ({
  screenDynamicFields: vi.fn(),
  fetchOptions: vi.fn(),
  patchTicket: vi.fn(),
}));

vi.mock("@/lib/phoneApi", () => ({ phoneApi: { screenDynamicFields } }));
vi.mock("@/lib/fieldOptionsApi", () => ({ fieldOptionsApi: { fetch: fetchOptions } }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { patchTicket } };
});

const PERMS = { ro: true, rw: true, note: true, owner: true, priority: true, move_into: true, create: true };

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Printer jam",
    can_write: true,
    permissions: PERMS,
    dynamic_fields: [{ name: "Area", label: "Bereich", field_type: "Dropdown", values: ["net"] }],
    ...overrides,
  } as TicketDetail;
}

const DEFS = [
  { name: "Area", label: "Bereich", field_type: "Dropdown", required: false, possible_values: { net: "Netz", tv: "TV" } },
  { name: "Due", label: "Fällig", field_type: "Date", required: false, possible_values: null },
];

function renderIt(ticket: TicketDetail) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ui = (tk: TicketDetail) => (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TicketDynamicFields ticket={tk} />
      </I18nextProvider>
    </QueryClientProvider>
  );
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const { rerender } = render(ui(ticket));
  return { invalidate, rerenderWith: (tk: TicketDetail) => rerender(ui(tk)) };
}

beforeEach(() => {
  screenDynamicFields.mockReset().mockResolvedValue([]);
  fetchOptions.mockReset().mockResolvedValue({ options: {} });
  patchTicket.mockReset().mockResolvedValue(undefined);
  setDisplayTimeZone("Europe/Berlin");
});
afterEach(() => setDisplayTimeZone(null));

describe("TicketDynamicFields", () => {
  it("shows values read-only and hides Bearbeiten without rw", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    renderIt(
      makeTicket({
        permissions: { ...PERMS, rw: false },
        dynamic_fields: [{ name: "Area", label: "Bereich", field_type: "Dropdown", values: ["net"] }],
      }),
    );
    // Dropdown shows the configured label once the field config is known
    expect(await screen.findByText("Netz")).toBeInTheDocument();
    expect(screen.getByText("Bereich")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-df-edit")).not.toBeInTheDocument();
  });

  it("shows the raw value of a dropdown whose config is unknown", async () => {
    renderIt(makeTicket());
    expect(await screen.findByText("net")).toBeInTheDocument();
    // nothing is configured for editing -> no edit button even with rw
    await waitFor(() => expect(screenDynamicFields).toHaveBeenCalledWith("AgentTicketFreeText", expect.anything()));
    expect(screen.queryByTestId("ticket-df-edit")).not.toBeInTheDocument();
  });

  it("formats Date, DateTime (agent zone) and Checkbox read-only", async () => {
    renderIt(
      makeTicket({
        permissions: { ...PERMS, rw: false },
        dynamic_fields: [
          { name: "Due", label: "Fällig", field_type: "Date", values: ["2026-02-01T00:00:00"] },
          { name: "When", label: "Wann", field_type: "DateTime", values: ["2026-07-01 08:30:00"] },
          { name: "Ok", label: "Ok", field_type: "Checkbox", values: ["1"] },
          { name: "No", label: "Nein-Feld", field_type: "Checkbox", values: ["0"] },
        ],
      }),
    );
    expect(await screen.findByText("2026-02-01")).toBeInTheDocument();
    expect(screen.getByText("2026-07-01 10:30")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-df-value-Ok")).toHaveTextContent("Yes");
    expect(screen.getByTestId("ticket-df-value-No")).toHaveTextContent("No");
  });

  it("edits configured fields incl. empty ones and saves only changes", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    fetchOptions.mockResolvedValue({ options: { DynamicField_Area: [{ value: "tv", label: "TV" }] } });
    const { invalidate } = renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "2026-02-01" } });
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    await waitFor(() =>
      expect(patchTicket).toHaveBeenCalledWith(7, { dynamic_fields: { Due: ["2026-02-01 00:00:00"] } }),
    );
    expect(patchTicket).toHaveBeenCalledTimes(1);
    // the zoom page's ticket queries (detail + history share the prefix) are refreshed
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets"] }));
    await waitFor(() => expect(screen.queryByTestId("ticket-df-save")).not.toBeInTheDocument());
  });

  it("asks for ACL options with the edit action and the current draft", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    await waitFor(() => expect(fetchOptions).toHaveBeenCalled());
    const [body] = fetchOptions.mock.calls[0];
    expect(body.fields).toEqual(["DynamicField_Area"]);
    expect(body.context).toMatchObject({
      ticket_id: 7,
      action: "AgentTicketFreeText",
      dynamic_fields: { Area: ["net"] },
    });
  });

  it("ACL options replace the static ones", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    fetchOptions.mockResolvedValue({ options: { DynamicField_Area: [{ value: "tv", label: "TV" }] } });
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    await waitFor(() => expect(fetchOptions).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("ticket-df-Area"));
    expect(await screen.findByTestId("ticket-df-Area-menu-option-tv")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-df-Area-menu-option-net")).not.toBeInTheDocument();
  });

  it("blocks saving while a required field is empty", async () => {
    screenDynamicFields.mockResolvedValue([{ ...DEFS[1], required: true }]);
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    expect(await screen.findByTestId("ticket-df-Due-error")).toBeInTheDocument();
    expect(patchTicket).not.toHaveBeenCalled();
  });

  it("shows the server error and stays in edit mode", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    patchTicket.mockRejectedValue(new ApiError(422, { detail: "Datum ungültig" }, "/api/v1/tickets/7"));
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "2026-02-01" } });
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    expect(await screen.findByTestId("ticket-df-error")).toHaveTextContent("Datum ungültig");
    expect(screen.getByTestId("ticket-df-save")).toBeInTheDocument();
  });

  it("cancel leaves edit mode without saving", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.click(screen.getByTestId("ticket-df-cancel"));
    expect(screen.queryByTestId("ticket-df-save")).not.toBeInTheDocument();
    expect(patchTicket).not.toHaveBeenCalled();
  });

  it("does not overwrite a value another agent set while editing", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    const { rerenderWith } = renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    // a refetch brings in someone else's new Area value
    rerenderWith(
      makeTicket({ dynamic_fields: [{ name: "Area", label: "Bereich", field_type: "Dropdown", values: ["tv"] }] }),
    );
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "2026-02-01" } });
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    await waitFor(() =>
      expect(patchTicket).toHaveBeenCalledWith(7, { dynamic_fields: { Due: ["2026-02-01 00:00:00"] } }),
    );
  });

  it("clears a field with an empty list and patches several fields at once", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    renderIt(
      makeTicket({
        dynamic_fields: [
          { name: "Area", label: "Bereich", field_type: "Dropdown", values: ["net"] },
          { name: "Due", label: "Fällig", field_type: "Date", values: ["2026-01-05 00:00:00"] },
        ],
      }),
    );
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("ticket-df-Area"));
    fireEvent.click(await screen.findByTestId("ticket-df-Area-menu-option-tv"));
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { dynamic_fields: { Area: ["tv"], Due: [] } }));
  });

  it("keeps the section open and shows Gespeichert after saving", async () => {
    screenDynamicFields.mockResolvedValue(DEFS);
    renderIt(makeTicket());
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "2026-02-01" } });
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    const saved = await screen.findByTestId("ticket-df-saved");
    expect(saved).toBeVisible();
    expect(screen.getByTestId("ticket-df")).toHaveAttribute("open");
  });
});
