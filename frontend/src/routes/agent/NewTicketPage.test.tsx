import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
}));

vi.mock("@/lib/phoneApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phoneApi")>("@/lib/phoneApi");
  return {
    ...actual,
    phoneApi: { callerLookup, screenDynamicFields: vi.fn().mockResolvedValue([]) },
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
      listReferenceAgents: vi.fn().mockResolvedValue([{ id: 5, login: "agent1", full_name: "Agent One" }]),
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

describe("NewTicketPage refine", () => {
  beforeEach(() => {
    navigate.mockReset();
    listQueues.mockReset().mockResolvedValue([queue]);
    listReferencePriorities.mockReset().mockResolvedValue(priorities);
    listReferenceStates.mockReset().mockResolvedValue(states);
    searchReferenceCustomers.mockReset().mockResolvedValue([customer]);
    getComposeContext.mockReset().mockResolvedValue(composeContext);
    refine.mockReset();
    refineAvailability.mockReset().mockResolvedValue({ available: true });
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
});
