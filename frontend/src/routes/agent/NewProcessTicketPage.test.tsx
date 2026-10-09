import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { NewProcessTicketPage } from "./NewProcessTicketPage";

const { navigate, searchParams, listStartable, startForm, createTicket } = vi.hoisted(() => ({
  navigate: vi.fn(),
  searchParams: { current: {} as Record<string, unknown> },
  listStartable: vi.fn(),
  startForm: vi.fn(),
  createTicket: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams.current,
}));

vi.mock("@/lib/processApi", () => ({ processApi: { listStartable, startForm, createTicket } }));

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

const summaries = [
  { id: 1, entity_id: "P", name: "Bestellung", state_entity_id: "S1", state_name: "Active" },
  { id: 2, entity_id: "Q", name: "Urlaub", state_entity_id: "S1", state_name: "Active" },
];

const startFormData: ProcessDialogFormData = {
  process_entity_id: "P",
  activity_dialog_entity_id: "AD-1",
  name: "Bestellung anlegen",
  description_short: "",
  description_long: "",
  submit_button_text: "Bestellen",
  submit_advice_text: "Danach prüft der Einkauf.",
  fields: [
    {
      name: "Title",
      kind: "text",
      label: "Title",
      required: true,
      description_short: "",
      description_long: "",
      default: null,
      options: [],
    },
  ],
};

beforeEach(() => {
  navigate.mockReset();
  listStartable.mockReset();
  startForm.mockReset();
  createTicket.mockReset();
  searchParams.current = {};
  void i18n.changeLanguage("de");
});

async function fillTitleAndSubmit() {
  fireEvent.change(await screen.findByLabelText(/Titel/), { target: { value: "Laptop" } });
  fireEvent.click(screen.getByTestId("new-process-submit"));
}

describe("NewProcessTicketPage", () => {
  it("renders the start form of ?process=P with the dialog's button text and advice", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("new-process-submit")).toHaveTextContent("Bestellen");
    expect(screen.getByText("Danach prüft der Einkauf.")).toBeInTheDocument();
    expect(screen.queryByTestId("new-process-select")).not.toBeInTheDocument();
    expect(startForm).toHaveBeenCalledWith("P", { ticketId: undefined }, expect.anything());
  });

  it("creates the ticket and opens it", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockResolvedValue({ ticket_id: 77, ticket_number: "2026100077" });
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    await waitFor(() =>
      expect(createTicket).toHaveBeenCalledWith({
        process_entity_id: "P",
        field_values: { Title: "Laptop" },
      }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/tickets/$ticketId",
        params: { ticketId: "77" },
      }),
    );
  });

  it("shows the server message of a 400 in new-process-error", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockRejectedValue(new ApiError(400, { detail: "Queue nicht erlaubt" }, "/x"));
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    expect(await screen.findByTestId("new-process-error")).toHaveTextContent("Queue nicht erlaubt");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows a plain German message for a 403 on submit", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockRejectedValue(new ApiError(403, { detail: "Forbidden" }, "/x"));
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    expect(await screen.findByTestId("new-process-error")).toHaveTextContent(
      "Sie dürfen diesen Prozess hier nicht starten.",
    );
  });

  it("shows the plain conflict text for a 409, not the backend exception text", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockRejectedValue(new ApiError(409, { detail: "ProcessNotStartable('P')" }, "/x"));
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    const msg = await screen.findByTestId("new-process-error");
    expect(msg).toHaveTextContent("Dieser Prozess kann für dieses Ticket im Moment nicht gestartet werden.");
    expect(msg).not.toHaveTextContent("ProcessNotStartable");
  });

  it("blocks submit on a missing required field without calling the API", async () => {
    searchParams.current = { process: "P" };
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    wrap(<NewProcessTicketPage />);

    fireEvent.click(await screen.findByTestId("new-process-submit"));

    expect(await screen.findByTestId("new-process-field-Title-error")).toBeInTheDocument();
    expect(createTicket).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("lets the agent pick a process when none is in the URL", async () => {
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    wrap(<NewProcessTicketPage />);

    fireEvent.click(await screen.findByTestId("new-process-select"));
    fireEvent.click(screen.getByTestId("new-process-select-menu-option-P"));

    expect(await screen.findByTestId("new-process-submit")).toBeInTheDocument();
    expect(startForm).toHaveBeenCalledWith("P", { ticketId: undefined }, expect.anything());
  });

  it("says so when there is no process to start", async () => {
    listStartable.mockResolvedValue([]);
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("new-process-empty")).toHaveTextContent(
      "Es gibt keinen Prozess, den Sie starten können.",
    );
    expect(startForm).not.toHaveBeenCalled();
  });
});
