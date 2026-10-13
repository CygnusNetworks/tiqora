import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { makeCi } from "@/components/itsm/testSupport";
import { CiListPage } from "./CiListPage";

const { cis, catalog } = vi.hoisted(() => ({ cis: vi.fn(), catalog: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, cis, catalog } };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiListPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const page = (n: number, total = n, truncated = false) => ({
  items: Array.from({ length: n }, (_, i) => makeCi(i + 1)),
  total,
  truncated,
});

beforeEach(() => {
  cis.mockReset().mockResolvedValue(page(2));
  catalog.mockReset().mockResolvedValue({
    classes: [{ key: "computer", name: "Computer", icon: null, can_read: true, can_write: false }],
    deployment_states: [{ key: "1", name: "Produktiv", functionality: "operational" }],
    incident_states: [{ key: "2", name: "Störung", functionality: "incident" }],
  });
});
afterEach(cleanup);

describe("CiListPage", () => {
  it("lists CIs with number link, class and state badges", async () => {
    renderPage();
    const row = await screen.findByTestId("ci-row-1");
    expect(row).toHaveTextContent("srv-1.example.org");
    expect(row).toHaveTextContent("Computer");
    expect(row.querySelector("a")).toHaveAttribute("href", "/agent/itsm/cis/znuny/1");
    expect(screen.getAllByTestId("ci-state-badge").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByTestId("ci-list-range")).toHaveTextContent("1–2 of 2");
    expect(cis).toHaveBeenCalledWith(expect.objectContaining({ q: undefined, limit: 25, offset: 0 }), expect.anything());
  });

  it("debounces the search: one request for fast typing", async () => {
    renderPage();
    await screen.findByTestId("ci-row-1");
    cis.mockClear();
    const input = screen.getByTestId("ci-list-search");
    fireEvent.change(input, { target: { value: "s" } });
    fireEvent.change(input, { target: { value: "sr" } });
    fireEvent.change(input, { target: { value: "srv-2" } });
    await waitFor(() => expect(cis).toHaveBeenCalledWith(expect.objectContaining({ q: "srv-2" }), expect.anything()));
    expect(cis.mock.calls.filter((c) => c[0].q === "s" || c[0].q === "sr")).toHaveLength(0);
  });

  it("hints that attribute values are searched from 3 characters on", async () => {
    renderPage();
    await screen.findByTestId("ci-row-1");
    const input = screen.getByTestId("ci-list-search");
    expect(screen.queryByTestId("ci-list-hint")).toBeNull();
    fireEvent.change(input, { target: { value: "ab" } });
    expect(screen.getByTestId("ci-list-hint")).toHaveTextContent("3 characters");
    fireEvent.change(input, { target: { value: "abc" } });
    expect(screen.queryByTestId("ci-list-hint")).toBeNull();
  });

  it("sends the class, deployment and incident filters and resets paging", async () => {
    cis.mockResolvedValue(page(25, 60));
    renderPage();
    await screen.findByTestId("ci-row-1");
    fireEvent.click(screen.getByTestId("ci-list-next"));
    await waitFor(() => expect(cis).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 }), expect.anything()));
    fireEvent.click(screen.getByTestId("ci-list-class"));
    fireEvent.click(await screen.findByTestId("ci-list-class-menu-option-computer"));
    await waitFor(() =>
      expect(cis).toHaveBeenLastCalledWith(expect.objectContaining({ class: ["computer"], offset: 0 }), expect.anything()),
    );
    fireEvent.click(screen.getByTestId("ci-list-depl"));
    fireEvent.click(await screen.findByTestId("ci-list-depl-menu-option-1"));
    fireEvent.click(screen.getByTestId("ci-list-inci"));
    fireEvent.click(await screen.findByTestId("ci-list-inci-menu-option-2"));
    await waitFor(() =>
      expect(cis).toHaveBeenLastCalledWith(
        expect.objectContaining({ class: ["computer"], depl: ["1"], inci: ["2"] }),
        expect.anything(),
      ),
    );
  });

  it("pages forward and back", async () => {
    cis.mockResolvedValue(page(25, 60));
    renderPage();
    await screen.findByTestId("ci-row-1");
    expect(screen.getByTestId("ci-list-prev")).toBeDisabled();
    expect(screen.getByTestId("ci-list-range")).toHaveTextContent("1–25 of 60");
    fireEvent.click(screen.getByTestId("ci-list-next"));
    await waitFor(() => expect(screen.getByTestId("ci-list-range")).toHaveTextContent("26–50 of 60"));
    fireEvent.click(screen.getByTestId("ci-list-prev"));
    await waitFor(() => expect(screen.getByTestId("ci-list-range")).toHaveTextContent("1–25 of 60"));
  });

  it("shows the empty state", async () => {
    cis.mockResolvedValue(page(0));
    renderPage();
    expect(await screen.findByTestId("ci-list-empty")).toHaveTextContent("No configuration items found.");
    expect(screen.queryByTestId("ci-list-paging")).toBeNull();
  });

  it("says when the result is truncated", async () => {
    cis.mockResolvedValue(page(3, 3, true));
    renderPage();
    expect(await screen.findByTestId("ci-list-truncated")).toBeInTheDocument();
  });

  it("explains a refused search in plain language", async () => {
    cis.mockRejectedValue(new ApiError(409, { detail: { code: "search_too_broad" } }, "/x"));
    renderPage();
    expect(await screen.findByTestId("ci-list-error")).toHaveTextContent("The search is too broad");
  });
});
