import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { TicketDetail } from "@/lib/api";
import { useComposerRequests, useConversationViewRequests } from "./telegram/composerBus";
import { TicketHeaderActions } from "./TicketHeaderActions";

const {
  patchTicket,
  listReferencePriorities,
  listReferenceStates,
  listReferenceTypes,
  listReferenceServices,
  listReferenceSlas,
  listArticles,
  getReplyDraft,
  listQueues,
  listReferenceAgents,
  ticketAclFieldOptions,
  getTicketCustomerLink,
  getCustomer,
  phoneConfig,
} = vi.hoisted(() => ({
  patchTicket: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  listReferenceTypes: vi.fn(),
  listReferenceServices: vi.fn(),
  listReferenceSlas: vi.fn(),
  listArticles: vi.fn(),
  getReplyDraft: vi.fn(),
  listQueues: vi.fn(),
  listReferenceAgents: vi.fn(),
  ticketAclFieldOptions: vi.fn(),
  getTicketCustomerLink: vi.fn(),
  getCustomer: vi.fn(),
  phoneConfig: vi.fn(),
}));

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return {
    ...actual,
    phoneApi: {
      phoneConfig,
      screenDynamicFields: vi.fn().mockResolvedValue([]),
      logPhoneCall: vi.fn(),
    },
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      patchTicket,
      listReferencePriorities,
      listReferenceStates,
      listReferenceTypes,
      listReferenceServices,
      listReferenceSlas,
      listArticles,
      getReplyDraft,
      listQueues,
      listReferenceAgents,
      ticketAclFieldOptions,
      getTicketCustomerLink,
      getCustomer,
      listTemplates: vi.fn().mockResolvedValue([]),
      // Queried by the header's @ / ⏱ counters.
      listTicketMentions: vi.fn().mockResolvedValue([]),
      listTicketTimeAccounting: vi.fn().mockResolvedValue([]),
      listTicketLinks: vi.fn().mockResolvedValue([]),
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
    params,
    search,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
    params?: Record<string, string>;
    search?: Record<string, unknown>;
    [k: string]: unknown;
  }) => {
    // Resolve $params into the path and search into a query string, like
    // the real Link does, so hrefs can be asserted.
    let href = typeof to === "string" ? to : "#";
    for (const [k, v] of Object.entries(params ?? {})) href = href.replace(`$${k}`, v);
    const qs = new URLSearchParams(
      Object.entries(search ?? {}).map(([k, v]) => [k, String(v)] as [string, string]),
    ).toString();
    return (
      <a href={qs ? `${href}?${qs}` : href} data-params={JSON.stringify(params ?? {})} {...rest}>
        {children}
      </a>
    );
  },
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
    listReferenceTypes.mockReset().mockResolvedValue([{ id: 1, name: "Unclassified" }]);
    listReferenceServices.mockReset().mockResolvedValue([]);
    listReferenceSlas.mockReset().mockResolvedValue([]);
    ticketAclFieldOptions.mockReset().mockResolvedValue({
      state: {},
      priority: {},
      type: {},
      service: {},
      sla: {},
      queue: {},
    });
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
    listQueues.mockReset().mockResolvedValue([]);
    listReferenceAgents
      .mockReset()
      .mockResolvedValue([{ id: 2, login: "ada", full_name: "Ada Lovelace" }]);
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
    getTicketCustomerLink.mockReset().mockResolvedValue({ label: null, url: null });
  });

  it("renders the status bar and the priority/queue/owner/customer values", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    const open = await screen.findByTestId("ticket-status-4");
    expect(open).toHaveTextContent("Open");
    expect(open).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("ticket-pill-priority")).toHaveTextContent("normal");
    expect(screen.getByTestId("ticket-pill-queue")).toHaveTextContent("Support");
    expect(screen.getByTestId("ticket-pill-owner")).toHaveTextContent("Ada");
    expect(screen.getByTestId("ticket-pill-customer")).toHaveTextContent("bob");
  });

  it("hides the type/service/sla pills when the system defines ≤1 of each", async () => {
    // beforeEach already mocks 1 type, 0 services, 0 slas.
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    await screen.findByTestId("ticket-status-4");
    expect(screen.queryByTestId("ticket-pill-type")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ticket-pill-service")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ticket-pill-sla")).not.toBeInTheDocument();
  });

  it("shows the type/service/sla pills once the system defines more than one", async () => {
    listReferenceTypes.mockResolvedValue([
      { id: 1, name: "Unclassified" },
      { id: 2, name: "Incident" },
    ]);
    listReferenceServices.mockResolvedValue([
      { id: 1, name: "Hosting" },
      { id: 2, name: "Consulting" },
    ]);
    listReferenceSlas.mockResolvedValue([
      { id: 1, name: "Gold" },
      { id: 2, name: "Silver" },
    ]);
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    expect(await screen.findByTestId("ticket-pill-type")).toHaveTextContent("Type");
    expect(screen.getByTestId("ticket-pill-service")).toHaveTextContent("Service");
    expect(screen.getByTestId("ticket-pill-sla")).toHaveTextContent("SLA");
  });

  it("closes the ticket from the status bar's Closed menu", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("ticket-status-closed"));
    const item = await screen.findByTestId("ticket-status-close-2");
    expect(item).toHaveTextContent(/closed successful/i);
    fireEvent.click(item);
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { state_id: 2 }));
  });

  it("opens the pending (Warten) dialog from the status bar", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("ticket-status-pending"));
    expect(await screen.findByTestId("pending-dialog")).toBeInTheDocument();
  });

  it("does not patch when the current state's segment is clicked again", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("ticket-status-4"));
    expect(patchTicket).not.toHaveBeenCalled();
  });

  it("links back to the ticket's queue when it was not opened from a list", async () => {
    window.sessionStorage.clear();
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    const back = await screen.findByTestId("ticket-back-link");
    expect(back).toHaveTextContent("Support");
    expect(back.getAttribute("href")).toContain("queue_id=1");
    expect(screen.queryByTestId("ticket-back-nav-pos")).toBeNull();
  });

  it("links back to the originating list with its position and neighbours", async () => {
    window.sessionStorage.setItem(
      "tiqora.ticketNavContext",
      JSON.stringify({
        label: "Meine Tickets",
        to: "/agent/queues",
        search: { view: "mine", owner_id: 2 },
        ids: [5, 7, 9],
      }),
    );
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    const back = await screen.findByTestId("ticket-back-link");
    expect(back).toHaveTextContent("Meine Tickets");
    expect(back.getAttribute("href")).toContain("view=mine");
    expect(screen.getByTestId("ticket-back-nav-pos")).toHaveTextContent("2 / 3");
    expect(screen.getByTestId("ticket-nav-prev").getAttribute("href")).toContain("/agent/tickets/5");
    expect(screen.getByTestId("ticket-nav-next").getAttribute("href")).toContain("/agent/tickets/9");
    window.sessionStorage.clear();
  });

  it("places the AI pieces: summary under the title, drafts on the reply button, banners", async () => {
    wrap(
      <TicketHeaderActions
        ticket={makeTicket()}
        canNote
        onOpenNote={vi.fn()}
        ai={{
          summaryLine: <p data-testid="ai-line">Summary</p>,
          summaryPanel: null,
          draftsButton: <button data-testid="ai-drafts">✎ 1</button>,
          banners: <div data-testid="ai-banner">Triage</div>,
          overlays: null,
        }}
      />,
    );
    const title = await screen.findByRole("heading", { level: 1 });
    expect(title.parentElement).toContainElement(screen.getByTestId("ai-line"));
    expect(screen.getByTestId("ticket-actions-reply").parentElement).toContainElement(
      screen.getByTestId("ai-drafts"),
    );
    expect(screen.getByTestId("ai-banner")).toBeInTheDocument();
  });

  it("opens the queue pill's listbox and patches queue_id on selection", async () => {
    listQueues.mockResolvedValue([{ id: 9, name: "Sales", group_id: 1, valid: true }]);
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-queue"));
    fireEvent.click(await screen.findByText("Sales"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { queue_id: 9 }));
  });

  it("opens the owner pill's listbox and patches owner_id on selection", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-owner"));
    fireEvent.click(await screen.findByText("Ada Lovelace"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { owner_id: 2 }));
  });

  it("shows a Responsible pill next to Owner and patches responsible_id on selection", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    expect(screen.getByTestId("ticket-pill-responsible")).toHaveTextContent("—");
    fireEvent.click(screen.getByTestId("ticket-pill-responsible"));
    fireEvent.click(await screen.findByText("Ada Lovelace"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { responsible_id: 2 }));
  });

  it("shows a search field in the owner listbox once there are more than 8 agents", async () => {
    listReferenceAgents.mockResolvedValue(
      Array.from({ length: 9 }, (_, i) => ({ id: i + 1, login: `agent${i}`, full_name: `Agent ${i}` })),
    );
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ticket-pill-owner"));
    expect(await screen.findByTestId("ticket-pill-owner-menu-search")).toBeInTheDocument();
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

  it("routes 'Antworten' to the chat composer for a Telegram ticket, switching from split view first", async () => {
    listArticles.mockResolvedValue([
      {
        id: 501,
        ticket_id: 7,
        sender_type: "customer",
        sender_type_id: 3,
        communication_channel_id: 42,
        communication_channel_name: "Telegram",
        is_visible_for_customer: true,
        create_time: "2024-06-01T13:00:00Z",
        create_by: 10,
        subject: "Hi",
        from_address: null,
        to_address: null,
      },
    ]);

    const viewHandler = vi.fn();
    const composerHandler = vi.fn();
    function Listener() {
      useConversationViewRequests(7, viewHandler);
      useComposerRequests(7, composerHandler);
      return null;
    }

    wrap(
      <>
        <Listener />
        <TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("ticket-actions-reply")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("ticket-actions-reply"));

    expect(viewHandler).toHaveBeenCalledOnce();
    expect(composerHandler).toHaveBeenCalledWith({ focus: true });
    expect(screen.queryByTestId("reply-dialog")).not.toBeInTheDocument();
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
    expect(await screen.findByTestId("more-link")).toBeInTheDocument();
    const menu = screen.getByTestId("ticket-actions-more-menu");
    expect(menu).toHaveTextContent("Assignment");
    expect(menu).toHaveTextContent("Organize");
    expect(menu).toHaveTextContent("Other");
    // Closing moved to the status bar's Closed segment.
    expect(screen.queryByTestId("more-close-2")).toBeNull();
    expect(screen.getByTestId("more-link")).toBeInTheDocument();
    expect(screen.getByTestId("more-merge")).toBeInTheDocument();
    expect(screen.getByTestId("more-print")).toBeInTheDocument();
    expect(screen.getByTestId("more-appointment")).toBeInTheDocument();
    // Verantwortlicher moved out into its own pill (see the Responsible-pill test).
    expect(screen.queryByTestId("more-responsible")).not.toBeInTheDocument();

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

  it("shows the internal Kunde button once the customer login resolves to an email", async () => {
    wrap(
      <TicketHeaderActions
        ticket={makeTicket({ customer_email: "bob@example.com" })}
        canNote
        onOpenNote={vi.fn()}
      />,
    );
    const link = await screen.findByTestId("ticket-customer-centre-link");
    expect(link).toHaveAttribute("href", "/agent/customers/bob");
    expect(link).toHaveAttribute("data-params", JSON.stringify({ login: "bob" }));
  });

  it("shows no external customer-link button when the endpoint returns url: null", async () => {
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    await waitFor(() => expect(getTicketCustomerLink).toHaveBeenCalledWith(7, expect.anything()));
    expect(screen.queryByTestId("ticket-customer-external-link")).not.toBeInTheDocument();
  });

  it("shows the external customer-link button with its configured label, opening in a new tab", async () => {
    getTicketCustomerLink.mockResolvedValue({
      label: "Diagnose",
      url: "https://netadmin.example/?u=bob",
    });
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    const link = await screen.findByTestId("ticket-customer-external-link");
    expect(link).toHaveTextContent("Diagnose");
    expect(link).toHaveAttribute("href", "https://netadmin.example/?u=bob");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("falls back to the default 'Customer data' label when the config has no label", async () => {
    getTicketCustomerLink.mockResolvedValue({
      label: null,
      url: "https://netadmin.example/?u=bob",
    });
    wrap(<TicketHeaderActions ticket={makeTicket()} canNote onOpenNote={vi.fn()} />);
    const link = await screen.findByTestId("ticket-customer-external-link");
    expect(link).toHaveTextContent(/Customer data|Kundendaten/);
  });

  it("offers inbound/outbound calls and dials the customer's numbers", async () => {
    getCustomer.mockResolvedValue({
      login: "bob",
      email: "bob@example.com",
      customer_id: "C-9",
      first_name: "Bob",
      last_name: "B",
      phone: "+49 228 555-0101",
      mobile: "0171 1234567",
    });
    phoneConfig.mockResolvedValue({ dial_scheme: "sip" });
    wrap(
      <TicketHeaderActions
        ticket={makeTicket({ customer_email: "bob@example.com" })}
        canNote
        onOpenNote={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("ticket-actions-phone"));
    expect(await screen.findByTestId("phone-menu-inbound")).toBeInTheDocument();
    const dial = await screen.findByTestId("phone-dial-mobile");
    expect(dial).toHaveAttribute("href", "sip:01711234567");
    expect(screen.getByTestId("phone-dial-phone")).toHaveAttribute("href", "sip:+492285550101");

    fireEvent.click(screen.getByTestId("phone-menu-inbound"));
    expect(await screen.findByTestId("phone-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("phone-direction-inbound")).toHaveAttribute("aria-pressed", "true");
  });

  it("hides the call button without write permission", async () => {
    wrap(
      <TicketHeaderActions
        ticket={makeTicket({ permissions: { ...ALL_PERMS, rw: false } })}
        canNote
        onOpenNote={vi.fn()}
      />,
    );
    await screen.findByTestId("ticket-actions-reply");
    expect(screen.queryByTestId("ticket-actions-phone")).toBeNull();
  });
});
