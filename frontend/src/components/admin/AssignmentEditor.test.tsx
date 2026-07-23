import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  AssignmentEditor,
  type AssignmentConfig,
} from "./AssignmentEditor";

type Queue = { id: number; name: string; valid_id?: number };
type Template = { id: number; name: string; template_type: string; valid_id?: number };
type CustomerUser = { login: string; first_name: string; last_name: string; valid_id?: number };
type Group = { id: number; name: string; valid_id?: number };

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

  it("shows bulk loadCounts badges on anchors without selecting them", async () => {
    const loadCounts = vi.fn();
    loadCounts.mockResolvedValue({ "3": 2, "4": 0 });
    const config: AssignmentConfig<Queue, Template> = {
      ...queueTemplateConfig,
      loadCounts: (dir) => loadCounts(dir),
    };
    renderEditor(config);

    await waitFor(() => {
      expect(loadCounts).toHaveBeenCalledWith("a");
    });
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-anchor-count-3")).toHaveTextContent("2");
      expect(screen.getByTestId("ae-qt-anchor-count-4")).toHaveTextContent("0");
    });
    // Must not have required selecting an anchor to load assigned sets for the badge.
    expect(listAssignedB).not.toHaveBeenCalled();
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

  it("grays invalid items with an (ungültig) marker; valid items stay unmarked", async () => {
    listQueues.mockResolvedValue([
      { id: 3, name: "Support", valid_id: 1 },
      { id: 9, name: "Legacy", valid_id: 2 },
    ]);
    listTemplates.mockResolvedValue([
      { id: 20, name: "Welcome", template_type: "Create", valid_id: 1 },
      { id: 22, name: "Retired", template_type: "Close", valid_id: 2 },
    ]);
    listAssignedB.mockResolvedValue([
      { id: 22, name: "Retired", template_type: "Close", valid_id: 2 },
    ]);

    const config: AssignmentConfig<Queue, Template> = {
      ...queueTemplateConfig,
      sideA: {
        ...queueTemplateConfig.sideA,
        isValid: (q) => q.valid_id === 1,
      },
      sideB: {
        ...queueTemplateConfig.sideB,
        isValid: (t) => t.valid_id === 1,
      },
    };
    renderEditor(config);

    // Default filter ("Gültig") hides the invalid, unassigned anchor.
    await screen.findByTestId("ae-qt-anchor-3");
    expect(screen.queryByTestId("ae-qt-anchor-9")).not.toBeInTheDocument();

    // Switching to "Alle" reveals it, grayed with the invalid marker.
    fireEvent.click(screen.getByTestId("ae-qt-valid-all"));
    const invalidAnchor = await screen.findByTestId("ae-qt-anchor-9");
    expect(invalidAnchor).toHaveAttribute("data-invalid", "true");
    expect(invalidAnchor).toHaveTextContent(/ungültig|invalid/i);
    expect(invalidAnchor.className).toMatch(/opacity-50/);

    const validAnchor = screen.getByTestId("ae-qt-anchor-3");
    expect(validAnchor).not.toHaveAttribute("data-invalid");
    expect(validAnchor).not.toHaveTextContent(/ungültig|invalid/i);

    fireEvent.click(validAnchor);
    await waitFor(() => {
      expect(screen.getByTestId("ae-qt-counterpart-22")).toBeChecked();
    });

    // Already-assigned invalid counterpart (22) stays visible even under the
    // default "Gültig" filter so it can still be unassigned.
    const invalidRow = screen.getByTestId("ae-qt-counterpart-row-22");
    expect(invalidRow).toHaveAttribute("data-invalid", "true");
    expect(invalidRow).toHaveTextContent(/ungültig|invalid/i);
    expect(invalidRow.className).toMatch(/opacity-50/);

    const validRow = screen.getByTestId("ae-qt-counterpart-row-20");
    expect(validRow).not.toHaveAttribute("data-invalid");
    expect(validRow).not.toHaveTextContent(/ungültig|invalid/i);

    // Checkbox on invalid item remains usable (unassign).
    fireEvent.click(screen.getByTestId("ae-qt-counterpart-22"));
    await waitFor(() => {
      expect(revoke).toHaveBeenCalledWith(3, 22);
    });
  });

  it("Gültigkeit filter defaults to hiding invalid entries and can reveal them", async () => {
    listQueues.mockResolvedValue([
      { id: 3, name: "Support", valid_id: 1 },
      { id: 9, name: "Legacy", valid_id: 2 },
    ]);
    listTemplates.mockResolvedValue([
      { id: 20, name: "Welcome", template_type: "Create", valid_id: 1 },
      { id: 22, name: "Retired", template_type: "Close", valid_id: 2 },
    ]);
    listAssignedB.mockResolvedValue([]);

    const config: AssignmentConfig<Queue, Template> = {
      ...queueTemplateConfig,
      sideA: {
        ...queueTemplateConfig.sideA,
        isValid: (q) => q.valid_id === 1,
      },
      sideB: {
        ...queueTemplateConfig.sideB,
        isValid: (t) => t.valid_id === 1,
      },
    };
    renderEditor(config);

    await screen.findByTestId("ae-qt-anchor-3");
    expect(screen.queryByTestId("ae-qt-anchor-9")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("ae-qt-anchor-3"));
    await waitFor(() => {
      expect(listAssignedB).toHaveBeenCalledWith(3);
    });
    // Unassigned invalid counterpart (22) is hidden by default.
    await screen.findByTestId("ae-qt-counterpart-20");
    expect(screen.queryByTestId("ae-qt-counterpart-row-22")).not.toBeInTheDocument();

    // "Ungültig" shows only the invalid items on both lists.
    fireEvent.click(screen.getByTestId("ae-qt-valid-invalid"));
    await screen.findByTestId("ae-qt-anchor-9");
    expect(screen.queryByTestId("ae-qt-anchor-3")).not.toBeInTheDocument();

    // "Alle" shows everything again.
    fireEvent.click(screen.getByTestId("ae-qt-valid-all"));
    await screen.findByTestId("ae-qt-anchor-3");
    await screen.findByTestId("ae-qt-anchor-9");
  });

  it("hides the Gültigkeit filter entirely when neither side defines isValid", async () => {
    renderEditor(queueTemplateConfig);
    await screen.findByTestId("ae-qt-anchor-3");
    expect(screen.queryByTestId("ae-qt-valid-filter")).not.toBeInTheDocument();
  });
});
