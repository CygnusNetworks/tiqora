import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { TicketDetail } from "@/lib/api";
import { TicketHeader } from "./TicketHeader";

// TicketHeader now embeds TicketHeaderActions (pills + Antworten/Notiz/Mehr),
// which fetches reference data and articles — mock the same surface
// ActionToolbar.test.tsx mocks.
const { listReferencePriorities, listReferenceStates, listArticles, patchTicket } = vi.hoisted(
  () => ({
    listReferencePriorities: vi.fn(),
    listReferenceStates: vi.fn(),
    listArticles: vi.fn(),
    patchTicket: vi.fn(),
  }),
);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listReferencePriorities,
      listReferenceStates,
      listArticles,
      patchTicket,
      listQueues: vi.fn().mockResolvedValue([]),
      listTicketLinks: vi.fn().mockResolvedValue([]),
      listReferenceAgents: vi.fn().mockResolvedValue([]),
      listReferenceTypes: vi.fn().mockResolvedValue([]),
      listReferenceServices: vi.fn().mockResolvedValue([]),
      listReferenceSlas: vi.fn().mockResolvedValue([]),
      ticketAclFieldOptions: vi.fn().mockResolvedValue({}),
      searchReferenceCustomers: vi.fn().mockResolvedValue([]),
    },
  };
});

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 42, login: "agent" } }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  Link: ({
    children,
    to,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
    [k: string]: unknown;
  }) => (
    <a href={typeof to === "string" ? to : "#"} {...rest}>
      {children}
    </a>
  ),
}));

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Printer jam",
    queue_id: 1,
    queue_name: "Support",
    state_id: 4,
    state: "open",
    state_type: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 2,
    owner_name: "Ada",
    create_time: "2024-06-01T12:00:00Z",
    change_time: "2024-06-01T12:00:00Z",
    is_watched: false,
    can_write: true,
    ...overrides,
  } as TicketDetail;
}

function wrap(ticket: TicketDetail, overflowItems?: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <TicketHeader
          ticket={ticket}
          overflowItems={overflowItems}
          canNote
          onOpenNote={vi.fn()}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TicketHeader", () => {
  beforeEach(() => {
    listReferencePriorities.mockReset().mockResolvedValue([
      { id: 3, name: "3 normal" },
      { id: 5, name: "5 very high" },
    ]);
    listReferenceStates.mockReset().mockResolvedValue([
      { id: 1, name: "new", type_name: "new" },
      { id: 4, name: "open", type_name: "open" },
      { id: 2, name: "closed successful", type_name: "closed" },
      { id: 8, name: "pending reminder", type_name: "pending reminder" },
    ]);
    listArticles.mockReset().mockResolvedValue([]);
    patchTicket.mockReset().mockResolvedValue(undefined);
  });

  it("marks the pending segment of the status bar with the exact pending state", async () => {
    wrap(makeTicket({ state: "pending reminder", state_type: "pending reminder", state_id: 8 }));
    const pending = await screen.findByTestId("ticket-status-pending");
    expect(pending).toHaveTextContent("Pending reminder");
    expect(pending).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("ticket-pill-priority")).toHaveTextContent("normal");
  });

  it("marks a new ticket's own segment in the status bar", async () => {
    wrap(makeTicket({ state: "new", state_type: "new", state_id: 1 }));
    expect(await screen.findByTestId("ticket-status-1")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("ticket-status-4")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("ticket-header-new-badge")).toBeNull();
  });

  it("lists the page's own items at the end of the single ⋯ menu", async () => {
    wrap(makeTicket(), <button data-testid="overflow-stub">History</button>);
    fireEvent.click(screen.getByTestId("ticket-actions-more"));
    expect(await screen.findByTestId("ticket-actions-more-menu")).toContainElement(
      screen.getByTestId("overflow-stub"),
    );
  });

  it("renders the queue/owner/customer pills and the primary action buttons", () => {
    wrap(makeTicket({ customer_id: "C-9", customer_user_id: "bob" }));
    expect(screen.getByTestId("ticket-pill-queue")).toHaveTextContent("Support");
    expect(screen.getByTestId("ticket-pill-owner")).toHaveTextContent("Ada");
    expect(screen.getByTestId("ticket-pill-customer")).toHaveTextContent("bob");
    expect(screen.getByTestId("ticket-actions-reply")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-actions-note")).toBeInTheDocument();
    expect(screen.getByTestId("ticket-actions-more")).toBeInTheDocument();
  });

  it("patches priority when picked from the priority pill's menu", async () => {
    wrap(makeTicket());
    await waitFor(() => expect(screen.getByTestId("ticket-pill-priority")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("ticket-pill-priority"));
    const item = await screen.findByText("5 very high");
    fireEvent.click(item);
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { priority_id: 5 }));
  });
});
