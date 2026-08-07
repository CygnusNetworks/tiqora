import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { TicketMetaCounters } from "./TicketMetaCounters";

const {
  listTicketMentions,
  createTicketMention,
  deleteTicketMention,
  listTicketTimeAccounting,
  createTicketTimeAccounting,
  deleteTicketTimeAccounting,
  listReferenceAgents,
} = vi.hoisted(() => ({
  listTicketMentions: vi.fn(),
  createTicketMention: vi.fn(),
  deleteTicketMention: vi.fn(),
  listTicketTimeAccounting: vi.fn(),
  createTicketTimeAccounting: vi.fn(),
  deleteTicketTimeAccounting: vi.fn(),
  listReferenceAgents: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listTicketMentions,
      createTicketMention,
      deleteTicketMention,
      listTicketTimeAccounting,
      createTicketTimeAccounting,
      deleteTicketTimeAccounting,
      listReferenceAgents,
    },
  };
});

function wrap() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <TicketMetaCounters ticketId={7} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TicketMetaCounters", () => {
  beforeEach(() => {
    listTicketMentions
      .mockReset()
      .mockResolvedValue([{ id: 11, user_id: 2, user_name: "Ada Lovelace", user_login: "ada" }]);
    createTicketMention.mockReset().mockResolvedValue({ id: 12 });
    deleteTicketMention.mockReset().mockResolvedValue(undefined);
    listTicketTimeAccounting.mockReset().mockResolvedValue([]);
    createTicketTimeAccounting.mockReset().mockResolvedValue({ id: 30 });
    deleteTicketTimeAccounting.mockReset().mockResolvedValue(undefined);
    listReferenceAgents.mockReset().mockResolvedValue([
      { id: 2, login: "ada", full_name: "Ada Lovelace" },
      { id: 3, login: "bob", full_name: "Bob Stone" },
    ]);
  });

  it("shows the mention count and marks the chip as filled", async () => {
    wrap();
    const chip = await screen.findByTestId("ticket-counter-mentions");
    await waitFor(() => expect(chip).toHaveTextContent("1"));
    expect(chip).toHaveAttribute("data-filled", "true");
  });

  it("leaves the time chip unfilled while nothing is booked", async () => {
    wrap();
    const chip = await screen.findByTestId("ticket-counter-time");
    await waitFor(() => expect(chip).toHaveTextContent("0"));
    expect(chip).not.toHaveAttribute("data-filled");
  });

  it("sums booked units into the time chip", async () => {
    listTicketTimeAccounting.mockResolvedValue([
      { id: 1, time_unit: 15, create_by: 2, create_by_login: "ada" },
      { id: 2, time_unit: 7.5, create_by: 3, create_by_login: "bob" },
    ]);
    wrap();
    const chip = await screen.findByTestId("ticket-counter-time");
    await waitFor(() => expect(chip).toHaveTextContent("22.5"));
    expect(chip).toHaveAttribute("data-filled", "true");
  });

  it("lists mentions in the popover and removes one", async () => {
    wrap();
    fireEvent.click(await screen.findByTestId("ticket-counter-mentions"));
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ticket-mention-remove-11"));
    await waitFor(() => expect(deleteTicketMention).toHaveBeenCalledWith(7, 11));
  });

  it("adds a mention from the picker, offering only agents not yet mentioned", async () => {
    wrap();
    fireEvent.click(await screen.findByTestId("ticket-counter-mentions"));
    fireEvent.click(await screen.findByTestId("ticket-mention-add"));
    expect(await screen.findByText("Bob Stone")).toBeInTheDocument();
    // Ada is already mentioned, so she is not offered again — the only "Ada
    // Lovelace" on screen is the existing entry in the list above.
    expect(screen.getAllByText("Ada Lovelace")).toHaveLength(1);
    fireEvent.click(screen.getByText("Bob Stone"));
    await waitFor(() => expect(createTicketMention).toHaveBeenCalledWith(7, { user_id: 3 }));
  });

  it("books time from the popover and clears the field", async () => {
    wrap();
    fireEvent.click(await screen.findByTestId("ticket-counter-time"));
    const input = await screen.findByTestId("ticket-time-units");
    fireEvent.change(input, { target: { value: "20" } });
    fireEvent.click(screen.getByTestId("ticket-time-book"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 20 }),
    );
  });

  it("refuses to book a zero or empty value", async () => {
    wrap();
    fireEvent.click(await screen.findByTestId("ticket-counter-time"));
    const book = await screen.findByTestId("ticket-time-book");
    expect(book).toBeDisabled();
    fireEvent.change(screen.getByTestId("ticket-time-units"), { target: { value: "0" } });
    expect(book).toBeDisabled();
    expect(createTicketTimeAccounting).not.toHaveBeenCalled();
  });
});
