import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { ApiError, type TicketListItem } from "@/lib/api";
import { savedSearchesApi } from "@/lib/savedSearchesApi";
import { savedSearch as s, user } from "@/components/searches/searchTestUtils";
import { SavedSearchPage } from "./SavedSearchPage";
import { validateSavedSearchSearch } from "./savedSearchSearch";

const TICKET = {
  id: 101,
  tn: "2026100101",
  title: "Drucker streikt",
  queue_id: 1,
  queue_name: "Support",
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
const PAGE = { items: [TICKET], limit: 50, offset: 0, total: 120 };
const CRIT = { version: 1, where: { all: [{ field: "queue_id", op: "in", value: [1] }] } };

function renderAt(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const agent = createRoute({ getParentRoute: () => root, path: "/agent", component: () => <p data-testid="dashboard">Start</p> });
  const edit = createRoute({ getParentRoute: () => root, path: "/agent/searches/$searchId/edit", component: () => <p data-testid="editor">Editor</p> });
  const view = createRoute({
    getParentRoute: () => root,
    path: "/agent/searches/$searchId",
    validateSearch: validateSavedSearchSearch,
    component: SavedSearchPage,
  });
  const ticket = createRoute({ getParentRoute: () => root, path: "/agent/tickets/$ticketId", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([agent, edit, view, ticket]),
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

const mockGet = (id: number, row: ReturnType<typeof s>) =>
  vi.spyOn(savedSearchesApi, "get").mockImplementation(async (got) => {
    if (got !== id) throw new ApiError(404, { detail: { message: "x" } }, "/api/v1/saved-searches");
    return row;
  });

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 1, is_admin: false } }) }));

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
});
beforeEach(() => vi.restoreAllMocks());

describe("SavedSearchPage", () => {
  it("lists tickets of the saved criteria with paging and sort in the address", async () => {
    const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    mockGet(5, s({ id: 5, criteria: CRIT as never, sort_by: "changed", sort_order: "asc" }));
    renderAt("/agent/searches/5?offset=50");
    await waitFor(() =>
      expect(query).toHaveBeenCalledWith(
        expect.objectContaining({ filter: CRIT, sort: "changed", order: "asc", offset: 50, limit: 50, include_archived: false }),
        expect.anything(),
      ),
    );
    expect(await screen.findByTestId("ticket-table")).toBeInTheDocument();
    expect(await screen.findByTestId("saved-search-total")).toHaveTextContent("120");
    expect(screen.getByTestId("ticket-row-101")).toHaveTextContent("Drucker streikt");
  });

  it("takes sort and order from the address over the saved ones", async () => {
    const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    mockGet(5, s({ id: 5, criteria: CRIT as never, sort_by: "changed", sort_order: "asc" }));
    renderAt("/agent/searches/5?sort=tn&order=desc");
    await waitFor(() =>
      expect(query).toHaveBeenCalledWith(expect.objectContaining({ sort: "tn", order: "desc", offset: 0 }), expect.anything()),
    );
  });

  it("writes a clicked sort column into the address and starts at the first page", async () => {
    const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    mockGet(5, s({ id: 5, criteria: CRIT as never }));
    const router = renderAt("/agent/searches/5?offset=50");
    await user.click(await screen.findByTestId("ticket-table-sort-tn"));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ sort: "tn" });
      expect((router.state.location.search as { offset?: number }).offset).toBeUndefined();
    });
    await waitFor(() => expect(query).toHaveBeenCalledWith(expect.objectContaining({ sort: "tn", offset: 0 }), expect.anything()));
  });

  it("shows the not-found message on 404", async () => {
    vi.spyOn(savedSearchesApi, "get").mockRejectedValue(new ApiError(404, { detail: { message: "x" } }, "/api/v1/saved-searches/5"));
    renderAt("/agent/searches/5");
    expect(await screen.findByTestId("saved-search-not-found")).toHaveTextContent("nicht mit dir geteilt");
  });

  it("hides edit and delete for shared searches", async () => {
    mockGet(6, s({ id: 6, criteria: CRIT as never, is_owner: false, can_edit: false, owner: { user_id: 9, name: "Kim" } }));
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    renderAt("/agent/searches/6");
    expect(await screen.findByTestId("saved-search-shared-by")).toHaveTextContent("Kim");
    expect(screen.queryByTestId("saved-search-edit")).toBeNull();
    expect(screen.queryByTestId("saved-search-delete")).toBeNull();
  });

  it("offers edit and delete to the owner, and does not claim the search is shared", async () => {
    mockGet(5, s({ id: 5, criteria: CRIT as never }));
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    renderAt("/agent/searches/5");
    expect(await screen.findByTestId("saved-search-edit")).toHaveAttribute("href", "/agent/searches/5/edit");
    expect(screen.queryByTestId("saved-search-shared-by")).toBeNull();
  });

  it("deletes after the confirmation and goes back to the dashboard", async () => {
    mockGet(5, s({ id: 5, name: "Weg damit", criteria: CRIT as never }));
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const remove = vi.spyOn(savedSearchesApi, "remove").mockResolvedValue(undefined);
    renderAt("/agent/searches/5");
    await user.click(await screen.findByTestId("saved-search-delete"));
    expect(remove).not.toHaveBeenCalled();
    expect(await screen.findByTestId("confirm-dialog")).toHaveTextContent("Weg damit");
    await user.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(5));
    expect(await screen.findByTestId("dashboard")).toBeInTheDocument();
  });

  it("toggles the sidebar entry through the caller's own preferences", async () => {
    mockGet(6, s({ id: 6, criteria: CRIT as never, is_owner: false, can_edit: false, show_in_sidebar: false, show_count: true }));
    vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    const setPrefs = vi.spyOn(savedSearchesApi, "setPrefs").mockResolvedValue(s({ id: 6 }));
    renderAt("/agent/searches/6");
    const pin = await screen.findByTestId("saved-search-pin");
    expect(pin).not.toBeChecked();
    await user.click(pin);
    await waitFor(() => expect(setPrefs).toHaveBeenCalledWith(6, { show_in_sidebar: true, show_count: true }));
  });

  it("explains broken criteria and offers to edit instead of querying", async () => {
    const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
    mockGet(5, s({ id: 5, criteria: null, criteria_error: "Feld gelöscht" }));
    renderAt("/agent/searches/5");
    expect(await screen.findByTestId("saved-search-broken")).toHaveTextContent("nicht mehr gültig");
    expect(screen.getByTestId("saved-search-broken-edit")).toHaveAttribute("href", "/agent/searches/5/edit");
    expect(query).not.toHaveBeenCalled();
  });

  it("treats a 422 from the query as broken criteria", async () => {
    vi.spyOn(savedSearchesApi, "queryTickets").mockRejectedValue(new ApiError(422, { detail: { message: "kaputt" } }, "/api/v1/tickets/query"));
    mockGet(5, s({ id: 5, criteria: CRIT as never }));
    renderAt("/agent/searches/5");
    expect(await screen.findByTestId("saved-search-broken")).toBeInTheDocument();
  });

  it("answers an address that is no id with the not-found message", async () => {
    renderAt("/agent/searches/abc");
    expect(await screen.findByTestId("saved-search-not-found")).toBeInTheDocument();
  });
});
