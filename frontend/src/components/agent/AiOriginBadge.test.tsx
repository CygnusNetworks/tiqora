import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { AiOriginBadge } from "./AiOriginBadge";

const { getArticleAiOrigin } = vi.hoisted(() => ({
  getArticleAiOrigin: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getArticleAiOrigin } };
});

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiOriginBadge", () => {
  beforeEach(() => {
    getArticleAiOrigin.mockReset();
  });

  it("does not fetch the origin endpoint before it is expanded", () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: [{ name: "kb_search", content: "3 Treffer" }],
    });
    wrap(<AiOriginBadge ticketId={7} articleId={5} />);
    expect(screen.getByTestId("ai-origin-badge-5")).toBeInTheDocument();
    expect(getArticleAiOrigin).not.toHaveBeenCalled();
  });

  it("lazily loads and renders the tool trace on expand", async () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: [{ name: "kb_search", content: "3 Treffer" }],
    });
    wrap(<AiOriginBadge ticketId={7} articleId={5} />);

    fireEvent.click(screen.getByTestId("ai-origin-badge-5"));

    await waitFor(() => expect(getArticleAiOrigin).toHaveBeenCalledWith(7, 5));
    const trace = await screen.findByTestId("ai-origin-trace-5");
    expect(trace.textContent).toContain("kb_search");
  });

  it("shows an empty-trace message when the origin has no recorded tool calls", async () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: null,
    });
    wrap(<AiOriginBadge ticketId={7} articleId={5} />);

    fireEvent.click(screen.getByTestId("ai-origin-badge-5"));

    expect(await screen.findByText(/no tool calls were recorded/i)).toBeInTheDocument();
  });
});
