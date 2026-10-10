import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { savedSearchesApi } from "@/lib/savedSearchesApi";
import { SavedSearchNav } from "./SavedSearchNav";
import { savedSearch as s, user } from "./searchTestUtils";

function renderNav(onNavigate = vi.fn()) {
  const root = createRootRoute({ component: () => <SavedSearchNav onNavigate={onNavigate} /> });
  const view = createRoute({ getParentRoute: () => root, path: "/agent/searches/$searchId", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([view]),
    history: createMemoryHistory({ initialEntries: ["/agent/searches/2"] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onNavigate;
}

const mockList = (rows: ReturnType<typeof s>[]) => vi.spyOn(savedSearchesApi, "list").mockResolvedValue(rows);
const mockCounts = (counts: Record<string, number | null>) =>
  vi.spyOn(savedSearchesApi, "counts").mockResolvedValue({ counts });

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
});
beforeEach(() => vi.restoreAllMocks());

describe("SavedSearchNav", () => {
  it("shows pinned searches with counts and hides unavailable counts", async () => {
    mockList([
      s({ id: 1, name: "Meine VIP", show_in_sidebar: true, show_count: true, position: 0 }),
      s({ id: 2, name: "Team", show_in_sidebar: true, show_count: true, position: 1 }),
      s({ id: 3, name: "Versteckt", show_in_sidebar: false }),
    ]);
    mockCounts({ "1": 4, "2": null });
    renderNav();
    expect(await screen.findByTestId("sidebar-search-1")).toHaveTextContent("Meine VIP");
    await waitFor(() => expect(screen.getByTestId("sidebar-search-1-count")).toHaveTextContent("4"));
    expect(screen.getByTestId("sidebar-search-1-count")).toHaveAttribute("aria-label", "4 Tickets");
    expect(screen.queryByTestId("sidebar-search-2-count")).toBeNull();
    expect(screen.getByTestId("sidebar-search-2")).toHaveAttribute("title", "Anzahl gerade nicht verfügbar");
    expect(screen.getByTestId("sidebar-search-1")).not.toHaveAttribute("title");
    expect(screen.queryByTestId("sidebar-search-3")).toBeNull();
    expect(screen.getByTestId("sidebar-search-1")).toHaveAttribute("href", "/agent/searches/1");
    expect(screen.getByTestId("sidebar-search-2")).toHaveAttribute("aria-current", "page");
  });

  it("orders by position and asks for counts only when a pinned search wants one", async () => {
    mockList([
      s({ id: 7, name: "Zweite", position: 5, show_count: false }),
      s({ id: 8, name: "Erste", position: 1, show_count: false }),
    ]);
    const counts = mockCounts({});
    renderNav();
    await screen.findByTestId("sidebar-search-7");
    const links = screen.getAllByTestId(/^sidebar-search-\d+$/).map((el) => el.textContent);
    expect(links).toEqual(["Erste", "Zweite"]);
    expect(counts).not.toHaveBeenCalled();
  });

  it("pins a search from the list of all searches", async () => {
    mockList([
      s({ id: 1, name: "Sichtbar", show_in_sidebar: true }),
      s({ id: 3, name: "Versteckt", show_in_sidebar: false, show_count: true }),
    ]);
    mockCounts({});
    const setPrefs = vi.spyOn(savedSearchesApi, "setPrefs").mockResolvedValue(s({ id: 3 }));
    renderNav();
    await user.click(await screen.findByTestId("sidebar-searches-all"));
    const pin = await screen.findByTestId("sidebar-searches-pin-3");
    expect(pin).not.toBeChecked();
    expect(screen.getByTestId("sidebar-searches-pin-1")).toBeChecked();
    await user.click(pin);
    await waitFor(() => expect(setPrefs).toHaveBeenCalledWith(3, { show_in_sidebar: true, show_count: true }));
  });

  it("says so when there are no searches", async () => {
    mockList([]);
    renderNav();
    expect(await screen.findByTestId("sidebar-search-empty")).toHaveTextContent("noch keine gespeicherten Suchen");
  });

  it("opens the Znuny import dialog from the list of all searches", async () => {
    mockList([s({ id: 1, name: "Sichtbar" })]);
    mockCounts({});
    const preview = vi.spyOn(savedSearchesApi, "znunyPreview").mockResolvedValue({ profiles: [] });
    renderNav();
    expect(preview).not.toHaveBeenCalled();
    await user.click(await screen.findByTestId("sidebar-searches-all"));
    const entry = await screen.findByTestId("sidebar-searches-import-znuny");
    expect(entry).toHaveTextContent("Suchvorlagen aus Znuny übernehmen");
    await user.click(entry);
    expect(await screen.findByTestId("znuny-import-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar-searches-all-panel")).toBeNull();
    expect(preview).toHaveBeenCalledTimes(1);
  });

  it("offers the import also when there are no searches yet", async () => {
    mockList([]);
    vi.spyOn(savedSearchesApi, "znunyPreview").mockResolvedValue({ profiles: [] });
    renderNav();
    await user.click(await screen.findByTestId("sidebar-searches-all"));
    expect(await screen.findByTestId("sidebar-searches-import-znuny")).toBeInTheDocument();
  });
});
