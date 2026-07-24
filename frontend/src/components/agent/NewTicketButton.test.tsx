import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { NewTicketButton } from "./NewTicketButton";

const { navigate, listQueues } = vi.hoisted(() => ({
  navigate: vi.fn(),
  listQueues: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

vi.mock("@/lib/api", () => ({
  api: { listQueues },
}));

/** Renders and waits for the queues query to settle before returning, so
 * clicks in tests exercise the post-load branch (single vs multi-queue)
 * instead of the transient loading state where `queues` is still []. */
async function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <NewTicketButton />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(client.getQueryData(["queues"])).toBeDefined());
  return result;
}

describe("NewTicketButton", () => {
  beforeEach(() => {
    navigate.mockClear();
    listQueues.mockReset();
    void i18n.changeLanguage("en");
  });

  it("navigates directly to the new-ticket form (no queue) when there are zero queues", async () => {
    listQueues.mockResolvedValue([]);
    await renderButton();
    const btn = screen.getByTestId("new-ticket-button");
    fireEvent.click(btn);
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/tickets/new", search: {} });
    expect(screen.queryByTestId("new-ticket-queue-menu")).not.toBeInTheDocument();
  });

  it("navigates directly with the queue pre-selected when there is exactly one valid queue", async () => {
    listQueues.mockResolvedValue([{ id: 1, name: "Support", group_id: 1, valid: true }]);
    await renderButton();
    const btn = screen.getByTestId("new-ticket-button");
    fireEvent.click(btn);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { queue_id: 1 },
    });
  });

  it("ignores invalid queues when deciding single vs multi", async () => {
    listQueues.mockResolvedValue([
      { id: 1, name: "Support", group_id: 1, valid: true },
      { id: 2, name: "Archived", group_id: 1, valid: false },
    ]);
    await renderButton();
    const btn = screen.getByTestId("new-ticket-button");
    fireEvent.click(btn);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { queue_id: 1 },
    });
  });

  it("shows a queue picker menu when there is more than one valid queue", async () => {
    listQueues.mockResolvedValue([
      { id: 1, name: "Support", group_id: 1, valid: true },
      { id: 2, name: "Sales", group_id: 1, valid: true },
    ]);
    await renderButton();
    const btn = screen.getByTestId("new-ticket-button");
    fireEvent.click(btn);

    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByTestId("new-ticket-queue-menu")).toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-queue-1")).toHaveTextContent("Support");
    expect(screen.getByTestId("new-ticket-queue-2")).toHaveTextContent("Sales");

    fireEvent.click(screen.getByTestId("new-ticket-queue-2"));
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/new",
      search: { queue_id: 2 },
    });
  });

  it("shows only the short name (after '::') for nested queues in the picker", async () => {
    listQueues.mockResolvedValue([
      { id: 1, name: "Support::Tier1", group_id: 1, valid: true },
      { id: 2, name: "Sales", group_id: 1, valid: true },
    ]);
    await renderButton();
    fireEvent.click(screen.getByTestId("new-ticket-button"));
    expect(screen.getByTestId("new-ticket-queue-1")).toHaveTextContent("Tier1");
    expect(screen.getByTestId("new-ticket-queue-1")).not.toHaveTextContent("Support::Tier1");
  });

  it("flattens nested queue trees so children appear as pickable options", async () => {
    listQueues.mockResolvedValue([
      {
        id: 1,
        name: "Support",
        group_id: 1,
        valid: true,
        children: [{ id: 3, name: "Support::Escalated", group_id: 1, valid: true }],
      },
      { id: 2, name: "Sales", group_id: 1, valid: true },
    ]);
    await renderButton();
    fireEvent.click(screen.getByTestId("new-ticket-button"));
    expect(screen.getByTestId("new-ticket-queue-3")).toHaveTextContent("Escalated");
  });
});
