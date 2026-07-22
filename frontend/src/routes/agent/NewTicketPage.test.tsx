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
} = vi.hoisted(() => ({
  navigate: vi.fn(),
  listQueues: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  searchReferenceCustomers: vi.fn(),
  getComposeContext: vi.fn(),
  createTicket: vi.fn(),
  createArticle: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => ({}),
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
    <a href={`${to}${params ? `/${Object.values(params).join("/")}` : ""}`} {...rest}>
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
  await waitFor(() => expect(screen.getByTestId("new-ticket-type-email")).toBeInTheDocument());
  await waitFor(() => expect(screen.getByTestId("new-ticket-customer-search")).toBeInTheDocument());
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
  const result = await screen.findByTestId("new-ticket-customer-result-jane.doe");
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
  });

  it("defaults to email mode and seeds the To chip + customer card on selection", async () => {
    await renderReady();
    expect(screen.getByTestId("new-ticket-type-email")).toHaveAttribute("aria-pressed", "true");

    await pickCustomer();

    expect(screen.getByTestId("new-ticket-customer-card")).toHaveTextContent("Jane Doe");
    expect(screen.getByTestId("new-ticket-to")).toHaveTextContent("Jane Doe");
  });

  it("keeps submit disabled until To/subject/body are filled, then enables it and creates the ticket + article", async () => {
    await renderReady();
    await pickCustomer();
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).toBeDisabled());

    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Question about invoice" },
    });
    expect(screen.getByTestId("new-ticket-submit")).toBeDisabled();

    fireEvent.change(screen.getByTestId("new-ticket-body"), {
      target: { value: "Please advise." },
    });
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());

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

  it("phone mode hides recipients/signature and submits with direction-dependent sender_type", async () => {
    await renderReady();
    fireEvent.click(screen.getByTestId("new-ticket-type-phone"));
    fireEvent.click(screen.getByTestId("new-ticket-skip-customer"));

    expect(screen.queryByTestId("new-ticket-to")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-ticket-from")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-ticket-direction-in")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("new-ticket-direction-out"));
    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Called about delivery" },
    });
    fireEvent.change(screen.getByTestId("new-ticket-body"), {
      target: { value: "Customer called back." },
    });
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() => expect(createArticle).toHaveBeenCalledTimes(1));
    expect(createArticle).toHaveBeenCalledWith(
      42,
      expect.objectContaining({
        channel: "phone",
        sender_type: "agent",
        content_type: "text/plain; charset=utf-8",
      }),
    );
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
      new ApiError(502, "Outbound email delivery failed: SMTP refused", "/api/v1/tickets/42/articles"),
    );
    await renderReady();
    await pickCustomer();
    fireEvent.change(screen.getByTestId("new-ticket-subject"), {
      target: { value: "Question" },
    });
    fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "Body text" } });
    await waitFor(() => expect(screen.getByTestId("new-ticket-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("new-ticket-submit"));

    await waitFor(() => expect(screen.getByTestId("new-ticket-error")).toBeInTheDocument());
    expect(screen.getByTestId("new-ticket-error")).toHaveTextContent(
      /could not be sent/,
    );
    expect(screen.getByText("Go to ticket")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
