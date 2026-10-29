import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, renderWithClient } from "@/components/itsm/change/changeTestSupport";
import type { LegacyChangePage, LegacyChangeSummary } from "@/lib/changeApi";
import { LegacyChangeListPage } from "./LegacyChangeListPage";

const { legacyList, navigate, search } = vi.hoisted(() => ({ legacyList: vi.fn(), navigate: vi.fn(), search: { ticket_id: undefined as number | undefined } }));
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");
  return { ...actual, useNavigate: () => navigate, useSearch: () => search };
});
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, legacyList } };
});

const summary = (id: number, over: Partial<LegacyChangeSummary> = {}): LegacyChangeSummary => ({
  znuny_change_id: id,
  number: `20240401100000${id}`,
  title: `Core-Switch ${id}`,
  state: "successful",
  planned_start: "2024-04-06T20:00:00Z",
  planned_end: "2024-04-07T02:00:00Z",
  workorder_count: 2,
  changed_at: "2024-04-07T02:00:00Z",
  ...over,
});
const page = (source: LegacyChangePage["source"], items: LegacyChangeSummary[], total = items.length): LegacyChangePage => ({ source, items, total });

describe("LegacyChangeListPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    legacyList.mockReset();
    navigate.mockReset();
    search.ticket_id = undefined;
  });
  afterEach(cleanup);

  it("lists the changes with the live source badge and links to the detail page", async () => {
    legacyList.mockResolvedValue(page("live", [summary(11), summary(12, { title: null, planned_start: null, planned_end: null })]));
    renderWithClient(<LegacyChangeListPage />);
    expect(await screen.findByTestId("legacy-row-11")).toHaveTextContent("Core-Switch 11");
    expect(screen.getByTestId("legacy-source-badge")).toHaveTextContent("Live aus Znuny");
    expect(screen.getByTestId("legacy-source-badge")).toHaveAttribute("data-source", "live");
    expect(screen.getByRole("link", { name: "Core-Switch 11" })).toHaveAttribute("href", "/agent/itsm/legacy-changes/11");
    expect(screen.getByTestId("legacy-row-11")).toHaveTextContent("2024-04-06 20:00 – 2024-04-07 02:00");
    // no title: the number is the link text; no window: a placeholder
    expect(screen.getByTestId("legacy-row-12")).toHaveTextContent("Kein Zeitfenster");
    expect(legacyList).toHaveBeenCalledWith({ q: undefined, ticket_id: undefined, limit: 25, offset: 0 }, expect.anything());
  });

  it("shows the archive badge", async () => {
    legacyList.mockResolvedValue(page("archive", [summary(11)]));
    renderWithClient(<LegacyChangeListPage />);
    expect(await screen.findByTestId("legacy-source-badge")).toHaveTextContent("Archiv");
    expect(screen.getByTestId("legacy-source-badge")).toHaveAttribute("data-source", "archive");
  });

  it("filters by ?ticket_id= and can remove the filter", async () => {
    search.ticket_id = 130;
    legacyList.mockResolvedValue(page("live", [summary(11)]));
    renderWithClient(<LegacyChangeListPage />);
    expect(await screen.findByTestId("legacy-list-ticket-filter")).toHaveTextContent("Nur Changes zu Ticket #130");
    expect(legacyList).toHaveBeenCalledWith(expect.objectContaining({ ticket_id: 130 }), expect.anything());
    fireEvent.click(screen.getByTestId("legacy-list-ticket-clear"));
    expect(navigate).toHaveBeenCalledWith({ to: "/agent/itsm/legacy-changes", search: {} });
  });

  it("searches after a pause and says the search covers only number and title", async () => {
    legacyList.mockResolvedValue(page("live", [summary(11)]));
    renderWithClient(<LegacyChangeListPage />);
    await screen.findByTestId("legacy-row-11");
    expect(screen.getByTestId("legacy-list-search-hint")).toHaveTextContent("nur die Nummer und den Titel");
    fireEvent.change(screen.getByTestId("legacy-list-search"), { target: { value: " switch " } });
    await waitFor(() => expect(legacyList).toHaveBeenLastCalledWith({ q: "switch", ticket_id: undefined, limit: 25, offset: 0 }, expect.anything()));
  });

  it("pages through the results", async () => {
    legacyList.mockImplementation(async (q: { offset?: number }) => page("live", [summary(q.offset ? 40 : 11)], 30));
    renderWithClient(<LegacyChangeListPage />);
    await screen.findByTestId("legacy-row-11");
    expect(screen.getByTestId("legacy-list-prev")).toBeDisabled();
    await act(async () => {
      fireEvent.click(screen.getByTestId("legacy-list-next"));
    });
    expect(await screen.findByTestId("legacy-row-40")).toBeInTheDocument();
    expect(legacyList).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 }), expect.anything());
  });

  it("shows the empty state and an error by its code", async () => {
    legacyList.mockResolvedValueOnce(page("live", []));
    const { unmount } = renderWithClient(<LegacyChangeListPage />);
    expect(await screen.findByTestId("legacy-list-empty")).toHaveTextContent("Keine Znuny-Changes gefunden.");
    unmount();
    legacyList.mockRejectedValue(changeError(404, { code: "change_unavailable" }));
    renderWithClient(<LegacyChangeListPage />);
    expect(await screen.findByTestId("legacy-list-error")).toHaveTextContent("nicht eingeschaltet");
  });
});
