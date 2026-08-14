import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { AiOriginToggle, AiOriginTrace } from "./AiOriginBadge";
import { useAiOriginTrace } from "./useAiOriginTrace";

const { getArticleAiOrigin } = vi.hoisted(() => ({
  getArticleAiOrigin: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getArticleAiOrigin } };
});

/** Mirrors how call sites wire the toggle and the trace block together:
 * one shared `useAiOriginTrace` state, rendered as two separate elements. */
function Harness({ ticketId, articleId }: { ticketId: number; articleId: number }) {
  const aiOrigin = useAiOriginTrace({ ticketId, articleId });
  return (
    <div>
      <AiOriginToggle articleId={articleId} open={aiOrigin.open} onToggle={aiOrigin.toggle} />
      <AiOriginTrace articleId={articleId} open={aiOrigin.open} query={aiOrigin.query} />
    </div>
  );
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AiOriginToggle / AiOriginTrace", () => {
  beforeEach(() => {
    getArticleAiOrigin.mockReset();
  });

  it("does not fetch the origin endpoint before it is expanded", () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      audit_run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: [{ name: "kb_search", content: "3 Treffer" }],
    });
    wrap(<Harness ticketId={7} articleId={5} />);
    expect(screen.getByTestId("ai-origin-badge-5")).toBeInTheDocument();
    expect(getArticleAiOrigin).not.toHaveBeenCalled();
  });

  it("renders the trace outside the toggle, without a width clamp, once expanded", async () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      audit_run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: [{ name: "kb_search", content: "3 Treffer" }],
    });
    wrap(<Harness ticketId={7} articleId={5} />);

    fireEvent.click(screen.getByTestId("ai-origin-badge-5"));

    await waitFor(() => expect(getArticleAiOrigin).toHaveBeenCalledWith(7, 5));
    const trace = await screen.findByTestId("ai-origin-trace-5");
    expect(trace.textContent).toContain("kb_search");
    // Regression guard: the expanded trace used to live inside a
    // `min-w-[16rem] max-w-sm` span squeezed into the badge meta row.
    expect(trace.className).not.toMatch(/max-w-sm|min-w-\[16rem\]/);
    expect(screen.queryByTestId("ai-origin-badge-5")?.contains(trace)).toBe(false);
  });

  it("shows an empty-trace message when the origin has no recorded tool calls", async () => {
    getArticleAiOrigin.mockResolvedValue({
      article_id: 5,
      run_id: null,
      audit_run_id: null,
      source: "auto",
      created_at: "2026-08-14T09:00:00",
      tool_trace: null,
    });
    wrap(<Harness ticketId={7} articleId={5} />);

    fireEvent.click(screen.getByTestId("ai-origin-badge-5"));

    expect(await screen.findByText(/no tool calls were recorded/i)).toBeInTheDocument();
  });

  it("renders nothing for AiOriginTrace while collapsed", () => {
    wrap(<Harness ticketId={7} articleId={5} />);
    expect(screen.queryByTestId("ai-origin-trace-5")).not.toBeInTheDocument();
  });
});
