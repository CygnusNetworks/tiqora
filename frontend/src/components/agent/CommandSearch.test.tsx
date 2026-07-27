import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CommandSearch } from "./CommandSearch";

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

const search = vi.fn();
const listReferenceQueues = vi.fn();
const listReferenceAgents = vi.fn();
const customerQuickSearch = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    search: (...a: unknown[]) => search(...a),
    listReferenceQueues: (...a: unknown[]) => listReferenceQueues(...a),
    listReferenceAgents: (...a: unknown[]) => listReferenceAgents(...a),
    customerQuickSearch: (...a: unknown[]) => customerQuickSearch(...a),
  },
}));

function renderSearch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CommandSearch />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function flushRaf() {
  await act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
  });
}

const HIT = {
  id: 100,
  tn: "20240101000100",
  title: "Printer offline",
  queue_id: 1,
  queue_name: "Support",
  state: "open",
  state_type: "open",
  priority: "3 normal",
  owner_login: "a1",
  customer_id: null,
  create_time: null,
  change_time: null,
};

describe("CommandSearch", () => {
  beforeEach(() => {
    navigate.mockClear();
    listReferenceQueues.mockReset().mockResolvedValue([]);
    listReferenceAgents.mockReset().mockResolvedValue([]);
    customerQuickSearch.mockReset().mockResolvedValue({ companies: [], contacts: [] });
    search.mockReset().mockResolvedValue({ query: "", hits: [HIT], estimated_total: 1, facets: {} });
    void i18n.changeLanguage("en");
  });

  it("is closed by default and opens via the trigger button", () => {
    renderSearch();
    expect(screen.queryByTestId("command-search-input")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    expect(screen.getByTestId("command-search-input")).toBeInTheDocument();
  });

  it("opens on Cmd+K and Ctrl+K but not plain 'k'", () => {
    renderSearch();
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.queryByTestId("command-search-input")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByTestId("command-search-input")).toBeInTheDocument();
  });

  it("focuses the input once the dialog opens", async () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    await flushRaf();
    expect(screen.getByTestId("command-search-input")).toHaveFocus();
  });

  it("shows live results while typing and opens the ticket on click", async () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    fireEvent.change(screen.getByTestId("command-search-input"), { target: { value: "printer" } });
    const hit = await screen.findByTestId("command-search-hit-100");
    expect(hit).toHaveTextContent("Printer offline");
    fireEvent.click(hit);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/$ticketId",
      params: { ticketId: "100" },
    });
    expect(screen.queryByTestId("command-search-input")).not.toBeInTheDocument();
  });

  it("navigates to the full search page on Enter and closes", async () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    const input = screen.getByTestId("command-search-input");
    fireEvent.change(input, { target: { value: "printer" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agent/search",
        search: expect.objectContaining({ q: "printer" }),
      }),
    );
    expect(screen.queryByTestId("command-search-input")).not.toBeInTheDocument();
  });

  it("resets the composed query after closing", async () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    fireEvent.change(screen.getByTestId("command-search-input"), { target: { value: "leftover" } });
    fireEvent.keyDown(screen.getByTestId("command-search-input"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByTestId("command-search-input")).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    expect(screen.getByTestId("command-search-input")).toHaveValue("");
  });
});
