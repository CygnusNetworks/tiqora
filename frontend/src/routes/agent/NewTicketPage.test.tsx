import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { NewTicketPage } from "./NewTicketPage";

const {
  navigate,
  listQueues,
  listReferencePriorities,
  listReferenceStates,
  searchReferenceCustomers,
  getComposeContext,
  createTicket,
  createArticle,
  refine,
  refineAvailability,
  searchParams,
  callerLookup,
  getCustomer,
  suggestedQueue,
  defaultQueue,
  screenDynamicFields,
} = vi.hoisted(() => ({
  navigate: vi.fn(),
  listQueues: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  searchReferenceCustomers: vi.fn(),
  getComposeContext: vi.fn(),
  createTicket: vi.fn(),
  createArticle: vi.fn(),
  refine: vi.fn(),
  refineAvailability: vi.fn(),
  searchParams: { current: {} as Record<string, unknown> },
  callerLookup: vi.fn(),
  getCustomer: vi.fn(),
  suggestedQueue: vi.fn(),
  defaultQueue: vi.fn(),
  screenDynamicFields: vi.fn(),
}));

vi.mock("@/lib/newTicketApi", () => ({
  newTicketApi: { suggestedQueue, defaultQueue },
}));

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return {
    ...actual,
    phoneApi: { callerLookup, screenDynamicFields },
  };
});

vi.mock("@/lib/refineApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/refineApi")>("@/lib/refineApi");
  return { ...actual, refineApi: { refine, refineAvailability } };
});

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams.current,
  Link: ({
    children,
    to,
    params,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
  } & Record<string, unknown>) => (
    <a
      href={`${to}${params ? `/${Object.values(params).join("/")}` : ""}`}
      {...rest}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 5,
      login: "agent1",
      first_name: "Agent",
      last_name: "One",
      email: "agent1@example.com",
      is_admin: false,
    },
  }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listQueues,
      listReferencePriorities,
      listReferenceStates,
      searchReferenceCustomers,
      getComposeContext,
      createTicket,
      createArticle,
      getCustomer,
      listReferenceAgents: vi.fn().mockResolvedValue([
        { id: 5, login: "agent1", full_name: "Agent One" },
        { id: 7, login: "jwolff", full_name: "Jana Wolff" },
      ]),
      listReferenceTypes: vi.fn().mockResolvedValue([]),
      listReferenceServices: vi.fn().mockResolvedValue([]),
      listReferenceSlas: vi.fn().mockResolvedValue([]),
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

const queue = { id: 1, name: "Support", group_id: 1, valid: true };
const priorities = [{ id: 3, name: "3 normal" }];
const states = [{ id: 4, name: "open", type_name: "open" }];
const composeContext = {
  from_address: "Support <support@example.com>",
  signature: "",
  signature_is_html: false,
  rich_text: false,
};
const customer = {
  login: "jane.doe",
  email: "jane@example.com",
  customer_id: "CUST1",
  full_name: "Jane Doe",
};

async function renderReady() {
  const utils = wrap(<NewTicketPage />);
  await waitFor(() =>
    expect(screen.getByTestId("new-ticket-type-email")).toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(
      screen.getByTestId("new-ticket-customer-search"),
    ).toBeInTheDocument(),
  );
  return utils;
}

async function pickCustomer() {
  fireEvent.change(screen.getByTestId("new-ticket-customer-search"), {
    target: { value: "jane" },
  });
  await waitFor(() =>
    expect(searchReferenceCustomers).toHaveBeenCalledWith(
      expect.objectContaining({ q: "jane" }),
    ),
  );
  const result = await screen.findByTestId(
    "new-ticket-customer-result-jane.doe",
  );
  fireEvent.click(result);
}

describe("NewTicketPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    listQueues.mockReset().mockResolvedValue([queue]);
    listReferencePriorities.mockReset().mockResolvedValue(priorities);
    listReferenceStates.mockReset().mockResolvedValue(states);
    searchReferenceCustomers.mockReset().mockResolvedValue([customer]);
    getComposeContext.mockReset().mockResolvedValue(composeContext);
    createTicket.mockReset().mockResolvedValue({ ticket_id: 42 });
    createArticle.mockReset().mockResolvedValue({ article_id: 1 });
    searchParams.current = {};
    callerLookup.mockReset().mockResolvedValue({ number_normalized: "", customers: [], open_tickets: [] });
    getCustomer.mockReset();
    defaultQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "fallback" });
    suggestedQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "default" });
    screenDynamicFields.mockReset().mockResolvedValue([]);
    window.localStorage.clear();
  });

  it("defaults to email mode and seeds the To chip + customer card on selection", async () => {
    await renderReady();
    expect(screen.getByTestId("new-ticket-type-email")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await pickCustomer();

    expect(screen.getByTestId("new-ticket-customer-card")).toHaveTextContent(
      "Jane Doe",
    );
    expect(screen.getByTestId("new-ticket-to")).toHaveTextContent("Jane Doe");
  });

  it("keeps submit disabled until To/subject/body are filled, then enables it and creates the ticket + article", async () => {
    await renderReady();
    await pickCustomer();
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-submit")).toBeDisabled(),
    );

    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Question about invoice" },
    });
    expect(screen.getByTestId("new-ticket-submit")).toBeDisabled();

    fireEvent.change(screen.getByTestId("new-ticket-body"), {
      target: { value: "Please advise." },
    });
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled(),
    );

    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() => expect(createTicket).toHaveBeenCalledTimes(1));
    expect(createTicket).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Question about invoice",
        queue_id: 1,
        customer_user_id: "jane.doe",
      }),
    );
    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(1));
    expect(createArticle).toHaveBeenCalledWith(
      42,
      expect.objectContaining({
        channel: "email",
        sender_type: "agent",
        to_address: "Jane Doe <jane@example.com>",
        content_type: "text/plain; charset=utf-8",
      }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/tickets/$ticketId",
        params: { ticketId: "42" },
      }),
    );
  });

  it("phone mode creates ticket + call in one request with direction, time and auto reply", async () => {
    await renderReady();
    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    fireEvent.click(screen.getByTestId("new-ticket-skip-customer"));

    expect(screen.queryByTestId("new-ticket-to")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-ticket-from")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-direction-in")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("new-ticket-timer")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("new-ticket-direction-out"));
    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Called about delivery" },
    });
    fireEvent.change(screen.getByTestId("new-ticket-body"), {
      target: { value: "Customer called back." },
    });
    fireEvent.change(screen.getByTestId("new-ticket-time"), { target: { value: "12" } });
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() => expect(createTicket).toHaveBeenCalledTimes(1));
    expect(createArticle).not.toHaveBeenCalled();
    expect(createTicket).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Called about delivery",
        owner_id: 5,
        send_auto_response: false,
        phone_call: expect.objectContaining({
          direction: "outbound",
          body: "Customer called back.",
          time_unit: 12,
        }),
      }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/tickets/$ticketId",
        params: { ticketId: "42" },
      }),
    );
  });

  it("an inbound phone ticket asks for the auto reply; a pending state needs a time", async () => {
    listReferenceStates.mockResolvedValue([
      ...states,
      { id: 6, name: "pending reminder", type_name: "pending reminder" },
    ]);
    await renderReady();
    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    fireEvent.click(screen.getByTestId("new-ticket-skip-customer"));
    fireEvent.change(screen.getByTestId("new-ticket-subject"), { target: { value: "Rückruf" } });
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "Bitte zurückrufen" } });

    fireEvent.click(screen.getByTestId("new-ticket-state"));
    fireEvent.click(await screen.findByText(/pending reminder|Warten zur Erinnerung|Erinnerung/i));
    await screen.findByTestId("new-ticket-pending");
    expect(screen.getByTestId("new-ticket-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("new-ticket-pending-time-preset-tomorrow9"));
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() => expect(createTicket).toHaveBeenCalledTimes(1));
    const body = createTicket.mock.calls[0][0];
    expect(body.state_id).toBe(6);
    expect(new Date(body.pending_time).getHours()).toBe(9);
    expect(body.send_auto_response).toBe(true);
    expect(body.phone_call.direction).toBe("inbound");
  });

  it("looks up the caller number, picks the customer and hands a call over to an open ticket", async () => {
    callerLookup.mockResolvedValue({
      number_normalized: "492285550101",
      customers: [
        {
          login: "jane.doe",
          customer_id: "CUST1",
          name: "Jane Doe",
          email: "jane@example.com",
          phone: "+49 228 555-0101",
          mobile: null,
          company: "Acme",
        },
      ],
      open_tickets: [
        {
          id: 77,
          tn: "2026092900077",
          title: "Drucker",
          state: "open",
          queue: "Support",
          customer_user_id: "jane.doe",
          changed: "2026-09-29T10:00:00Z",
        },
      ],
    });
    await renderReady();
    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    fireEvent.change(screen.getByTestId("caller-number"), { target: { value: "+49 228 5550101" } });
    await waitFor(() => expect(callerLookup).toHaveBeenCalledWith("+49 228 5550101", expect.anything()));
    fireEvent.click(await screen.findByTestId("caller-pick-jane.doe"));
    expect(await screen.findByTestId("new-ticket-customer-card")).toHaveTextContent("Jane Doe");
    // The open tickets stay once the customer is chosen.
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "schon wieder" } });
    fireEvent.click(screen.getByTestId("caller-log-77"));
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/$ticketId",
      params: { ticketId: "77" },
    });
    const { loadPhoneDraft, peekPhoneCallRequest } = await import("@/lib/phoneCall");
    expect(loadPhoneDraft(77)?.body).toBe("schon wieder");
    expect(peekPhoneCallRequest(77)).toEqual({ direction: "inbound", number: "+49 228 5550101" });
    expect(createTicket).not.toHaveBeenCalled();
  });

  it("prefills an outbound phone ticket from the search params (click-to-call)", async () => {
    searchParams.current = { type: "phone", direction: "outbound", customer: "jane.doe", number: "0228 1" };
    getCustomer.mockResolvedValue({
      login: "jane.doe",
      email: "jane@example.com",
      customer_id: "CUST1",
      first_name: "Jane",
      last_name: "Doe",
    });
    wrap(<NewTicketPage />);
    expect(await screen.findByTestId("new-ticket-customer-card")).toHaveTextContent("Jane Doe");
    expect(screen.getByTestId("new-ticket-type-phone")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("new-ticket-direction-out")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("caller-number")).toHaveValue("0228 1");
  });

  it("seeds the call timer from the CTI popup's call times", async () => {
    const start = Date.now() - 120_000;
    searchParams.current = { type: "phone", number: "0228 1", call_started: start, call_ended: start + 75_000 };
    wrap(<NewTicketPage />);
    expect(await screen.findByTestId("new-ticket-timer")).toHaveTextContent("01:15");
  });

  it("renders a plain textarea when rich_text is false and the ComposerBody toolbar when true", async () => {
    getComposeContext.mockResolvedValue({ ...composeContext, rich_text: true });
    await renderReady();
    await pickCustomer();
    await waitFor(() => expect(getComposeContext).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-body-toolbar")).toBeInTheDocument(),
    );
  });

  it("shows sendError and does not navigate when the article creation returns 502", async () => {
    createArticle.mockRejectedValue(
      new ApiError(
        502,
        "Outbound email delivery failed: SMTP refused",
        "/api/v1/tickets/42/articles",
      ),
    );
    await renderReady();
    await pickCustomer();
    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Question" },
    });
    fireEvent.change(screen.getByTestId("new-ticket-body"), {
      target: { value: "Body text" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("new-ticket-error")).toHaveTextContent(
      /could not be sent/,
    );
    expect(screen.getByText("Go to ticket")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("NewTicketPage property bar and queue default", () => {
  const junk = { id: 9, name: "Junk", group_id: 1, valid: true };
  const hotline = { id: 2, name: "Level1::Hotline", group_id: 1, valid: true };
  const technik = { id: 3, name: "Level1::Technik", group_id: 1, valid: true };
  const other = {
    login: "max.muster",
    email: "max@example.com",
    customer_id: "CUST2",
    full_name: "Max Muster",
  };

  beforeEach(() => {
    navigate.mockReset();
    listQueues.mockReset().mockResolvedValue([junk, queue, hotline, technik]);
    listReferencePriorities.mockReset().mockResolvedValue(priorities);
    listReferenceStates.mockReset().mockResolvedValue(states);
    searchReferenceCustomers.mockReset().mockResolvedValue([customer, other]);
    getComposeContext.mockReset().mockResolvedValue(composeContext);
    createTicket.mockReset().mockResolvedValue({ ticket_id: 42 });
    createArticle.mockReset().mockResolvedValue({ article_id: 1 });
    searchParams.current = {};
    callerLookup.mockReset().mockResolvedValue({ number_normalized: "", customers: [], open_tickets: [] });
    getCustomer.mockReset();
    defaultQueue.mockReset().mockResolvedValue({ queue_id: 2, source: "default" });
    screenDynamicFields.mockReset().mockResolvedValue([]);
    suggestedQueue.mockReset().mockImplementation((login: string) =>
      Promise.resolve(
        login === "jane.doe"
          ? { queue_id: 3, source: "customer" }
          : { queue_id: 1, source: "company" },
      ),
    );
  });

  const queueValue = () => screen.getByTestId("new-ticket-queue-value");

  async function pick(login: string) {
    fireEvent.change(screen.getByTestId("new-ticket-customer-search"), {
      target: { value: login.slice(0, 3) },
    });
    fireEvent.click(await screen.findByTestId(`new-ticket-customer-result-${login}`));
  }

  it("starts in the configured default queue, not the first (Junk) one, and shows no source", async () => {
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Hotline"));
    expect(defaultQueue).toHaveBeenCalledWith("email", expect.anything());
    expect(screen.queryByTestId("new-ticket-queue-source")).not.toBeInTheDocument();
    expect(queueValue()).not.toHaveTextContent("Junk");
  });

  it("never falls back to Junk when the suggestion endpoint fails", async () => {
    defaultQueue.mockRejectedValue(new ApiError(500, "boom", "/x"));
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Support"));
  });

  it("never falls back to Junk when no queue is suggested", async () => {
    defaultQueue.mockResolvedValue({ queue_id: null, source: null });
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Support"));
  });

  it("takes the customer's queue once a customer is set and says why", async () => {
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Hotline"));
    await pick("jane.doe");
    await waitFor(() => expect(queueValue()).toHaveTextContent("Technik"));
    expect(suggestedQueue).toHaveBeenCalledWith("jane.doe", "email", expect.anything());
    expect(screen.getByTestId("new-ticket-queue-source")).toHaveTextContent(
      "like Jane Doe's last ticket",
    );
  });

  it("names the company as source when the queue comes from the company's last ticket", async () => {
    await renderReady();
    await pick("max.muster");
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-queue-source")).toHaveTextContent(
        "like the company's last ticket",
      ),
    );
    expect(queueValue()).toHaveTextContent("Support");
  });

  it("keeps a queue the agent picked by hand when the customer changes", async () => {
    await renderReady();
    await pick("jane.doe");
    await waitFor(() => expect(queueValue()).toHaveTextContent("Technik"));

    fireEvent.click(screen.getByTestId("new-ticket-queue"));
    fireEvent.click(await screen.findByTestId("new-ticket-queue-panel-option-2"));
    expect(queueValue()).toHaveTextContent("Hotline");
    expect(screen.queryByTestId("new-ticket-queue-source")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("new-ticket-customer-clear"));
    await pick("max.muster");
    expect(await screen.findByTestId("new-ticket-customer-card")).toHaveTextContent("Max Muster");
    // Give a (wrong) re-suggestion the chance to land.
    await new Promise((r) => setTimeout(r, 20));
    expect(suggestedQueue).not.toHaveBeenCalledWith("max.muster", expect.anything(), expect.anything());
    expect(queueValue()).toHaveTextContent("Hotline");
  });

  it("the queue_id param wins over every suggestion", async () => {
    searchParams.current = { queue_id: 1 };
    await renderReady();
    await pick("jane.doe");
    expect(await screen.findByTestId("new-ticket-customer-card")).toBeInTheDocument();
    expect(queueValue()).toHaveTextContent("Support");
    expect(defaultQueue).not.toHaveBeenCalled();
    expect(suggestedQueue).not.toHaveBeenCalled();
  });

  it("phone mode asks the phone screen and shows Queue, Owner, Priority, State; e-mail shows three", async () => {
    await renderReady();
    await pick("jane.doe");
    expect(screen.getByTestId("new-ticket-queue")).toBeInTheDocument();
    expect(screen.queryByTestId("new-ticket-owner")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-priority-value")).toHaveTextContent("normal");
    expect(screen.getByTestId("new-ticket-priority-value")).not.toHaveTextContent("3");

    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    await waitFor(() =>
      expect(suggestedQueue).toHaveBeenCalledWith("jane.doe", "phone", expect.anything()),
    );
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-owner-value")).toHaveTextContent("Agent One"),
    );
    // The owner lives in the bar only.
    expect(screen.getAllByTestId("new-ticket-owner")).toHaveLength(1);
  });

  it("the submit button says 'Log and close' for a closed state on a phone ticket", async () => {
    listReferenceStates.mockResolvedValue([
      ...states,
      { id: 2, name: "closed successful", type_name: "closed" },
    ]);
    await renderReady();
    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    fireEvent.click(screen.getByTestId("new-ticket-skip-customer"));
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Create ticket");

    fireEvent.click(screen.getByTestId("new-ticket-state"));
    fireEvent.click(await screen.findByTestId("new-ticket-state-panel-option-2"));
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Log and close");

    fireEvent.click(screen.getByTestId("new-ticket-state"));
    fireEvent.click(await screen.findByTestId("new-ticket-state-panel-option-4"));
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Create ticket");
  });

  it("a slow suggestion that lands after the agent picked a queue is not applied", async () => {
    let resolveJane: (v: unknown) => void = () => {};
    suggestedQueue.mockImplementation(
      () => new Promise((r) => {
        resolveJane = r;
      }),
    );
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Hotline"));
    await pick("jane.doe");
    await waitFor(() =>
      expect(suggestedQueue).toHaveBeenCalledWith("jane.doe", "email", expect.anything()),
    );

    fireEvent.click(screen.getByTestId("new-ticket-queue"));
    fireEvent.click(await screen.findByTestId("new-ticket-queue-panel-option-1"));
    expect(queueValue()).toHaveTextContent("Support");

    await act(async () => {
      resolveJane({ queue_id: 3, source: "customer" });
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(queueValue()).toHaveTextContent("Support");
    expect(screen.queryByTestId("new-ticket-queue-source")).not.toBeInTheDocument();
  });

  it("a slow answer for the previous customer is not applied after switching customers", async () => {
    const pending: Record<string, (v: unknown) => void> = {};
    suggestedQueue.mockImplementation(
      (login: string) => new Promise((r) => {
        pending[login] = r;
      }),
    );
    await renderReady();
    await waitFor(() => expect(queueValue()).toHaveTextContent("Hotline"));
    await pick("jane.doe");
    await waitFor(() => expect(pending["jane.doe"]).toBeDefined());
    fireEvent.click(screen.getByTestId("new-ticket-customer-clear"));
    await pick("max.muster");
    await waitFor(() => expect(pending["max.muster"]).toBeDefined());

    // Jane's answer arrives late: Max is the customer now.
    await act(async () => {
      pending["jane.doe"]({ queue_id: 3, source: "customer" });
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(queueValue()).not.toHaveTextContent("Technik");
    expect(screen.queryByTestId("new-ticket-queue-source")).not.toBeInTheDocument();

    await act(async () => {
      pending["max.muster"]({ queue_id: 1, source: "company" });
    });
    await waitFor(() => expect(queueValue()).toHaveTextContent("Support"));
    expect(screen.getByTestId("new-ticket-queue-source")).toHaveTextContent(
      "like the company's last ticket",
    );
  });
});

describe("NewTicketPage compact phone ticket", () => {
  const closedStates = [
    ...states,
    { id: 10, name: "closed unsuccessful", type_name: "closed" },
    { id: 11, name: "closed successful", type_name: "closed" },
  ];
  const popupSearch = (extra: Record<string, unknown> = {}) => {
    const start = Date.now() - 200_000;
    return {
      type: "phone",
      from_call: true,
      direction: "inbound",
      number: "+49 228 1234",
      call_started: start,
      call_ended: start + 192_000,
      ...extra,
    };
  };

  beforeEach(() => {
    navigate.mockReset();
    listQueues.mockReset().mockResolvedValue([queue]);
    listReferencePriorities.mockReset().mockResolvedValue(priorities);
    listReferenceStates.mockReset().mockResolvedValue(closedStates);
    searchReferenceCustomers.mockReset().mockResolvedValue([customer]);
    getComposeContext.mockReset().mockResolvedValue(composeContext);
    createTicket.mockReset().mockResolvedValue({ ticket_id: 42 });
    createArticle.mockReset().mockResolvedValue({ article_id: 1 });
    callerLookup.mockReset().mockResolvedValue({ number_normalized: "", customers: [], open_tickets: [] });
    getCustomer.mockReset();
    defaultQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "fallback" });
    suggestedQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "default" });
    screenDynamicFields.mockReset().mockResolvedValue([]);
    refine.mockReset();
    refineAvailability.mockReset().mockResolvedValue({ available: false });
    searchParams.current = {};
    window.localStorage.clear();
  });

  it("from the call popup: direction badge, owner who answered, closed-successful status, timer in the strip", async () => {
    searchParams.current = popupSearch({ owner_id: 7 });
    wrap(<NewTicketPage />);
    const strip = await screen.findByTestId("phone-call-strip");
    expect(within(strip).getByTestId("new-ticket-direction-badge")).toHaveTextContent("Incoming call");
    expect(screen.queryByTestId("new-ticket-direction-in")).not.toBeInTheDocument();
    expect(within(strip).getByTestId("new-ticket-autoreply-hint")).toHaveTextContent(
      "customer gets the acknowledgement",
    );
    expect(within(strip).getByTestId("new-ticket-timer")).toHaveTextContent("03:12");
    expect(within(strip).getByTestId("phone-call-strip-number")).toHaveTextContent("+49 228 1234");

    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-owner-value")).toHaveTextContent("Jana Wolff"),
    );
    expect(screen.getByTestId("new-ticket-owner-source")).toHaveTextContent("answered the call");
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent(/successful/i),
    );
    expect(screen.getByTestId("new-ticket-state-value")).not.toHaveTextContent(/unsuccessful/i);
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Log and close");

    // Another owner picked by hand: the source line goes.
    fireEvent.click(screen.getByTestId("new-ticket-owner"));
    fireEvent.click(await screen.findByTestId("new-ticket-owner-panel-option-5"));
    expect(screen.getByTestId("new-ticket-owner-value")).toHaveTextContent("Agent One");
    expect(screen.queryByTestId("new-ticket-owner-source")).not.toBeInTheDocument();
  });

  it("submits the owner from the call and the closed state", async () => {
    searchParams.current = popupSearch({ owner_id: 7 });
    wrap(<NewTicketPage />);
    fireEvent.click(await screen.findByTestId("new-ticket-skip-customer"));
    fireEvent.change(screen.getByTestId("new-ticket-subject"), { target: { value: "Router" } });
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "Neu gestartet." } });
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("new-ticket-submit"));
    await waitFor(() => expect(createTicket).toHaveBeenCalledTimes(1));
    expect(createTicket.mock.calls[0][0]).toMatchObject({ owner_id: 7, state_id: 11 });
  });

  it("opened by hand: toggle, the current user as owner without source, open status", async () => {
    searchParams.current = { type: "phone" };
    wrap(<NewTicketPage />);
    const strip = await screen.findByTestId("phone-call-strip");
    expect(within(strip).getByTestId("new-ticket-direction-in")).toHaveAttribute("aria-pressed", "true");
    expect(within(strip).queryByTestId("new-ticket-direction-badge")).not.toBeInTheDocument();
    expect(within(strip).getByTestId("new-ticket-timer")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-owner-value")).toHaveTextContent("Agent One"),
    );
    expect(screen.queryByTestId("new-ticket-owner-source")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent(/open/i));
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Create ticket");
  });

  it("a missed call (ended, never answered) keeps the normal status", async () => {
    searchParams.current = popupSearch({ call_started: undefined });
    wrap(<NewTicketPage />);
    await screen.findByTestId("new-ticket-direction-badge");
    await waitFor(() => expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent(/open/i));
    expect(screen.getByTestId("new-ticket-submit")).toHaveTextContent("Create ticket");
  });

  it("a ringing call from the popup (no times yet) still shows the direction badge", async () => {
    searchParams.current = { type: "phone", from_call: true, direction: "outbound", number: "0228 1" };
    wrap(<NewTicketPage />);
    expect(await screen.findByTestId("new-ticket-direction-badge")).toHaveTextContent("Outgoing call");
    expect(screen.queryByTestId("new-ticket-direction-in")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent(/open/i));
  });

  it("click-to-call from the customer page (direction without popup marker) shows the toggle", async () => {
    searchParams.current = { type: "phone", direction: "outbound", number: "0228 1" };
    wrap(<NewTicketPage />);
    expect(await screen.findByTestId("new-ticket-direction-out")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("new-ticket-direction-badge")).not.toBeInTheDocument();
  });

  it("without a hang-up (call still running) the status stays open", async () => {
    searchParams.current = popupSearch({ call_ended: undefined });
    wrap(<NewTicketPage />);
    await screen.findByTestId("new-ticket-direction-badge");
    await waitFor(() => expect(screen.getByTestId("new-ticket-state-value")).toHaveTextContent(/open/i));
  });

  it("keeps rare fields collapsed behind a summary; a required field stays visible and blocks submit", async () => {
    screenDynamicFields.mockResolvedValue([
      { name: "Building", label: "Building", field_type: "Text", required: true, possible_values: null },
      { name: "Room", label: "Room", field_type: "Text", required: false, possible_values: null },
    ]);
    searchParams.current = { type: "phone" };
    wrap(<NewTicketPage />);
    fireEvent.click(await screen.findByTestId("new-ticket-skip-customer"));

    const toggle = screen.getByTestId("new-ticket-more-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("More fields");
    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-more-summary")).toHaveTextContent("Responsible, 1 extra field"),
    );
    expect(screen.queryByTestId("new-ticket-responsible")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-ticket-df-Room")).not.toBeInTheDocument();
    // The required field is outside the collapsed section.
    const required = screen.getByTestId("new-ticket-df-required-Building");
    expect(screen.getByTestId("new-ticket-more-toggle").parentElement).not.toContainElement(required);

    fireEvent.change(screen.getByTestId("new-ticket-subject"), { target: { value: "Kein Netz" } });
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "Router aus." } });
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.getByTestId("new-ticket-submit")).toBeDisabled();

    fireEvent.change(required, { target: { value: "Haus 3" } });
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("new-ticket-responsible")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("new-ticket-df-Room"), { target: { value: "214" } });

    fireEvent.click(screen.getByTestId("new-ticket-submit"));
    await waitFor(() => expect(createTicket).toHaveBeenCalledTimes(1));
    expect(createTicket.mock.calls[0][0].dynamic_fields).toEqual({ Building: ["Haus 3"], Room: ["214"] });
  });

  it("the pending time sits directly under the bar, outside the collapsed section", async () => {
    listReferenceStates.mockResolvedValue([
      ...states,
      { id: 6, name: "pending reminder", type_name: "pending reminder" },
    ]);
    searchParams.current = { type: "phone" };
    wrap(<NewTicketPage />);
    fireEvent.click(await screen.findByTestId("new-ticket-skip-customer"));
    fireEvent.click(screen.getByTestId("new-ticket-state"));
    fireEvent.click(await screen.findByTestId("new-ticket-state-panel-option-6"));
    const pending = await screen.findByTestId("new-ticket-pending");
    expect(screen.getByTestId("phone-ticket-fields")).not.toContainElement(pending);
    expect(screen.getByTestId("new-ticket-more-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("the note's footer holds refine, file and time accounting", async () => {
    refineAvailability.mockResolvedValue({ available: true });
    searchParams.current = { type: "phone" };
    wrap(<NewTicketPage />);
    fireEvent.click(await screen.findByTestId("new-ticket-skip-customer"));
    const footer = screen.getByTestId("new-ticket-note-footer");
    expect(await within(footer).findByTestId("new-ticket-refine-button")).toHaveTextContent(
      "Structure note",
    );
    expect(within(footer).getByTestId("new-ticket-attach-input")).toBeInTheDocument();
    expect(within(footer).getByTestId("new-ticket-time")).toBeInTheDocument();
    // The timer is not in the footer any more — it lives in the call strip.
    expect(within(footer).queryByTestId("new-ticket-timer")).not.toBeInTheDocument();
  });
});

describe("NewTicketPage refine", () => {
  beforeEach(() => {
    navigate.mockReset();
    listQueues.mockReset().mockResolvedValue([queue]);
    listReferencePriorities.mockReset().mockResolvedValue(priorities);
    listReferenceStates.mockReset().mockResolvedValue(states);
    searchReferenceCustomers.mockReset().mockResolvedValue([customer]);
    getComposeContext.mockReset().mockResolvedValue(composeContext);
    defaultQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "fallback" });
    suggestedQueue.mockReset().mockResolvedValue({ queue_id: 1, source: "default" });
    screenDynamicFields.mockReset().mockResolvedValue([]);
    refine.mockReset();
    refineAvailability.mockReset().mockResolvedValue({ available: true });
    searchParams.current = {};
  });

  it("rewrites the body and addresses the request by the picked queue", async () => {
    refine.mockResolvedValue({
      sections: [{ id: 0, text: "Der Kunde meldet eine Stoerung." }],
    });
    await renderReady();
    // The form stays locked (disabled fieldset) until a customer is picked.
    await pickCustomer();

    const body = () =>
      screen.getByTestId("new-ticket-body") as HTMLTextAreaElement;
    fireEvent.change(body(), { target: { value: "kunde meldet stoerung" } });

    await waitFor(() =>
      expect(screen.getByTestId("new-ticket-refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("new-ticket-refine-button"));

    fireEvent.click(await screen.findByTestId("refine-review-accept"));
    await waitFor(() =>
      expect(body().value).toBe("Der Kunde meldet eine Stoerung."),
    );
    expect(refine.mock.calls[0][0].queue_id).toBe(queue.id);
    expect(refine.mock.calls[0][0].ticket_id).toBeUndefined();
    // Without a ticket the backend has no source for name masking, so the
    // composer names the customer it was opened for.
    expect(refine.mock.calls[0][0].customer_user_id).toBe(customer.login);
  });

  it("submitting while the review is open sends the accepted selection", async () => {
    createArticle.mockClear();
    refine.mockResolvedValue({ sections: [{ id: 0, text: "EINS zwei DREI" }] });
    await renderReady();
    await pickCustomer();
    fireEvent.change(screen.getByTestId("new-ticket-subject"), { target: { value: "Frage" } });
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "eins zwei drei" } });
    await waitFor(() => expect(screen.getByTestId("new-ticket-refine-button")).toBeEnabled());
    fireEvent.click(screen.getByTestId("new-ticket-refine-button"));
    await screen.findByTestId("refine-review");
    // Turn the first change off.
    fireEvent.click(screen.getAllByTestId("refine-review-change")[0]);
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("new-ticket-submit"));
    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(1));
    expect(createArticle.mock.calls[0][1].body).toBe("eins zwei DREI");
  });
});
