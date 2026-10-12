import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { createDemoCmdb, demoCmdbJob, resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { api } from "@/lib/api";
import type { CiPage } from "@/lib/cmdbApi";
import { validateCmdbListSearch } from "@/components/cmdb/ciFilterModel";
import { CmdbListPage } from "./CmdbListPage";

let store: DemoCmdb;
let cappedPage: CiPage | null = null;
const requests: Array<{ method: string; path: string; body: unknown }> = [];

/** The component talks to the demo mock through the API client: the full chain, no hand-made fixtures. */
function serveDemo() {
  vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    requests.push({ method, path, body: opts?.body });
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) {
      if (v === undefined || v === null) continue;
      for (const item of Array.isArray(v) ? v : [v]) query.append(k, String(item));
    }
    const job = /\/api\/v1\/jobs\/(\d+)$/.exec(path);
    if (job) {
      const j = demoCmdbJob(store, Number(job[1]));
      if (j) return j as never;
    }
    if (cappedPage && path.endsWith("/cis/search")) return cappedPage as never;
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    return out.body as never;
  });
}

async function renderList(initial = "/agent/cmdb") {
  const rootRoute = createRootRoute();
  const listRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb", validateSearch: validateCmdbListSearch, component: CmdbListPage });
  const ciRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId", component: () => null });
  const newRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/new", component: () => null });
  const importRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/import", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([listRoute, ciRoute, newRoute, importRoute]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

const searches = () => requests.filter((r) => r.path.endsWith("/cis/search"));

beforeEach(async () => {
  store = createDemoCmdb();
  cappedPage = null;
  requests.length = 0;
  await i18n.changeLanguage("de");
  serveDemo();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

describe("CmdbListPage", () => {
  it("renders the demo rows with badges and the total", async () => {
    await renderList();
    expect(await screen.findByTestId("ci-row-1")).toHaveTextContent("web-01.example.org");
    expect(screen.getByTestId("ci-row-3")).toHaveTextContent("Vorfall");
    expect(screen.getByTestId("cmdb-total")).toHaveTextContent("6 CIs");
    expect(screen.getByTestId("ci-card-3")).toHaveTextContent("db-01.example.org");
  });

  it("debounces typing into a single request", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    const input = screen.getByTestId("ci-filter-q");
    for (const v of ["d", "db", "db-", "db-0", "db-01"]) fireEvent.change(input, { target: { value: v } });
    await waitFor(() => expect(screen.queryByTestId("ci-row-1")).not.toBeInTheDocument());
    const withQ = searches().filter((r) => (r.body as { q: string | null }).q !== null);
    expect(withQ).toHaveLength(1);
    expect((withQ[0].body as { q: string }).q).toBe("db-01");
    expect(screen.getByTestId("ci-row-3")).toBeInTheDocument();
  });

  it("enables the bulk button once rows are selected", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    expect(screen.getByTestId("cmdb-bulk")).toBeDisabled();
    fireEvent.click(screen.getByTestId("ci-check-1"));
    fireEvent.click(screen.getByTestId("ci-check-2"));
    expect(screen.getByTestId("cmdb-bulk")).toBeEnabled();
    expect(screen.getByTestId("cmdb-bulk")).toHaveTextContent("(2)");
  });

  it("says 'mehr als 10 000' when the total is capped", async () => {
    cappedPage = { items: [], total: 10001, total_capped: true };
    await renderList();
    expect(await screen.findByTestId("cmdb-total")).toHaveTextContent("mehr als 10 000");
  });

  it("shows the empty state for a search without hits", async () => {
    await renderList("/agent/cmdb?q=zzz-none");
    expect(await screen.findByTestId("cmdb-empty")).toHaveTextContent("Keine CIs gefunden");
    expect(screen.queryByTestId("cmdb-table")).not.toBeInTheDocument();
  });

  it("shows only the notice in Mode A and loads no CIs", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    await renderList();
    expect(await screen.findByTestId("cmdb-mode-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("cmdb-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ci-filter-bar")).not.toBeInTheDocument();
    expect(searches()).toHaveLength(0);
  });

  it("filters by incident functionality chips and by class", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    fireEvent.click(screen.getByTestId("ci-filter-inc-incident"));
    await waitFor(() => expect(screen.queryByTestId("ci-row-1")).not.toBeInTheDocument());
    expect(screen.getByTestId("ci-row-3")).toBeInTheDocument();
    const last = searches().at(-1)?.body as { filter: { where: { all: Array<{ field: string; value: string[] }> } } };
    expect(last.filter.where.all).toEqual([{ field: "incident_functionality", op: "in", value: ["incident"] }]);
  });

  it("runs bulk edit through the job dialog and reports the result", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    fireEvent.click(screen.getByTestId("ci-check-1"));
    fireEvent.click(screen.getByTestId("ci-check-2"));
    fireEvent.click(screen.getByTestId("cmdb-bulk"));
    fireEvent.click(await screen.findByTestId("bulk-incident"));
    fireEvent.click(await screen.findByTestId("bulk-incident-menu-option-13"));
    fireEvent.click(screen.getByTestId("bulk-submit"));
    expect(await screen.findByTestId("cmdb-bulk-summary")).toHaveTextContent("2 geändert, 0 übersprungen (keine Berechtigung)");
    const bulk = requests.find((r) => r.path.endsWith("/bulk-jobs"));
    expect(bulk?.body).toEqual({ ci_ids: [1, 2], incident_state_id: 13 });
    expect(store.cis.find((c) => c.id === 1)?.incidentOwn).toBe(13);
  });

  it("exports the current filter of one class", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    expect(screen.getByTestId("cmdb-export")).toBeDisabled();
    fireEvent.click(screen.getByTestId("ci-filter-class"));
    fireEvent.click(await screen.findByTestId("ci-filter-class-menu-option-1"));
    await waitFor(() => expect(screen.getByTestId("cmdb-export")).toBeEnabled());
    fireEvent.click(screen.getByTestId("cmdb-export"));
    fireEvent.click(await screen.findByTestId("export-format-xlsx"));
    fireEvent.click(screen.getByTestId("export-start"));
    await waitFor(() => expect(requests.some((r) => r.path.endsWith("/export-jobs"))).toBe(true));
    expect(requests.find((r) => r.path.endsWith("/export-jobs"))?.body).toEqual({ class_id: 1, q: null, filter: null, file_format: "xlsx" });
    expect(await screen.findByTestId("export-job-file-1")).toHaveTextContent("cmdb.xlsx");
  });

  it("saves the current search and applies it again", async () => {
    await renderList();
    await screen.findByTestId("ci-row-1");
    fireEvent.click(screen.getByTestId("ci-filter-inc-warning"));
    await waitFor(() => expect(screen.queryByTestId("ci-row-3")).not.toBeInTheDocument());
    fireEvent.click(screen.getByTestId("ci-saved-save"));
    fireEvent.change(await screen.findByTestId("ci-saved-name"), { target: { value: "Warnungen" } });
    fireEvent.click(screen.getByTestId("ci-saved-shared"));
    fireEvent.click(screen.getByTestId("ci-saved-confirm"));
    await waitFor(() => expect(store.saved).toHaveLength(1));
    expect(store.saved[0]).toMatchObject({ name: "Warnungen", shared: true });
    expect(store.saved[0].criteria.filter?.where).toEqual({ all: [{ field: "incident_functionality", op: "in", value: ["warning"] }] });
    await waitFor(() => expect(screen.getByTestId("ci-saved-select")).toHaveTextContent("Warnungen"));
  });
});
