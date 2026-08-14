import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

function article(id: number, aiOrigin: boolean): ArticleListItem {
  return {
    id,
    ticket_id: 7,
    sender_type: "agent",
    sender_type_id: 1,
    communication_channel_id: 1,
    is_visible_for_customer: true,
    create_time: "2026-07-01T10:00:00Z",
    create_by: 10,
    subject: `Article ${id}`,
    from_address: "agent@example.com",
    to_address: null,
    content_type: null,
    incoming_time: null,
    ai_origin: aiOrigin,
  } as ArticleListItem;
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("AI-origin badge in the conversation view", () => {
  beforeEach(() => {
    getArticleBody.mockReset().mockResolvedValue({
      article_id: 1,
      content_type: "text/plain",
      is_html: false,
      body: "Body",
    });
    getState.mockReset().mockResolvedValue({
      manual_assist_available: false,
      summary_available: false,
      can_summarize: false,
      operation_mode_ready: true,
      drafts: [],
      summary_body: null,
      last_summary_upto_article_id: null,
      summary_created_at: null,
    });
  });

  it("shows the badge only for auto-sent AI articles", () => {
    wrap(
      <ArticleConversationView
        ticketId={7}
        articles={[article(1, true), article(2, false)]}
        canNote
        locale="de"
      />,
    );
    expect(screen.getByTestId("ai-origin-badge-1")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-origin-badge-2")).toBeNull();
  });
});
