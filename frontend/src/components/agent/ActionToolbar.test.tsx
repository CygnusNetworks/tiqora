import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ActionToolbar } from "./ActionToolbar";
import type { TicketDetail } from "@/lib/api";

const {
  patchTicket,
  listReferencePriorities,
  listReferenceStates,
  searchReferenceCustomers,
} = vi.hoisted(() => ({
  patchTicket: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  searchReferenceCustomers: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      patchTicket,
      listReferencePriorities,
      listReferenceStates,
      listReferenceAgents: vi.fn().mockResolvedValue([]),
      searchReferenceCustomers,
      listQueues: vi.fn().mockResolvedValue([]),
      listTicketLinks: vi.fn().mockResolvedValue([]),
    },
  };
});

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 42, login: "agent" } }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
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

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Test",
    queue_id: 1,
    state_id: 4,
    state: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 2,
    create_time: "2024-06-01T12:00:00Z",
    change_time: "2024-06-01T12:00:00Z",
    is_watched: false,
    can_write: true,
    ...overrides,
  } as TicketDetail;
}

describe("ActionToolbar", () => {
  beforeEach(() => {
    patchTicket.mockReset().mockResolvedValue(undefined);
    searchReferenceCustomers.mockReset().mockResolvedValue([]);
    listReferencePriorities.mockReset().mockResolvedValue([
      { id: 3, name: "3 normal" },
      { id: 5, name: "5 very high" },
    ]);
    listReferenceStates.mockReset().mockResolvedValue([
      { id: 4, name: "open", type_name: "open" },
      { id: 2, name: "closed successful", type_name: "closed" },
      { id: 8, name: "pending reminder", type_name: "pending reminder" },
    ]);
  });

  it("renders the toolbar with core actions", async () => {
    wrap(<ActionToolbar ticket={makeTicket()} />);
    expect(screen.getByTestId("action-toolbar")).toBeInTheDocument();
    expect(screen.getByTestId("toolbar-priority")).toBeInTheDocument();
    expect(screen.getByTestId("toolbar-lock")).toBeInTheDocument();
    expect(screen.getByTestId("toolbar-print")).toBeInTheDocument();
  });

  it("patches priority when picked from the dropdown", async () => {
    wrap(<ActionToolbar ticket={makeTicket()} />);
    await waitFor(() =>
      expect(screen.getByTestId("toolbar-priority")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("toolbar-priority"));
    const item = await screen.findByText("5 very high");
    fireEvent.click(item);
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { priority_id: 5 }));
  });

  it("shows localised state names in the state menu", async () => {
    wrap(<ActionToolbar ticket={makeTicket()} />);
    await waitFor(() =>
      expect(screen.getByTestId("toolbar-state")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("toolbar-state"));
    expect(await screen.findByText("Open")).toBeInTheDocument();
    expect(screen.getByText("Closed successful")).toBeInTheDocument();
    expect(screen.getByText("Pending reminder")).toBeInTheDocument();
    // Raw Znuny compound name should not appear untranslated.
    expect(screen.queryByText("closed successful")).toBeNull();
  });

  it("locks an unlocked ticket", async () => {
    wrap(<ActionToolbar ticket={makeTicket({ lock: "unlock" })} />);
    fireEvent.click(screen.getByTestId("toolbar-lock"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { lock: "lock" }));
  });

  it("watches using the current user id", async () => {
    wrap(<ActionToolbar ticket={makeTicket({ is_watched: false })} />);
    fireEvent.click(screen.getByTestId("toolbar-watch"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { watcher_user_id: 42 }));
  });

  it("disables mutating actions when the agent cannot write", () => {
    wrap(<ActionToolbar ticket={makeTicket({ can_write: false })} />);
    expect(screen.getByTestId("toolbar-priority")).toBeDisabled();
    expect(screen.getByTestId("toolbar-move")).toBeDisabled();
    // Print stays enabled — it is a client-side action.
    expect(screen.getByTestId("toolbar-print")).not.toBeDisabled();
  });

  it("shows the customer number as a badge on each search result", async () => {
    searchReferenceCustomers.mockResolvedValue([
      {
        login: "alice",
        full_name: "Alice Example",
        email: "alice@example.com",
        customer_id: "C-10042",
      },
    ]);
    wrap(<ActionToolbar ticket={makeTicket()} />);
    fireEvent.click(screen.getByTestId("toolbar-customer"));
    expect(screen.getByTestId("customer-picker-dialog")).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/select|auswählen/i);
    fireEvent.change(input, { target: { value: "ali" } });
    const badge = await screen.findByTestId("customer-picker-id-alice");
    expect(badge).toHaveTextContent("C-10042");
    // Pill/badge styling (rounded + muted tone), not plain text.
    expect(badge.className).toMatch(/rounded/);
    expect(badge.className).toMatch(/text-muted|border-hairline/);
    expect(screen.getByTestId("customer-picker-result-alice")).toHaveTextContent("Alice Example");
    expect(screen.getByTestId("customer-picker-result-alice")).toHaveTextContent(
      "alice@example.com",
    );
  });
});
