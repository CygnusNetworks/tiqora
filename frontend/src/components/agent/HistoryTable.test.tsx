import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { HistoryTable } from "./HistoryTable";

const { listHistory } = vi.hoisted(() => ({
  listHistory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { listHistory },
}));

function renderTable(order?: "asc" | "desc") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <HistoryTable ticketId={7} order={order} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("HistoryTable", () => {
  beforeEach(() => {
    listHistory.mockReset();
    void i18n.changeLanguage("en");
  });

  it("shows a loading spinner before data arrives", () => {
    listHistory.mockReturnValue(new Promise(() => {}));
    renderTable();
    expect(screen.queryByTestId("history-table")).not.toBeInTheDocument();
    expect(document.querySelector('[role="status"]')).toBeInTheDocument();
  });

  it("shows an empty state when there is no history", async () => {
    listHistory.mockResolvedValue([]);
    renderTable();
    expect(await screen.findByTestId("history-table")).toBeInTheDocument();
    expect(screen.getByText(/no history|Keine Historie/i)).toBeInTheDocument();
  });

  it("renders history rows with rendered text and creator", async () => {
    listHistory.mockResolvedValue([
      {
        id: 1,
        ticket_id: 7,
        name: "Ticket created",
        rendered: "Ticket created by Ada",
        history_type_id: 1,
        history_type: "NewTicket",
        owner_id: 2,
        create_time: "2026-06-01T09:00:00Z",
        create_by: 2,
        create_by_login: "ada",
      },
      {
        id: 2,
        ticket_id: 7,
        name: "Priority updated",
        rendered: "Priority changed to high",
        history_type_id: 2,
        history_type: null,
        owner_id: 3,
        create_time: "2026-06-02T10:00:00Z",
        create_by: 3,
        create_by_login: null,
      },
    ]);
    renderTable();

    expect(await screen.findByTestId("history-rendered-1")).toHaveTextContent(
      "Ticket created by Ada",
    );
    expect(screen.getByTestId("history-rendered-2")).toHaveTextContent(
      "Priority changed to high",
    );
    // create_by_login present -> shown; falls back to numeric create_by when null.
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // history_type falls back to history_type_id when null.
    expect(screen.getByText("NewTicket")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("passes the order prop through to listHistory", async () => {
    listHistory.mockResolvedValue([]);
    renderTable("asc");
    await screen.findByTestId("history-table");
    expect(listHistory).toHaveBeenCalledWith(7, "asc");
  });

  it("defaults to desc order when none is given", async () => {
    listHistory.mockResolvedValue([]);
    renderTable();
    await screen.findByTestId("history-table");
    expect(listHistory).toHaveBeenCalledWith(7, "desc");
  });
});
