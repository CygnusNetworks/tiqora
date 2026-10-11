import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/portalApi";
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

vi.mock("@/lib/portalProcessApi", () => ({ portalProcessApi: { listStartable, startForm, createTicket } }));

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

// A customer start form as the server sends it (agent-only fields are dropped server-side).
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
      time_units: "0",
      uploads: false,
    },
  ],
};

beforeEach(() => {
  navigate.mockReset();
  listStartable.mockReset();
  startForm.mockReset();
  createTicket.mockReset();
  searchParams.current = { process: "P" };
  void i18n.changeLanguage("de");
});

async function fillTitleAndSubmit() {
  fireEvent.change(await screen.findByLabelText(/Titel/), { target: { value: "Laptop" } });
  fireEvent.click(screen.getByTestId("portal-new-process-submit"));
}

describe("portal NewProcessTicketPage", () => {
  it("renders the start form of ?process=P with the dialog's button text and advice", async () => {
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("portal-new-process-page")).toBeInTheDocument();
    expect(await screen.findByTestId("portal-new-process-submit")).toHaveTextContent("Bestellen");
    expect(screen.getByText("Danach prüft der Einkauf.")).toBeInTheDocument();
    expect(startForm).toHaveBeenCalledWith("P", { ticketId: undefined }, expect.anything());
  });

  it("creates the ticket via the portal API and opens it in the portal", async () => {
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockResolvedValue({ ticket_id: 77, ticket_number: "2026100077" });
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    await waitFor(() =>
      expect(createTicket).toHaveBeenCalledWith({ process_entity_id: "P", field_values: { Title: "Laptop" } }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/portal/tickets/$ticketId",
        params: { ticketId: "77" },
      }),
    );
  });

  it.each([
    [403, "Sie dürfen diesen Prozess hier nicht starten."],
    [404, "Dieser Prozess oder dieses Ticket existiert nicht mehr."],
    [409, "Dieser Prozess kann für dieses Ticket im Moment nicht gestartet werden."],
  ])("shows a plain German message for a %i on submit", async (status, text) => {
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    createTicket.mockRejectedValue(new ApiError(status, null, "/x"));
    wrap(<NewProcessTicketPage />);

    await fillTitleAndSubmit();

    expect(await screen.findByTestId("portal-new-process-error")).toHaveTextContent(text);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("lets the customer pick a process when none is in the URL", async () => {
    searchParams.current = {};
    listStartable.mockResolvedValue(summaries);
    startForm.mockResolvedValue(startFormData);
    wrap(<NewProcessTicketPage />);

    fireEvent.click(await screen.findByTestId("portal-new-process-select"));
    fireEvent.click(screen.getByTestId("portal-new-process-select-menu-option-Q"));

    expect(await screen.findByTestId("portal-new-process-submit")).toBeInTheDocument();
    expect(startForm).toHaveBeenCalledWith("Q", { ticketId: undefined }, expect.anything());
  });

  it("refuses a process that is not in the startable list", async () => {
    searchParams.current = { process: "Z" };
    listStartable.mockResolvedValue(summaries);
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("portal-new-process-error")).toHaveTextContent(
      "Sie dürfen diesen Prozess hier nicht starten.",
    );
    expect(startForm).not.toHaveBeenCalled();
  });

  it("shows an error when the process list cannot be loaded", async () => {
    listStartable.mockRejectedValue(new ApiError(500, null, "/x"));
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("portal-new-process-error")).toHaveTextContent(
      "Prozesse konnten nicht geladen werden.",
    );
    expect(startForm).not.toHaveBeenCalled();
  });

  it("shows a plain message when the start form is forbidden or gone", async () => {
    listStartable.mockResolvedValue(summaries);
    startForm.mockRejectedValue(new ApiError(404, null, "/x"));
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("portal-new-process-load-error")).toHaveTextContent(
      "Dieser Prozess oder dieses Ticket existiert nicht mehr.",
    );
  });

  it("says so when there is no process to start", async () => {
    listStartable.mockResolvedValue([]);
    wrap(<NewProcessTicketPage />);

    expect(await screen.findByTestId("portal-new-process-empty")).toHaveTextContent(
      "Es gibt keinen Prozess, den Sie starten können.",
    );
  });
});
