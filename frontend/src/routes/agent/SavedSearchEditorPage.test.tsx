import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { demoTicketFilterFields } from "@/demo/savedMock";
import { ApiError, type TicketListItem } from "@/lib/api";
import { reportsApi } from "@/lib/reportsApi";
import { savedSearchesApi } from "@/lib/savedSearchesApi";
import { savedSearch as s, user } from "@/components/searches/searchTestUtils";
import { searchPreviewDebounce } from "@/components/searches/useDebouncedValue";
import { SavedSearchEditorPage } from "./SavedSearchEditorPage";
import { validateSavedSearchSearch } from "./savedSearchSearch";

const auth = vi.hoisted(() => ({ user: {} as Record<string, unknown> }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: auth.user }) }));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    request: vi.fn(async (_method: string, path: string) =>
      path.endsWith("/reference/queues")
        ? [
            { id: 1, name: "Postmaster" },
            { id: 2, name: "Support" },
          ]
        : [],
    ),
  },
}));

const TICKET = {
  id: 101,
  tn: "2026100101",
  title: "Drucker streikt",
  queue_id: 1,
  queue_name: "Postmaster",
  state_id: 4,
  state: "open",
  state_type: "open",
  priority_id: 3,
  priority: "3 normal",
  lock_id: 1,
  lock: "unlock",
  owner_id: 5,
  create_time: "2026-10-01T12:00:00",
  change_time: "2026-10-01T12:00:00",
  escalation_time: 0,
  escalation_response_time: 0,
  escalation_update_time: 0,
  escalation_solution_time: 0,
  until_time: 0,
  attachment_count: 0,
  channel: "email",
  has_ai_summary: false,
  ai_escalated: false,
  ai_paused: false,
  archive_flag: 0,
} as TicketListItem;
const PAGE = { items: [TICKET], limit: 10, offset: 0, total: 12 };
const QUEUE_FILTER = { version: 1, where: { all: [{ field: "queue_id", op: "in", value: [1] }] } };

function renderAt(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const agent = createRoute({ getParentRoute: () => root, path: "/agent", component: () => <p data-testid="dashboard">Start</p> });
  const created = createRoute({ getParentRoute: () => root, path: "/agent/searches/new", component: SavedSearchEditorPage });
  const edit = createRoute({ getParentRoute: () => root, path: "/agent/searches/$searchId/edit", component: SavedSearchEditorPage });
  const view = createRoute({
    getParentRoute: () => root,
    path: "/agent/searches/$searchId",
    validateSearch: validateSavedSearchSearch,
    component: () => <p data-testid="search-view">Ansicht</p>,
  });
  const ticket = createRoute({ getParentRoute: () => root, path: "/agent/tickets/$ticketId", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([agent, created, edit, view, ticket]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

/** Picks "Queue" / "ist eines von" / "Postmaster" in the first condition row. */
async function addQueueCondition() {
  await user.click(await screen.findByTestId("filter-builder-add-condition"));
  await user.click(screen.getByTestId("filter-builder-row-0-field"));
  await user.click(screen.getByRole("option", { name: "Queue" }));
  // the operator is a native select
  fireEvent.change(screen.getByTestId("filter-builder-row-0-op"), { target: { value: "in" } });
  await user.click(await screen.findByTestId("filter-builder-row-0-value-add"));
  await user.click(await screen.findByRole("option", { name: "Postmaster" }));
}

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  vi.restoreAllMocks();
  searchPreviewDebounce.ms = 20;
  auth.user = { id: 1, is_admin: false, can_share_reports: true };
  vi.spyOn(savedSearchesApi, "ticketFilterFields").mockResolvedValue(demoTicketFilterFields());
  vi.spyOn(reportsApi, "shareGroups").mockResolvedValue([
    { id: 2, name: "users" },
    { id: 3, name: "Support" },
  ]);
});
afterEach(() => {
  searchPreviewDebounce.ms = 500;
});

describe("SavedSearchEditorPage", () => {
  it("creates a search with a live count", async () => {
    const count = vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 12 });
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 9 }) as never);
    const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const router = renderAt("/agent/searches/new");
    await addQueueCondition();
    await user.type(screen.getByTestId("saved-search-editor-name"), "Neu");
    expect(await screen.findByTestId("saved-search-editor-count")).toHaveTextContent("12 Tickets passen");
    expect(count).toHaveBeenCalledWith(QUEUE_FILTER, expect.anything());
    expect(query).toHaveBeenCalledWith(expect.objectContaining({ filter: QUEUE_FILTER, limit: 10 }), expect.anything());
    expect(await screen.findByTestId("ticket-row-101")).toHaveTextContent("Drucker streikt");
    await user.click(screen.getByTestId("saved-search-editor-save"));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Neu", criteria: QUEUE_FILTER, group_ids: [], include_archived: false }),
      ),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/searches/9"));
  });

  it("counts only finished conditions, once the edits have settled", async () => {
    searchPreviewDebounce.ms = 120;
    const count = vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 3 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    renderAt("/agent/searches/new");
    await addQueueCondition();
    expect(await screen.findByTestId("saved-search-editor-count")).toHaveTextContent("3 Tickets passen");
    // the empty filter at the start, then the finished one - never the half-built conditions between
    expect(count.mock.calls.map((c) => JSON.stringify(c[0]))).toEqual([
      JSON.stringify({ version: 1 }),
      JSON.stringify(QUEUE_FILTER),
    ]);
  });

  it("does not save without a name or with an unfinished condition", async () => {
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 0 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue({ ...PAGE, items: [], total: 0 } as never);
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 9 }) as never);
    renderAt("/agent/searches/new");
    await user.click(await screen.findByTestId("filter-builder-add-condition"));
    await user.click(screen.getByTestId("saved-search-editor-save"));
    expect(await screen.findByTestId("saved-search-editor-name-error")).toBeInTheDocument();
    expect(screen.getByTestId("saved-search-editor-filter-error")).toHaveTextContent("markierten Bedingungen");
    expect(create).not.toHaveBeenCalled();
  });

  it("offers archived tickets to admins only", async () => {
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 0 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    renderAt("/agent/searches/new");
    await screen.findByTestId("saved-search-editor-name");
    expect(screen.queryByTestId("saved-search-editor-archived")).toBeNull();
  });

  it("sends the archive switch for an admin", async () => {
    auth.user = { id: 1, is_admin: true, can_share_reports: true };
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 0 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 9 }) as never);
    renderAt("/agent/searches/new");
    await user.click(await screen.findByTestId("saved-search-editor-archived"));
    await user.type(screen.getByTestId("saved-search-editor-name"), "Alles");
    await user.click(screen.getByTestId("saved-search-editor-save"));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ include_archived: true })));
  });

  it("shares with groups only, and locks the choice without the sharing right", async () => {
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 0 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 9 }) as never);
    renderAt("/agent/searches/new");
    // a search has no "everyone" choice
    await screen.findByTestId("saved-search-editor-share-group-ids");
    expect(screen.queryByText("Alle mit Berichtszugriff")).toBeNull();
    await user.click(await screen.findByTestId("saved-search-editor-share-group-ids-add"));
    await user.click(await screen.findByRole("option", { name: "Support" }));
    await user.type(screen.getByTestId("saved-search-editor-name"), "Geteilt");
    await user.click(screen.getByTestId("saved-search-editor-save"));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ group_ids: [3] })));
  });

  it("locks sharing without the right", async () => {
    auth.user = { id: 1, is_admin: false, can_share_reports: false };
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 0 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    renderAt("/agent/searches/new");
    expect(await screen.findByTestId("saved-search-editor-share-hint")).toHaveTextContent("Berechtigung");
  });

  it("updates an existing search with the version it loaded, and reports a 409", async () => {
    vi.spyOn(savedSearchesApi, "get").mockResolvedValue(
      s({ id: 5, name: "Alt", criteria: QUEUE_FILTER as never, version: 4, sort_by: "tn", sort_order: "asc", group_ids: [3] }),
    );
    vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 1 });
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const update = vi
      .spyOn(savedSearchesApi, "update")
      .mockRejectedValue(new ApiError(409, { detail: { message: "stale" } }, "/api/v1/saved-searches/5"));
    renderAt("/agent/searches/5/edit");
    const name = await screen.findByTestId("saved-search-editor-name");
    expect(name).toHaveValue("Alt");
    await user.type(name, "er");
    await user.click(screen.getByTestId("saved-search-editor-save"));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        5,
        expect.objectContaining({ name: "Alter", version: 4, criteria: QUEUE_FILTER, sort_by: "tn", sort_order: "asc", group_ids: [3] }),
      ),
    );
    expect(await screen.findByTestId("saved-search-editor-error")).toHaveTextContent("inzwischen geändert");
    expect(screen.getByTestId("saved-search-editor-reload")).toBeInTheDocument();
  });

  it("does not let a viewer without edit rights open the editor", async () => {
    vi.spyOn(savedSearchesApi, "get").mockResolvedValue(s({ id: 6, is_owner: false, can_edit: false }));
    renderAt("/agent/searches/6/edit");
    expect(await screen.findByTestId("saved-search-editor-no-right")).toBeInTheDocument();
    expect(screen.queryByTestId("saved-search-editor")).toBeNull();
  });

  it("shows the not-found message for an unknown search", async () => {
    vi.spyOn(savedSearchesApi, "get").mockRejectedValue(new ApiError(404, { detail: { message: "x" } }, "/api/v1/saved-searches/5"));
    renderAt("/agent/searches/5/edit");
    expect(await screen.findByTestId("saved-search-editor-not-found")).toHaveTextContent("nicht mit dir geteilt");
  });
});
