import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { createDemoCmdb, resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { api, ApiError } from "@/lib/api";
import type { Ci } from "@/lib/cmdbApi";
import { validateCiDetailSearch } from "@/components/cmdb/ciDetailModel";
import { CiDetailPage } from "./CiDetailPage";

let store: DemoCmdb;
let ciOverride: ((ci: Ci) => Ci) | null = null;
const requests: Array<{ method: string; path: string; query: URLSearchParams }> = [];

function serveDemo() {
  vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) {
      if (v === undefined || v === null) continue;
      query.append(k, String(v));
    }
    requests.push({ method, path, query });
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    if (out.status === 404) throw new ApiError(404, "Nicht gefunden.", path);
    if (ciOverride && method === "GET" && /\/cis\/\d+$/.test(path)) return ciOverride(out.body as Ci) as never;
    return out.body as never;
  });
}

async function renderDetail(initial: string) {
  const rootRoute = createRootRoute();
  const detailRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId", validateSearch: validateCiDetailSearch, component: CiDetailPage });
  const listRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb", component: () => <div data-testid="list-stub" /> });
  const editRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId/edit", component: () => null });
  const ticketRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/tickets/$ticketId", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([detailRoute, listRoute, editRoute, ticketRoute]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

beforeAll(() => {
  // the dependency tab mounts the graph canvas, which measures with ResizeObserver
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("DOMMatrixReadOnly", class { m22 = 1; });
});

beforeEach(async () => {
  store = createDemoCmdb();
  ciOverride = null;
  requests.length = 0;
  await i18n.changeLanguage("de");
  serveDemo();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

describe("CiDetailPage", () => {
  it("shows the derived-warning hint when the current incident state differs from the own one", async () => {
    await renderDetail("/agent/cmdb/1"); // web-01: own operational, current warning (a dependency is in incident)
    expect(await screen.findByTestId("ci-name")).toHaveTextContent("web-01.example.org");
    expect(screen.getByTestId("ci-number")).toHaveTextContent("SRV-0001");
    expect(screen.getByTestId("ci-class")).toHaveTextContent("Server");
    expect(within(screen.getByTestId("ci-incident-own")).getByTestId("ci-incident-badge")).toHaveTextContent("Betriebsbereit");
    expect(within(screen.getByTestId("ci-incident-current")).getByTestId("ci-incident-badge")).toHaveTextContent("Warnung");
    expect(screen.getByTestId("ci-derived-hint")).toHaveTextContent("Warnung (abgeleitet)");
    expect(screen.getByTestId("ci-deployment-badge")).toHaveTextContent("Produktiv");
    // overview tab: the attribute tree
    expect(screen.getByTestId("ci-view-hostname")).toHaveTextContent("web-01.example.org");
  });

  it("shows no hint when own and current incident state agree", async () => {
    await renderDetail("/agent/cmdb/3"); // db-01 is itself in incident
    await screen.findByTestId("ci-name");
    expect(screen.queryByTestId("ci-derived-hint")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("ci-incident-current")).getByTestId("ci-incident-badge")).toHaveTextContent("Vorfall");
  });

  it("keeps the tab in the URL", async () => {
    const router = await renderDetail("/agent/cmdb/1?tab=history");
    expect(await screen.findByTestId("ci-history")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Verlauf" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Versionen" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "versions" }));
    expect(await screen.findByTestId("ci-versions")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Übersicht" }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await screen.findByTestId("ci-view")).toBeInTheDocument();
  });

  it("ignores an unknown tab", async () => {
    await renderDetail("/agent/cmdb/1?tab=bogus");
    expect(await screen.findByTestId("ci-view")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Übersicht" })).toHaveAttribute("aria-selected", "true");
  });

  it("lists linked tickets, narrows to open ones and links to the ticket", async () => {
    await renderDetail("/agent/cmdb/3?tab=tickets");
    expect(await screen.findByTestId("ci-ticket-1")).toHaveTextContent("Datenbank antwortet nicht");
    await waitFor(() => expect(screen.getByTestId("ci-ticket-1")).toHaveTextContent("betrifft / betroffen von"));
    expect(screen.getByTestId("ci-tickets")).not.toHaveTextContent("RelevantTo");
    expect(screen.getByTestId("ci-ticket-2")).toBeInTheDocument();
    expect(within(screen.getByTestId("ci-ticket-1")).getByRole("link", { name: "2026100801" })).toHaveAttribute("href", "/agent/tickets/1");
    fireEvent.click(screen.getByTestId("ci-tickets-open-only"));
    await waitFor(() => expect(screen.queryByTestId("ci-ticket-2")).not.toBeInTheDocument());
    expect(await screen.findByTestId("ci-ticket-1")).toBeInTheDocument();
    expect(requests.some((r) => /\/cis\/3\/tickets$/.test(r.path) && r.query.get("open_only") === "true")).toBe(true);
  });

  it("lists the links, with an unreadable CI as a plain 'no access' text", async () => {
    await renderDetail("/agent/cmdb/3?tab=links");
    const list = await screen.findByTestId("ci-links");
    expect(within(list).getByRole("link", { name: /web-01/ })).toHaveAttribute("href", "/agent/cmdb/1");
    expect(list).toHaveTextContent("Kein Zugriff");
    expect(within(list).queryByRole("link", { name: /Kein Zugriff/ })).toBeNull();
  });

  it("loads the dependency graph and the affected objects on the 'Abhängigkeiten' tab", async () => {
    await renderDetail("/agent/cmdb/3?tab=dependencies");
    expect(await screen.findByTestId("ci-impact")).toBeInTheDocument();
    expect(await screen.findByTestId("impact-list")).toHaveTextContent("web-01");
    expect(await screen.findByTestId("ci-affected")).toBeInTheDocument();
    expect(requests.some((r) => /\/cis\/3\/impact$/.test(r.path))).toBe(true);
    expect(requests.some((r) => /\/cis\/3\/affected$/.test(r.path))).toBe(true);
  });

  it("offers adding a link on the 'Verknüpfungen' tab to writers", async () => {
    await renderDetail("/agent/cmdb/3?tab=links");
    expect(await screen.findByTestId("ci-link-add")).toBeInTheDocument();
  });

  it("deletes only after confirmation, then returns to the list", async () => {
    const router = await renderDetail("/agent/cmdb/4");
    fireEvent.click(await screen.findByTestId("ci-delete"));
    const dialog = await screen.findByTestId("confirm-dialog");
    expect(dialog).toHaveTextContent("SRV-0004");
    expect(dialog).toHaveTextContent("lb-01.example.org");
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    // cancel leaves everything alone
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument());
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    expect(store.cis.some((c) => c.id === 4)).toBe(true);
    // confirm deletes and navigates
    fireEvent.click(screen.getByTestId("ci-delete"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/cmdb"));
    expect(requests.filter((r) => r.method === "DELETE").map((r) => r.path)).toEqual(["/api/v1/cmdb/cis/4"]);
    expect(store.cis.some((c) => c.id === 4)).toBe(false);
    expect(await screen.findByTestId("list-stub")).toBeInTheDocument();
  });

  it("offers no edit or delete without write access", async () => {
    ciOverride = (ci) => ({ ...ci, can_write: false });
    await renderDetail("/agent/cmdb/1");
    await screen.findByTestId("ci-name");
    expect(screen.queryByTestId("ci-edit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ci-delete")).not.toBeInTheDocument();
  });

  it("links the edit action to the edit page for writers", async () => {
    await renderDetail("/agent/cmdb/1");
    expect(await screen.findByTestId("ci-edit")).toHaveAttribute("href", "/agent/cmdb/1/edit");
  });

  it("answers 'CI nicht gefunden' for a missing CI and for one the caller may not see", async () => {
    await renderDetail("/agent/cmdb/12345");
    expect(await screen.findByTestId("ci-not-found")).toHaveTextContent("CI nicht gefunden");
  });

  it("answers the same for the hidden CI of the demo", async () => {
    await renderDetail("/agent/cmdb/99");
    expect(await screen.findByTestId("ci-not-found")).toHaveTextContent("CI nicht gefunden");
    expect(screen.queryByTestId("ci-name")).not.toBeInTheDocument();
  });

  it("shows the mode notice instead of the CI when the CMDB is still kept in Znuny", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    await renderDetail("/agent/cmdb/1");
    expect(await screen.findByTestId("cmdb-mode-notice")).toBeInTheDocument();
    expect(requests.some((r) => /\/cis\/1$/.test(r.path))).toBe(false);
  });
});
