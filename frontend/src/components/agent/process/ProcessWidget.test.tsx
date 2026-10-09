import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@tiqora/api-client";
import { ProcessWidget } from "./ProcessWidget";
import type { ProcessDialogFormData, ProcessSummary, TicketProcessState } from "@/lib/processApi";

const { listStartable, startForm, enroll, ticketState, dialogForm, submit } = vi.hoisted(() => ({
  listStartable: vi.fn(),
  startForm: vi.fn(),
  enroll: vi.fn(),
  ticketState: vi.fn(),
  dialogForm: vi.fn(),
  submit: vi.fn(),
}));

vi.mock("@/lib/processApi", () => ({
  processApi: { listStartable, startForm, enroll, ticketState, dialogForm, submit },
}));

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

const notInProcessState: TicketProcessState = {
  process_entity_id: null,
  process_name: null,
  activity_entity_id: null,
  activity_name: null,
  available_dialogs: [],
  available_transitions_count: 0,
  process_state: null,
  can_start: true,
};

const activeState: TicketProcessState = {
  process_entity_id: "Process-1",
  process_name: "Onboarding",
  activity_entity_id: "Activity-1",
  activity_name: "Collect info",
  available_dialogs: [
    { entity_id: "ActivityDialog-1", name: "Set Title", description_short: "Set the title" },
  ],
  available_transitions_count: 1,
  process_state: "Active",
  can_start: false,
};

const dialogDetail: ProcessDialogFormData = {
  process_entity_id: "Process-1",
  activity_dialog_entity_id: "ActivityDialog-1",
  name: "Set Title",
  description_short: "Set the title",
  description_long: "Fill in the ticket title.",
  submit_button_text: "Submit",
  submit_advice_text: "",
  fields: [
    { name: "Title", kind: "text", label: "Title", required: true, description_short: "", description_long: "", default: null, options: [] },
  ],
};

const processSummaries: ProcessSummary[] = [
  { id: 1, entity_id: "Process-1", name: "Onboarding", state_entity_id: "S1", state_name: "Active" },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ProcessWidget", () => {
  it("renders the start-process affordance when the ticket is not in a process", async () => {
    ticketState.mockResolvedValue(notInProcessState);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() => expect(screen.getByTestId("process-widget-inactive")).toBeInTheDocument());
    expect(screen.getByTestId("process-widget-start-button")).toBeInTheDocument();
  });

  it("renders the current activity and dialog buttons when in a process", async () => {
    ticketState.mockResolvedValue(activeState);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() =>
      expect(screen.getByTestId("process-widget-activity-name")).toHaveTextContent("Collect info"),
    );
    expect(screen.getByTestId("process-dialog-button-ActivityDialog-1")).toBeInTheDocument();
  });

  it("opens the dialog and renders dynamic form fields from the fetched detail", async () => {
    ticketState.mockResolvedValue(activeState);
    dialogForm.mockResolvedValue(dialogDetail);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() =>
      expect(screen.getByTestId("process-dialog-button-ActivityDialog-1")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("process-dialog-button-ActivityDialog-1"));

    await waitFor(() => expect(screen.getByTestId("process-field-Title")).toBeInTheDocument());
  });

  it("blocks submit and shows an error when a required field is left blank", async () => {
    ticketState.mockResolvedValue(activeState);
    dialogForm.mockResolvedValue(dialogDetail);
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-dialog-button-ActivityDialog-1"));
    await screen.findByTestId("process-field-Title");

    fireEvent.click(screen.getByTestId("process-dialog-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("process-dialog-required-error")).toBeInTheDocument(),
    );
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByTestId("process-field-Title-error")).toBeInTheDocument();
  });

  it("submits successfully and refreshes the ticket state", async () => {
    ticketState.mockResolvedValueOnce(activeState).mockResolvedValueOnce(notInProcessState);
    dialogForm.mockResolvedValue(dialogDetail);
    submit.mockResolvedValue({
      activity_changed: true,
      new_activity_entity_id: null,
      transition_entity_id: null,
      unsupported_actions: [],
      state: notInProcessState,
    });
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-dialog-button-ActivityDialog-1"));
    const titleInput = await screen.findByTestId("process-field-Title");
    fireEvent.change(titleInput, { target: { value: "New title" } });

    fireEvent.click(screen.getByTestId("process-dialog-submit"));

    await waitFor(() => expect(submit).toHaveBeenCalledWith(42, {
      activity_dialog_entity_id: "ActivityDialog-1",
      field_values: { Title: "New title" },
    }));
    await waitFor(() => expect(screen.queryByTestId("process-dialog-form")).not.toBeInTheDocument());
  });

  it("hides the start button when the ticket cannot start a process", async () => {
    ticketState.mockResolvedValue({ ...notInProcessState, can_start: false });
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() => expect(screen.getByTestId("process-widget-inactive")).toBeInTheDocument());
    expect(screen.queryByTestId("process-widget-start-button")).not.toBeInTheDocument();
  });

  it("shows the inactive note instead of dialog buttons for a switched-off process", async () => {
    ticketState.mockResolvedValue({ ...activeState, process_state: "Inactive" });
    wrap(<ProcessWidget ticketId={42} />);

    expect(await screen.findByTestId("process-widget-inactive-note")).toHaveTextContent(
      i18n.t("process.widget.inactive"),
    );
    expect(screen.queryByTestId("process-dialog-button-ActivityDialog-1")).not.toBeInTheDocument();
  });

  it("starts a process in two steps: pick, then the start form, then enroll with the ticket", async () => {
    ticketState.mockResolvedValue(notInProcessState);
    listStartable.mockResolvedValue(processSummaries);
    startForm.mockResolvedValue(dialogDetail);
    enroll.mockResolvedValue(activeState);
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-widget-start-button"));
    await screen.findByTestId("process-start-select");
    expect(listStartable).toHaveBeenCalledWith({ ticketId: 42 }, expect.anything());

    // SelectField: open the menu and click the option in its portal panel.
    fireEvent.click(screen.getByTestId("process-start-select"));
    fireEvent.click(screen.getByTestId("process-start-select-menu-option-Process-1"));
    fireEvent.click(screen.getByTestId("process-start-next"));

    await screen.findByTestId("process-start-form");
    expect(startForm).toHaveBeenCalledWith("Process-1", { ticketId: 42 }, expect.anything());
    fireEvent.change(await screen.findByLabelText(/Title/), { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("process-start-submit"));

    await waitFor(() =>
      expect(enroll).toHaveBeenCalledWith(42, {
        process_entity_id: "Process-1",
        field_values: expect.anything(),
      }),
    );
  });

  it("shows a plain message when the server refuses the start (403)", async () => {
    ticketState.mockResolvedValue(notInProcessState);
    listStartable.mockResolvedValue(processSummaries);
    startForm.mockRejectedValue(new ApiError(403, { detail: "Forbidden" }, "/x"));
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-widget-start-button"));
    await screen.findByTestId("process-start-select");
    fireEvent.click(screen.getByTestId("process-start-select"));
    fireEvent.click(screen.getByTestId("process-start-select-menu-option-Process-1"));
    fireEvent.click(screen.getByTestId("process-start-next"));

    expect(await screen.findByTestId("process-start-load-error")).toHaveTextContent(
      i18n.t("process.start.forbidden"),
    );
  });
});
