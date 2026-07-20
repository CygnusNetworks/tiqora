import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ProcessWidget } from "./ProcessWidget";
import type {
  ActivityDialogDetailOut,
  ProcessSummaryOut,
  TicketProcessStateOut,
} from "@/lib/api";

const {
  getTicketProcessState,
  listProcesses,
  startTicketProcess,
  getActivityDialog,
  submitActivityDialog,
  listQueues,
} = vi.hoisted(() => ({
  getTicketProcessState: vi.fn(),
  listProcesses: vi.fn(),
  startTicketProcess: vi.fn(),
  getActivityDialog: vi.fn(),
  submitActivityDialog: vi.fn(),
  listQueues: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      getTicketProcessState,
      listProcesses,
      startTicketProcess,
      getActivityDialog,
      submitActivityDialog,
      listQueues,
    },
  };
});

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

const notInProcessState: TicketProcessStateOut = {
  process_entity_id: null,
  process_name: null,
  activity_entity_id: null,
  activity_name: null,
  available_dialogs: [],
  available_transitions_count: 0,
};

const activeState: TicketProcessStateOut = {
  process_entity_id: "Process-1",
  process_name: "Onboarding",
  activity_entity_id: "Activity-1",
  activity_name: "Collect info",
  available_dialogs: [
    { entity_id: "ActivityDialog-1", name: "Set Title", description_short: "Set the title" },
  ],
  available_transitions_count: 1,
};

const dialogDetail: ActivityDialogDetailOut = {
  entity_id: "ActivityDialog-1",
  name: "Set Title",
  description_short: "Set the title",
  description_long: "Fill in the ticket title.",
  field_order: ["Title"],
  fields: {
    Title: {
      display: "1",
      default_value: "",
      description_short: "Title",
      description_long: "Ticket title",
      config: {},
    },
  },
  submit_advice_text: "",
  submit_button_text: "Submit",
};

const processSummaries: ProcessSummaryOut[] = [
  { id: 1, entity_id: "Process-1", name: "Onboarding", state_entity_id: "S1" },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ProcessWidget", () => {
  it("renders the start-process affordance when the ticket is not in a process", async () => {
    getTicketProcessState.mockResolvedValue(notInProcessState);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() => expect(screen.getByTestId("process-widget-inactive")).toBeInTheDocument());
    expect(screen.getByTestId("process-widget-start-button")).toBeInTheDocument();
  });

  it("renders the current activity and dialog buttons when in a process", async () => {
    getTicketProcessState.mockResolvedValue(activeState);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() =>
      expect(screen.getByTestId("process-widget-activity-name")).toHaveTextContent("Collect info"),
    );
    expect(screen.getByTestId("process-dialog-button-ActivityDialog-1")).toBeInTheDocument();
  });

  it("opens the dialog and renders dynamic form fields from the fetched detail", async () => {
    getTicketProcessState.mockResolvedValue(activeState);
    getActivityDialog.mockResolvedValue(dialogDetail);
    listQueues.mockResolvedValue([]);
    wrap(<ProcessWidget ticketId={42} />);

    await waitFor(() =>
      expect(screen.getByTestId("process-dialog-button-ActivityDialog-1")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("process-dialog-button-ActivityDialog-1"));

    await waitFor(() => expect(screen.getByTestId("process-field-Title")).toBeInTheDocument());
  });

  it("blocks submit and shows an error when a required field is left blank", async () => {
    getTicketProcessState.mockResolvedValue(activeState);
    getActivityDialog.mockResolvedValue(dialogDetail);
    listQueues.mockResolvedValue([]);
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-dialog-button-ActivityDialog-1"));
    await screen.findByTestId("process-field-Title");

    fireEvent.click(screen.getByTestId("process-dialog-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("process-dialog-required-error")).toBeInTheDocument(),
    );
    expect(submitActivityDialog).not.toHaveBeenCalled();
  });

  it("submits successfully and refreshes the ticket state", async () => {
    getTicketProcessState.mockResolvedValueOnce(activeState).mockResolvedValueOnce(notInProcessState);
    getActivityDialog.mockResolvedValue(dialogDetail);
    listQueues.mockResolvedValue([]);
    submitActivityDialog.mockResolvedValue({
      activity_changed: true,
      new_activity_entity_id: null,
      transition_entity_id: null,
      unsupported_actions: [],
      state: notInProcessState,
    });
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-dialog-button-ActivityDialog-1"));
    const titleInput = await screen.findByTestId("process-field-Title-input");
    fireEvent.change(titleInput, { target: { value: "New title" } });

    fireEvent.click(screen.getByTestId("process-dialog-submit"));

    await waitFor(() => expect(submitActivityDialog).toHaveBeenCalledWith(42, {
      activity_dialog_entity_id: "ActivityDialog-1",
      field_values: { Title: "New title" },
    }));
    await waitFor(() => expect(screen.queryByTestId("process-dialog-form")).not.toBeInTheDocument());
  });

  it("starts a process from the start-process dialog", async () => {
    getTicketProcessState.mockResolvedValue(notInProcessState);
    listProcesses.mockResolvedValue(processSummaries);
    startTicketProcess.mockResolvedValue(activeState);
    wrap(<ProcessWidget ticketId={42} />);

    fireEvent.click(await screen.findByTestId("process-widget-start-button"));
    await screen.findByTestId("process-start-select");

    fireEvent.change(screen.getByTestId("process-start-select"), {
      target: { value: "Process-1" },
    });
    fireEvent.click(screen.getByTestId("process-start-submit"));

    await waitFor(() =>
      expect(startTicketProcess).toHaveBeenCalledWith(42, { process_entity_id: "Process-1" }),
    );
  });
});
