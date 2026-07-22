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
  createCustomer,
} = vi.hoisted(() => ({
  patchTicket: vi.fn(),
  listReferencePriorities: vi.fn(),
  listReferenceStates: vi.fn(),
  searchReferenceCustomers: vi.fn(),
  createCustomer: vi.fn(),
}));

const listReferenceAgents = vi.hoisted(() =>
  vi.fn().mockResolvedValue([
    { id: 2, login: "ada", full_name: "Ada Lovelace" },
    { id: 9, login: "bob", full_name: "Bob Agent" },
  ]),
);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      patchTicket,
      listReferencePriorities,
      listReferenceStates,
      listReferenceAgents,
      searchReferenceCustomers,
      createCustomer,
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
    permissions: ALL_PERMS,
    ...overrides,
  } as TicketDetail;
}

describe("ActionToolbar", () => {
  beforeEach(() => {
    patchTicket.mockReset().mockResolvedValue(undefined);
    searchReferenceCustomers.mockReset().mockResolvedValue([]);
    createCustomer.mockReset();
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
    wrap(
      <ActionToolbar
        ticket={makeTicket({
          can_write: false,
          permissions: {
            ro: true,
            move_into: false,
            create: false,
            note: false,
            owner: false,
            priority: false,
            rw: false,
          },
        })}
      />,
    );
    expect(screen.getByTestId("toolbar-priority")).toBeDisabled();
    expect(screen.getByTestId("toolbar-move")).toBeDisabled();
    // Print stays enabled — it is a client-side action.
    expect(screen.getByTestId("toolbar-print")).not.toBeDisabled();
  });

  it("gates each control by its own permission key", async () => {
    wrap(
      <ActionToolbar
        ticket={makeTicket({
          can_write: false,
          permissions: {
            ro: true,
            move_into: true,
            create: false,
            note: false,
            owner: false,
            priority: true,
            rw: false,
          },
        })}
      />,
    );
    // Priority menu also waits for the reference list to load.
    await waitFor(() =>
      expect(screen.getByTestId("toolbar-priority")).not.toBeDisabled(),
    );
    expect(screen.getByTestId("toolbar-move")).not.toBeDisabled();
    expect(screen.getByTestId("toolbar-owner")).toBeDisabled();
    expect(screen.getByTestId("toolbar-state")).toBeDisabled();
    expect(screen.getByTestId("toolbar-lock")).toBeDisabled();
  });

  it("relabels the move control as Queue", () => {
    wrap(<ActionToolbar ticket={makeTicket()} />);
    expect(screen.getByTestId("toolbar-move")).toHaveTextContent(/queue|Queue/i);
  });

  it("prefills the owner picker with the current owner_id", async () => {
    wrap(<ActionToolbar ticket={makeTicket({ owner_id: 2 })} />);
    fireEvent.click(screen.getByTestId("toolbar-owner"));
    const select = await screen.findByTestId("agent-picker-select");
    expect(select).toHaveValue("2");
  });

  it("shows the current customer and prefills search", async () => {
    searchReferenceCustomers.mockResolvedValue([]);
    wrap(
      <ActionToolbar
        ticket={makeTicket({
          customer_id: "C-9",
          customer_user_id: "bob",
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("toolbar-customer"));
    expect(screen.getByTestId("customer-picker-current")).toHaveTextContent("bob");
    expect(screen.getByTestId("customer-picker-current")).toHaveTextContent("C-9");
    const input = screen.getByPlaceholderText(/select|auswählen/i);
    expect(input).toHaveValue("bob");
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

  it("creates a new customer and assigns it to the ticket on success", async () => {
    createCustomer.mockResolvedValue({
      login: "newlogin",
      email: "new@example.com",
      customer_id: "C-NEW",
      first_name: "New",
      last_name: "Person",
    });
    wrap(<ActionToolbar ticket={makeTicket()} />);
    fireEvent.click(screen.getByTestId("toolbar-customer"));
    fireEvent.click(screen.getByTestId("customer-picker-new"));
    expect(screen.getByTestId("customer-create-dialog")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("customer-create-login"), {
      target: { value: "newlogin" },
    });
    fireEvent.change(screen.getByTestId("customer-create-email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(screen.getByTestId("customer-create-first-name"), {
      target: { value: "New" },
    });
    fireEvent.change(screen.getByTestId("customer-create-last-name"), {
      target: { value: "Person" },
    });
    fireEvent.change(screen.getByTestId("customer-create-customer-id"), {
      target: { value: "C-NEW" },
    });
    fireEvent.click(screen.getByTestId("customer-create-submit"));

    await waitFor(() =>
      expect(createCustomer).toHaveBeenCalledWith({
        login: "newlogin",
        email: "new@example.com",
        first_name: "New",
        last_name: "Person",
        customer_id: "C-NEW",
      }),
    );
    await waitFor(() =>
      expect(patchTicket).toHaveBeenCalledWith(7, {
        customer_user_id: "newlogin",
        customer_id: "C-NEW",
      }),
    );
  });

  it("shows a conflict message when the chosen login already exists (409)", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    createCustomer.mockRejectedValue(new ApiError(409, "Customer user login already exists", "/api/v1/customers"));
    wrap(<ActionToolbar ticket={makeTicket()} />);
    fireEvent.click(screen.getByTestId("toolbar-customer"));
    fireEvent.click(screen.getByTestId("customer-picker-new"));

    fireEvent.change(screen.getByTestId("customer-create-login"), {
      target: { value: "taken" },
    });
    fireEvent.change(screen.getByTestId("customer-create-email"), {
      target: { value: "taken@example.com" },
    });
    fireEvent.change(screen.getByTestId("customer-create-first-name"), {
      target: { value: "Tak" },
    });
    fireEvent.change(screen.getByTestId("customer-create-last-name"), {
      target: { value: "En" },
    });
    fireEvent.change(screen.getByTestId("customer-create-customer-id"), {
      target: { value: "C-1" },
    });
    fireEvent.click(screen.getByTestId("customer-create-submit"));

    expect(await screen.findByTestId("customer-create-conflict")).toBeInTheDocument();
    expect(patchTicket).not.toHaveBeenCalled();
  });
});
