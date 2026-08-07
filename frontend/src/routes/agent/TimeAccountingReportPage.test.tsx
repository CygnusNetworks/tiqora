import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { rangeForPreset } from "@/lib/dateRange";
import { TimeAccountingReportPage, type TimeAccountingSearch } from "./TimeAccountingReportPage";

const navigate = vi.fn();
let searchParams: TimeAccountingSearch = {};

const listTimeAccountingReport = vi.fn();
const listReferenceAgents = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams,
  Link: ({
    children,
    to,
    params,
    className,
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
    className?: string;
  }) => (
    <a
      href={`${to}${params ? `/${Object.values(params).join("/")}` : ""}`}
      className={className}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  api: {
    listTimeAccountingReport: (...args: unknown[]) => listTimeAccountingReport(...args),
    listReferenceAgents: (...args: unknown[]) => listReferenceAgents(...args),
  },
}));

function row(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    ticket_id: 42,
    ticket_tn: "20260101000042",
    ticket_title: "Printer jam",
    article_id: null,
    time_unit: 1.5,
    create_time: "2026-08-04T09:30:00",
    create_by: 5,
    create_by_login: "agent1",
    ...over,
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TimeAccountingReportPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

/** Resolve the search patch a setSearch() call produced. */
function lastSearchPatch(): TimeAccountingSearch {
  const call = navigate.mock.calls.at(-1)?.[0] as {
    search: (prev: TimeAccountingSearch) => TimeAccountingSearch;
  };
  return call.search(searchParams);
}

describe("TimeAccountingReportPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    searchParams = {};
    listReferenceAgents.mockReset().mockResolvedValue([]);
    listTimeAccountingReport.mockReset().mockResolvedValue({
      items: [],
      total_units: 0,
      offset: 0,
      limit: 100,
    });
  });

  it("keeps the documented filter test ids", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("ta-total-units")).toBeInTheDocument());
    for (const id of [
      "time-accounting-report",
      "ta-filter-user",
      "ta-filter-from",
      "ta-filter-to",
      "ta-filter-ticket",
      "ta-filter-clear",
    ]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it("writes the preset range into the URL search", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("ta-preset-thisMonth")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("ta-preset-thisMonth"));

    const expected = rangeForPreset("thisMonth");
    expect(lastSearchPatch()).toMatchObject({
      created_from: expected.from,
      created_to: expected.to,
    });
  });

  it("marks the preset that matches the current range", async () => {
    const { from, to } = rangeForPreset("lastMonth");
    searchParams = { created_from: from, created_to: to };
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ta-preset-lastMonth")).toBeInTheDocument());
    expect(screen.getByTestId("ta-preset-lastMonth")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("ta-preset-thisMonth")).toHaveAttribute("aria-pressed", "false");
  });

  it("marks no preset for a hand-picked range", async () => {
    searchParams = { created_from: "2026-05-03", created_to: "2026-05-17" };
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ta-presets")).toBeInTheDocument());
    const pressed = screen
      .queryAllByRole("button", { pressed: true })
      .filter((b) => b.dataset.testid?.startsWith("ta-preset-"));
    expect(pressed).toHaveLength(0);
  });

  it("renders key figures and per-day groups", async () => {
    listTimeAccountingReport.mockResolvedValue({
      items: [
        row({ id: 1, ticket_id: 42, create_by: 5, time_unit: 1.5, create_time: "2026-08-04T09:30:00" }),
        row({ id: 2, ticket_id: 42, create_by: 5, time_unit: 0.5, create_time: "2026-08-04T14:00:00" }),
        row({ id: 3, ticket_id: 77, create_by: 9, time_unit: 2, create_time: "2026-08-05T08:00:00" }),
      ],
      total_units: 4,
      offset: 0,
      limit: 100,
    });
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ta-table")).toBeInTheDocument());
    expect(screen.getByTestId("ta-total-units")).toHaveTextContent("4.00");
    expect(screen.getByTestId("ta-kpi-entries")).toHaveTextContent("3");
    expect(screen.getByTestId("ta-kpi-tickets")).toHaveTextContent("2");
    expect(screen.getByTestId("ta-kpi-agents")).toHaveTextContent("2");
    // One tbody per calendar day, each with a day-total header row.
    expect(screen.getByTestId("ta-table").querySelectorAll("tbody")).toHaveLength(2);
    expect(screen.getByTestId("ta-chart")).toBeInTheDocument();
  });

  it("omits the chart when all rows fall on one day", async () => {
    listTimeAccountingReport.mockResolvedValue({
      items: [row({ id: 1 }), row({ id: 2, create_time: "2026-08-04T16:00:00" })],
      total_units: 3,
      offset: 0,
      limit: 100,
    });
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ta-table")).toBeInTheDocument());
    expect(screen.queryByTestId("ta-chart")).toBeNull();
  });

  it("offers a reset in the empty state only while filters are set", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(i18n.t("timeAccounting.empty"))).toBeInTheDocument());
    expect(screen.queryByTestId("ta-empty-clear")).toBeNull();

    searchParams = { ticket_id: 42 };
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId("ta-empty-clear")).not.toHaveLength(0));
  });

  it("shows the loaded row range in the pager", async () => {
    searchParams = { offset: 100 };
    listTimeAccountingReport.mockResolvedValue({
      items: [row({ id: 1 }), row({ id: 2 })],
      total_units: 3,
      offset: 100,
      limit: 100,
    });
    renderPage();

    await waitFor(() => expect(screen.getByTestId("ta-page-range")).toHaveTextContent("101–102"));
    expect(screen.getByTestId("ta-page-prev")).toBeEnabled();
    expect(screen.getByTestId("ta-page-next")).toBeDisabled();
  });
});
