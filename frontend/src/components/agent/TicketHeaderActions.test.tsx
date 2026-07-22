import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { TicketDetail } from "@/lib/api";
import { TicketHeaderActions } from "./TicketHeaderActions";

const {
  patchTicket,
  listReferencePriorities,
  listReferenceStates,
  listArticles,
  getReplyDraft,
} = vi.hoisted(() => ({
  patchTicket: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  listArticles: vi.fn(),
  getReplyDraft: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      patchTicket,
      listReferencePriorities,
      listReferenceStates,
      listArticles,
      getReplyDraft,
      listTemplates: vi.fn().mockResolvedValue([]),
      listQueues: vi.fn().mockResolvedValue([]),
      listTicketLinks: vi.fn().mockResolvedValue([]),
      listReferenceAgents: vi.fn().mockResolvedValue([
        { id: 2, login: "ada", full_name: "Ada Lovelace" },
      ]),
      searchReferenceCustomers: vi.fn().mockResolvedValue([]),
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

const ALL_PERMS = {
  ro: true,
  move_into: true,
  create: true,
  note: true,
  owner: true,
  priority: true,
  rw: true,
};

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: 7,
    tn: "20240601000001",
    title: "Test",
    queue_id: 1,
    queue_name: "Support",
    state_id: 4,
    state: "open",
    priority_id: 3,
    priority: "3 normal",
    lock_id: 1,
    lock: "unlock",
    owner_id: 2,
    owner_name: "Ada",
    customer_id: "C-9",
    customer_user_id: "bob",
    create_time: "2024-06-01T12:00:00Z",
    change_time: "2024-06-01T12:00:00Z",
    is_watched: false,
    can_write: true,
    permissions: ALL_PERMS,
    ...overrides,
  } as TicketDetail;
}

describe("TicketHeaderActions", () => {
  beforeEach(() => {
    patchTicket.mockReset().mockResolvedValue(undefined);
    listReferencePriorities.mockReset().mockResolvedValue([
      { id: 3, name: "3 normal" },
      { id: 5, name: "5 very high" },
    ]);
    listReferenceStates.mockReset().mockResolvedValue([
      { id: 4, name: "open", type_name: "open" },
      { id: 2, name: "closed successful", type_name: "closed" },
      { id: 8, name: "pending reminder", type_name: "pending reminder" },
    ]);
    listArticles.mockReset().mockResolvedValue([
      {
        id: 501,
        ticket_id: 7,
        sender_type: "customer",
        sender_type_id: 3,
        communication_channel_id: 1,
        is_visible_for_customer: true,
        create_time: "2024-06-01T13:00:00Z",
        create_by: 10,
        subject: "Hi",
        from_address: "customer@example.com",
        to_address: "support@example.com",
      },
    ]);
    getReplyDraft.mockResolvedValue({
      to_address: "customer@example.com",
      cc: "",
      subject: "Re: Hi",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
  });

  it("renders the state/priority/queue/owner/customer pills with their values", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    expect(screen.getByTestId("ticket-pill-state")).toHaveTextContent("Open");
    expect(screen.getByTestId("ticket-pill-priority")).toHaveTextContent("normal");
    expect(screen.getByTestId("ticket-pill-queue")).toHaveTextContent("Support");
    expect(screen.getByTestId("ticket-pill-owner")).toHaveTextContent("Ada");
    expect(screen.getByTestId("ticket-pill-customer")).toHaveTextContent("bob");
  });

  it("patches state directly from the status pill's menu", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-state"));
    const item = await screen.findByText("Closed successful");
    fireEvent.click(item);
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { state_id: 2 }));
  });

  it("opens the pending (Warten) dialog from the status pill's menu", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-state"));
    fireEvent.click(await screen.findByText(/Pending/));
    expect(await screen.findByTestId("pending-dialog")).toBeInTheDocument();
  });

  it("opens the queue picker dialog from the queue pill", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-queue"));
    expect(await screen.findByTestId("move-picker-dialog")).toBeInTheDocument();
  });

  it("opens the owner picker dialog from the owner pill, prefilled", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket({ owner_id: 2 })} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-owner"));
    const select = await screen.findByTestId("agent-picker-select");
    expect(select).toHaveValue("2");
  });

  it("opens the customer picker dialog from the customer pill", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-customer"));
    expect(await screen.findByTestId("customer-picker-dialog")).toBeInTheDocument();
  });

  it("opens the reply dialog for the latest article from the Antworten button", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("ticket-actions-reply")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("ticket-actions-reply"));
    expect(await screen.findByTestId("reply-dialog")).toBeInTheDocument();
  });

  it("calls onOpenNote from the Notiz button", () => {
    const onOpenNote = vi.fn();
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={onOpenNote} />);
    fireEvent.click(screen.getByTestId("ticket-actions-note"));
    expect(onOpenNote).toHaveBeenCalledOnce();
  });

  it("lists the Mehr menu's grouped entries and triggers a dialog", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-actions-more"));
    const menu = screen.getByTestId("ticket-actions-more-menu");
    expect(menu).toHaveTextContent("Assignment");
    expect(menu).toHaveTextContent("Organize");
    expect(menu).toHaveTextContent("Other");
    expect(screen.getByTestId("more-link")).toBeInTheDocument();
    expect(screen.getByTestId("more-merge")).toBeInTheDocument();
    expect(screen.getByTestId("more-print")).toBeInTheDocument();
    expect(screen.getByTestId("more-appointment")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("more-link"));
    expect(await screen.findByTestId("link-dialog")).toBeInTheDocument();
  });

  it("toggles watch from the Mehr menu", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket({ is_watched: false })} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-actions-more"));
    fireEvent.click(screen.getByTestId("more-watch"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { watcher_user_id: 42 }));
  });

  it("disables Antworten/Notiz without the note permission", () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote={false} onOpenNote={vi.fn()} />);
    expect(screen.getByTestId("ticket-actions-reply")).toBeDisabled();
    expect(screen.getByTestId("ticket-actions-note")).toBeDisabled();
  });
});
