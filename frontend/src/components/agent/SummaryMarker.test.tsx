import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ArticleConversationView } from "./ArticleConversationView";
import type { ArticleListItem } from "@/lib/api";

const { getArticleBody, getState } = vi.hoisted(() => ({
  getArticleBody: vi.fn(),
  getState: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getArticleBody } };
});

vi.mock("@/lib/ticketAiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ticketAiApi")>("@/lib/ticketAiApi");
  return { ...actual, ticketAiApi: { getState } };
});

function article(id: number): ArticleListItem {
  return {
    id,
    ticket_id: 7,
    sender_type: "customer",
    sender_type_id: 3,
    communication_channel_id: 1,
    is_visible_for_customer: true,
    create_time: "2026-07-01T10:00:00Z",
    create_by: 10,
    subject: `Article ${id}`,
    from_address: "jane@example.com",
    to_address: null,
    content_type: null,
    incoming_time: null,
  } as ArticleListItem;
}

const AI_STATE = {
  manual_assist_available: false,
  summary_available: true,
  can_summarize: true,
  operation_mode_ready: true,
  drafts: [],
  summary_body: "Summary text",
  last_summary_upto_article_id: 2,
  summary_created_at: "2026-07-23T09:21:00",
};

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SummaryMarker in the conversation view", () => {
  beforeEach(() => {
    getArticleBody.mockReset().mockResolvedValue({
      article_id: 1,
      content_type: "text/plain",
      is_html: false,
      body: "Body",
    });
    getState.mockReset();
  });

  it("renders the marker after the newest summarized article", async () => {
    getState.mockResolvedValue(AI_STATE);
    wrap(
      <ArticleConversationView
        ticketId={7}
        articles={[article(1), article(2), article(3)]}
        canNote
        locale="de"
      />,
    );

    const marker = await screen.findByTestId("summary-marker");
    expect(marker.textContent).toContain("2026");
    // Boundary article 2 comes before the marker, article 3 after it.
    const container = screen.getByTestId("article-conversation");
    const order = [...container.querySelectorAll("[data-testid]")].map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(order.indexOf("conversation-bubble-2")).toBeLessThan(order.indexOf("summary-marker"));
    expect(order.indexOf("summary-marker")).toBeLessThan(order.indexOf("conversation-bubble-3"));
  });

  it("renders no marker when there is no summary", async () => {
    getState.mockResolvedValue({ ...AI_STATE, summary_body: null, last_summary_upto_article_id: null });
    wrap(
      <ArticleConversationView ticketId={7} articles={[article(1), article(2)]} canNote locale="de" />,
    );
    await waitFor(() => expect(getState).toHaveBeenCalled());
    expect(screen.queryByTestId("summary-marker")).toBeNull();
  });
});
