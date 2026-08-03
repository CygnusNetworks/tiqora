import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SimilarTicketsPanel } from "./SimilarTicketsPanel";

const getSimilarTickets = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    params,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
    className?: string;
    "data-testid"?: string;
  }) => (
    <a
      href={`${to}${params ? `/${Object.values(params).join("/")}` : ""}`}
      className={className}
      {...rest}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getSimilarTickets: (...args: unknown[]) => getSimilarTickets(...args),
  },
}));

function renderPanel(ticketId = 42) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SimilarTicketsPanel ticketId={ticketId} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SimilarTicketsPanel", () => {
  beforeEach(() => {
    getSimilarTickets.mockReset();
  });

  it("does not fetch while collapsed", () => {
    renderPanel();
    expect(screen.getByTestId("similar-tickets-panel")).toBeInTheDocument();
    expect(screen.getByTestId("similar-tickets-toggle")).toBeInTheDocument();
    expect(getSimilarTickets).not.toHaveBeenCalled();
    expect(screen.queryByTestId("similar-tickets-body")).toBeNull();
  });

  it("fetches and lists items after expand", async () => {
    getSimilarTickets.mockResolvedValue({
      items: [
        {
          id: 7,
          tn: "20240721000007",
          title: "Related closed issue",
          state: "closed successful",
          queue_name: "Support",
          score: 0.9,
        },
      ],
    });

    renderPanel();
    fireEvent.click(screen.getByTestId("similar-tickets-toggle"));

    await waitFor(() => {
      expect(getSimilarTickets).toHaveBeenCalledWith(42, expect.anything());
    });
    await waitFor(() => {
      expect(screen.getByTestId("similar-tickets-item-7")).toBeInTheDocument();
    });
    expect(screen.getByText("Related closed issue")).toBeInTheDocument();
    expect(screen.getByText("20240721000007")).toBeInTheDocument();
    expect(screen.getByTestId("similar-tickets-score-7")).toHaveTextContent("90%");
  });

  it("hides the score badge when score is missing or zero", async () => {
    getSimilarTickets.mockResolvedValue({
      items: [
        {
          id: 8,
          tn: "20240721000008",
          title: "No score issue",
          state: "closed successful",
          queue_name: "Support",
          score: 0,
        },
      ],
    });

    renderPanel();
    fireEvent.click(screen.getByTestId("similar-tickets-toggle"));

    await waitFor(() => {
      expect(screen.getByTestId("similar-tickets-item-8")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("similar-tickets-score-8")).toBeNull();
  });

  it("shows empty state when no similar tickets", async () => {
    getSimilarTickets.mockResolvedValue({ items: [] });
    renderPanel();
    fireEvent.click(screen.getByTestId("similar-tickets-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("similar-tickets-empty")).toBeInTheDocument();
    });
  });
});
