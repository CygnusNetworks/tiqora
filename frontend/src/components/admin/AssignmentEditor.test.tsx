import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  AssignmentEditor,
  type AssignmentConfig,
} from "./AssignmentEditor";

type Queue = { id: number; name: string };
type Template = { id: number; name: string; template_type: string };
type CustomerUser = { login: string; first_name: string; last_name: string };
type Group = { id: number; name: string };

const listQueues = vi.fn();
const listTemplates = vi.fn();
const listAssignedB = vi.fn();
const listAssignedA = vi.fn();
const assign = vi.fn();
const revoke = vi.fn();

const queueTemplateConfig: AssignmentConfig<Queue, Template> = {
  testId: "ae-qt",
  titleKey: "admin.queueTemplates.title",
  subtitleKey: "admin.queueTemplates.subtitle",
  sideA: {
    key: "queues",
    labelKey: "admin.queueTemplates.queue",
    loadItems: () => listQueues(),
    getId: (q) => q.id,
    getLabel: (q) => q.name,
  },
  sideB: {
    key: "templates",
    labelKey: "admin.queueTemplates.templates",
    loadItems: () => listTemplates(),
    getId: (t) => t.id,
    getLabel: (t) => t.name,
    getSubLabel: (t) => t.template_type,
  },
  loadAssignedB: (aId) => listAssignedB(aId),
  loadAssignedA: (bId) => listAssignedA(bId),
  assign: (a, b) => assign(a, b),
  revoke: (a, b) => revoke(a, b),
};

const listCustomerUsers = vi.fn();
const listGroups = vi.fn();
const listCuGroups = vi.fn();
const listGroupCus = vi.fn();
const assignCuGroup = vi.fn();
const revokeCuGroup = vi.fn();

const customerGroupConfig: AssignmentConfig<CustomerUser, Group> = {
  testId: "ae-cug",
  titleKey: "admin.customerUserGroups.title",
  subtitleKey: "admin.customerUserGroups.subtitle",
  sideA: {
    key: "customer-users",
    labelKey: "admin.customerUserGroups.customerUser",
    loadItems: () => listCustomerUsers(),
    getId: (u) => u.login,
    getLabel: (u) => u.login,
    getSubLabel: (u) => `${u.first_name} ${u.last_name}`,
  },
  sideB: {
    key: "groups",
    labelKey: "admin.customerUserGroups.groups",
    loadItems: () => listGroups(),
    getId: (g) => g.id,
    getLabel: (g) => g.name,
  },
  loadAssignedB: (login) => listCuGroups(login),
  loadAssignedA: (gId) => listGroupCus(gId),
  assign: (login, gId) => assignCuGroup(login, gId),
  revoke: (login, gId) => revokeCuGroup(login, gId),
};

function renderEditor<A, B>(config: AssignmentConfig<A, B>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AssignmentEditor config={config} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AssignmentEditor", () => {
  beforeEach(() => {
    listQueues.mockReset();
    listTemplates.mockReset();
    listAssignedB.mockReset();
    listAssignedA.mockReset();
    assign.mockReset();
    revoke.mockReset();
    listCustomerUsers.mockReset();
    listGroups.mockReset();
    listCuGroups.mockReset();
    listGroupCus.mockReset();
    assignCuGroup.mockReset();
    revokeCuGroup.mockReset();

    listQueues.mockResolvedValue([
      { id: 3, name: "Support" },
      { id: 4, name: "Sales" },
    ]);
    listTemplates.mockResolvedValue([
      { id: 20, name: "Welcome", template_type: "Create" },
      { id: 21, name: "Close", template_type: "Close" },
    ]);
    listAssignedB.mockResolvedValue([{ id: 20, name: "Welcome", template_type: "Create" }]);
    listAssignedA.mockResolvedValue([{ id: 3, name: "Support" }]);
    assign.mockResolvedValue(undefined);
    revoke.mockResolvedValue(undefined);

    listCustomerUsers.mockResolvedValue([
      { login: "alice", first_name: "Alice", last_name: "A" },
    ]);
    listGroups.mockResolvedValue([
      { id: 5, name: "users" },
      { id: 6, name: "stats" },
    ]);
    listCuGroups.mockResolvedValue([{ id: 5, name: "users" }]);
    listGroupCus.mockResolvedValue([]);
    assignCuGroup.mockResolvedValue(undefined);
    revokeCuGroup.mockResolvedValue(undefined);
  });

  it("renders assigned counterparts as checked (preselection)", async () => {
    renderEditor(queueTemplateConfig);

    await screen.findByTestId("ae-qt-anchor-3");
    fireEvent.click(screen.getByTestId("ae-qt-anchor-3"));

    await waitFor(() => {
      expect(listAssignedB).toHaveBeenCalledWith(3);
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-counterpart-20")).toBeChecked();
    });
    expect(screen.getByTestId("ae-qt-counterpart-21")).not.toBeChecked();
  });

  it("direction toggle swaps master/detail and loads reverse assigned set", async () => {
    renderEditor(queueTemplateConfig);

    await screen.findByTestId("ae-qt-anchor-3");
    fireEvent.click(screen.getByTestId("ae-qt-direction-b"));

    // Master is now templates
    await screen.findByTestId("ae-qt-anchor-20");
    fireEvent.click(screen.getByTestId("ae-qt-anchor-20"));

    await waitFor(() => {
      expect(listAssignedA).toHaveBeenCalledWith(20);
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-counterpart-3")).toBeChecked();
    });
  });

  it("Alle / Keine bulk actions assign and revoke all counterparts", async () => {
    renderEditor(queueTemplateConfig);

    await screen.findByTestId("ae-qt-anchor-3");
    fireEvent.click(screen.getByTestId("ae-qt-anchor-3"));
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-counterpart-20")).toBeChecked();
    });

    // Subsequent refetches after Alle should see both as assigned.
    listAssignedB.mockResolvedValue([
      { id: 20, name: "Welcome", template_type: "Create" },
      { id: 21, name: "Close", template_type: "Close" },
    ]);

    // Alle: assign every unassigned counterpart (21).
    fireEvent.click(screen.getByTestId("ae-qt-bulk-all"));
    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith(3, 21);
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-counterpart-20")).toBeChecked();
      expect(screen.getByTestId("ae-qt-counterpart-21")).toBeChecked();
    });

    // Keine: revoke every currently checked counterpart.
    fireEvent.click(screen.getByTestId("ae-qt-bulk-none"));
    await waitFor(() => {
      expect(revoke).toHaveBeenCalled();
    });
    const revoked = new Set(
      revoke.mock.calls.filter((c) => c[0] === 3).map((c) => c[1] as number),
    );
    expect(revoked.has(20)).toBe(true);
    expect(revoked.has(21)).toBe(true);
  });

  it("works with string-id relations (customer user ↔ groups)", async () => {
    renderEditor(customerGroupConfig);

    await screen.findByTestId("ae-cug-anchor-alice");
    fireEvent.click(screen.getByTestId("ae-cug-anchor-alice"));

    await waitFor(() => {
      expect(listCuGroups).toHaveBeenCalledWith("alice");
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-cug-counterpart-5")).toBeChecked();
    });
    expect(screen.getByTestId("ae-cug-counterpart-6")).not.toBeChecked();

    fireEvent.click(screen.getByTestId("ae-cug-counterpart-6"));
    await waitFor(() => {
      expect(assignCuGroup).toHaveBeenCalledWith("alice", 6);
    });
  });

  it("server-search side debounces searchItems and renders results", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const searchUsers = vi.fn();
    searchUsers.mockResolvedValue([
      { login: "bob", first_name: "Bob", last_name: "B" },
    ]);

    const config: AssignmentConfig<CustomerUser, Group> = {
      ...customerGroupConfig,
      sideA: {
        ...customerGroupConfig.sideA,
        loadItems: () => listCustomerUsers(),
        searchItems: (q) => searchUsers(q),
      },
    };

    renderEditor(config);

    // Empty query fires searchItems("") for the first page.
    await waitFor(() => {
      expect(searchUsers).toHaveBeenCalledWith("");
    });
    await screen.findByTestId("ae-cug-anchor-bob");

    searchUsers.mockClear();
    searchUsers.mockResolvedValue([
      { login: "carol", first_name: "Carol", last_name: "C" },
    ]);

    fireEvent.change(screen.getByTestId("ae-cug-search-anchor"), {
      target: { value: "car" },
    });

    // Not called immediately (debounce 300ms).
    expect(searchUsers).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      expect(searchUsers).toHaveBeenCalledWith("car");
    });
    await screen.findByTestId("ae-cug-anchor-carol");
    expect(screen.queryByTestId("ae-cug-anchor-bob")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("counterpart server-search shows assigned + search hits and assigns on toggle", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const searchUsers = vi.fn();
    // Initial empty search for counterpart candidates.
    searchUsers.mockResolvedValue([
      { login: "dave", first_name: "Dave", last_name: "D" },
    ]);
    listGroupCus.mockResolvedValue([
      { login: "alice", first_name: "Alice", last_name: "A" },
    ]);

    // Groups as master (client); customer-users as counterpart with server search.
    const serverCounterpartConfig: AssignmentConfig<Group, CustomerUser> = {
      testId: "ae-gcu",
      titleKey: "admin.customerUserGroups.title",
      subtitleKey: "admin.customerUserGroups.subtitle",
      sideA: {
        key: "groups",
        labelKey: "admin.customerUserGroups.groups",
        loadItems: () => listGroups(),
        getId: (g) => g.id,
        getLabel: (g) => g.name,
      },
      sideB: {
        key: "customer-users",
        labelKey: "admin.customerUserGroups.customerUser",
        loadItems: () => listCustomerUsers(),
        searchItems: (q) => searchUsers(q),
        getId: (u) => u.login,
        getLabel: (u) => u.login,
        getSubLabel: (u) => `${u.first_name} ${u.last_name}`,
      },
      loadAssignedB: (gId) => listGroupCus(gId),
      loadAssignedA: (login) => listCuGroups(login),
      assign: (gId, login) => assignCuGroup(login, gId),
      revoke: (gId, login) => revokeCuGroup(login, gId),
    };

    renderEditor(serverCounterpartConfig);

    await screen.findByTestId("ae-gcu-anchor-5");
    fireEvent.click(screen.getByTestId("ae-gcu-anchor-5"));

    await waitFor(() => {
      expect(listGroupCus).toHaveBeenCalledWith(5);
    });
    // Assigned alice is checked; search hit dave is not.
    await waitFor(() => {
      expect(screen.getByTestId("ae-gcu-counterpart-alice")).toBeChecked();
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-gcu-counterpart-dave")).not.toBeChecked();
    });

    // Search for another user and assign them.
    searchUsers.mockResolvedValue([
      { login: "erin", first_name: "Erin", last_name: "E" },
    ]);
    fireEvent.change(screen.getByTestId("ae-gcu-search-counterpart"), {
      target: { value: "erin" },
    });
    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      expect(searchUsers).toHaveBeenCalledWith("erin");
    });
    await screen.findByTestId("ae-gcu-counterpart-erin");
    // Assigned alice still present (not deduped away).
    expect(screen.getByTestId("ae-gcu-counterpart-alice")).toBeChecked();

    fireEvent.click(screen.getByTestId("ae-gcu-counterpart-erin"));
    await waitFor(() => {
      expect(assignCuGroup).toHaveBeenCalledWith("erin", 5);
    });

    vi.useRealTimers();
  });
});
