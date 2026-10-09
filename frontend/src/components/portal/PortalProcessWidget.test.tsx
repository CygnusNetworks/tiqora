import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/portalApi";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { PortalProcessWidget } from "./PortalProcessWidget";

const api = vi.hoisted(() => ({
  ticketState: vi.fn(),
  listStartable: vi.fn(),
  startForm: vi.fn(),
  dialogForm: vi.fn(),
  enroll: vi.fn(),
  submit: vi.fn(),
}));

vi.mock("@/lib/portalProcessApi", () => ({ portalProcessApi: api }));

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const field = (name: string, kind: string, extra: Partial<ProcessDialogFormData["fields"][number]> = {}) => ({
  name,
  kind,
  label: name,
  required: false,
  description_short: "",
  description_long: "",
  default: null,
  options: [],
  ...extra,
});

// Customer dialog form as the server sends it (agent-only fields are dropped server-side).
const dialogForm: ProcessDialogFormData = {
  process_entity_id: "P",
  activity_dialog_entity_id: "AD-1",
  name: "Rückfrage beantworten",
  description_short: "",
  description_long: "",
  submit_button_text: "Antworten",
  submit_advice_text: "",
  fields: [field("Article", "article", { required: true })],
} as ProcessDialogFormData;

const startFormData: ProcessDialogFormData = {
  ...dialogForm,
  activity_dialog_entity_id: "AD-S",
  name: "Start",
  submit_button_text: "Starten",
  fields: [field("Title", "text", { required: true })],
} as ProcessDialogFormData;

const inProcess = {
  process_entity_id: "P",
  process_name: "Bestellung",
  activity_entity_id: "A1",
  activity_name: "Rückfrage",
  available_dialogs: [{ entity_id: "AD-1", name: "Antwort senden", description_short: "" }],
  available_transitions_count: 1,
  process_state: "Active",
  can_start: false,
};
const notInProcess = {
  process_entity_id: null,
  process_name: null,
  activity_entity_id: null,
  activity_name: null,
  available_dialogs: [],
  available_transitions_count: 0,
  process_state: null,
  can_start: true,
};

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  void i18n.changeLanguage("de");
});

describe("PortalProcessWidget", () => {
  it("opens a dialog through the portal dialog form and renders its article fields", async () => {
    api.ticketState.mockResolvedValue(inProcess);
    api.dialogForm.mockResolvedValue(dialogForm);
    wrap(<PortalProcessWidget ticketId={5} />);

    fireEvent.click(await screen.findByTestId("portal-process-dialog-AD-1"));

    expect(await screen.findByTestId("portal-process-dialog-submit")).toHaveTextContent("Antworten");
    expect(api.dialogForm).toHaveBeenCalledWith(5, "AD-1", expect.anything());
    expect(screen.getByLabelText(/Betreff/)).toBeInTheDocument();
  });

  it("submits the dialog values through the portal API", async () => {
    api.ticketState.mockResolvedValue(inProcess);
    api.dialogForm.mockResolvedValue(dialogForm);
    api.submit.mockResolvedValue({ activity_changed: true });
    wrap(<PortalProcessWidget ticketId={5} />);

    fireEvent.click(await screen.findByTestId("portal-process-dialog-AD-1"));
    fireEvent.change(await screen.findByLabelText(/Betreff/), { target: { value: "Hallo" } });
    fireEvent.change(screen.getByLabelText(/Text/), { target: { value: "Danke" } });
    fireEvent.click(screen.getByTestId("portal-process-dialog-submit"));

    await waitFor(() =>
      expect(api.submit).toHaveBeenCalledWith(5, {
        activity_dialog_entity_id: "AD-1",
        field_values: { Subject: "Hallo", Body: "Danke" },
      }),
    );
  });

  it.each([
    [403, "Sie dürfen diesen Prozess hier nicht starten."],
    [404, "Dieser Prozess oder dieses Ticket existiert nicht mehr."],
    [409, "Dieser Schritt ist im Moment nicht möglich."],
  ])("shows a plain German message when the dialog submit fails with %i", async (status, text) => {
    api.ticketState.mockResolvedValue(inProcess);
    api.dialogForm.mockResolvedValue(dialogForm);
    api.submit.mockRejectedValue(new ApiError(status, null, "/x"));
    wrap(<PortalProcessWidget ticketId={5} />);

    fireEvent.click(await screen.findByTestId("portal-process-dialog-AD-1"));
    fireEvent.change(await screen.findByLabelText(/Betreff/), { target: { value: "Hallo" } });
    fireEvent.change(screen.getByLabelText(/Text/), { target: { value: "Danke" } });
    fireEvent.click(screen.getByTestId("portal-process-dialog-submit"));

    expect(await screen.findByTestId("portal-process-dialog-error")).toHaveTextContent(text);
  });

  it.each([
    [403, "Sie dürfen diesen Prozess hier nicht starten."],
    [404, "Dieser Prozess oder dieses Ticket existiert nicht mehr."],
    [500, "Formular konnte nicht geladen werden."],
  ])("shows a message when the dialog form fails to load with %i", async (status, text) => {
    api.ticketState.mockResolvedValue(inProcess);
    api.dialogForm.mockRejectedValue(new ApiError(status, null, "/x"));
    wrap(<PortalProcessWidget ticketId={5} />);

    fireEvent.click(await screen.findByTestId("portal-process-dialog-AD-1"));

    expect(await screen.findByTestId("portal-process-dialog-load-error")).toHaveTextContent(text);
    expect(api.submit).not.toHaveBeenCalled();
  });

  it("enrolls a ticket through the portal start form with the ticket id", async () => {
    api.ticketState.mockResolvedValue(notInProcess);
    api.listStartable.mockResolvedValue([
      { id: 1, entity_id: "P", name: "Bestellung", state_entity_id: "S1", state_name: "Active" },
    ]);
    api.startForm.mockResolvedValue(startFormData);
    api.enroll.mockResolvedValue(inProcess);
    wrap(<PortalProcessWidget ticketId={5} />);

    fireEvent.click(await screen.findByTestId("portal-process-start"));
    fireEvent.click(await screen.findByTestId("portal-process-select"));
    fireEvent.click(screen.getByTestId("portal-process-select-menu-option-P"));
    fireEvent.change(await screen.findByLabelText(/Titel/), { target: { value: "Laptop" } });
    fireEvent.click(screen.getByTestId("portal-process-start-submit"));

    expect(api.listStartable).toHaveBeenCalledWith({ ticketId: 5 }, expect.anything());
    expect(api.startForm).toHaveBeenCalledWith("P", { ticketId: 5 }, expect.anything());
    await waitFor(() =>
      expect(api.enroll).toHaveBeenCalledWith(5, { process_entity_id: "P", field_values: { Title: "Laptop" } }),
    );
  });
});
