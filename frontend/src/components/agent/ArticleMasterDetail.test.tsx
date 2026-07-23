import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ArticleMasterDetail } from "./ArticleMasterDetail";

const {
  listArticles,
  getArticleBody,
  listAttachments,
  getReplyDraft,
  listTemplates,
  deleteArticle,
} = vi.hoisted(() => ({
  listArticles: vi.fn(),
  getArticleBody: vi.fn(),
  listAttachments: vi.fn(),
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
  deleteArticle: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listArticles,
      getArticleBody,
      listAttachments,
      getReplyDraft,
      listTemplates,
      deleteArticle,
      listQueues: vi.fn().mockResolvedValue([]),
      createArticle: vi.fn().mockResolvedValue({ id: 999 }),
      forwardArticle: vi.fn(),
      bounceArticle: vi.fn(),
      splitArticle: vi.fn(),
    },
  };
});

const ARTICLES = [
  {
    id: 1,
    ticket_id: 7,
    sender_type: "customer",
    sender_type_id: 3,
    communication_channel_id: 1, // Email
    is_visible_for_customer: true,
    create_time: "2024-06-01T10:00:00Z",
    create_by: 10,
    subject: "First",
    from_address: "jane.doe@example.com",
    to_address: "support@example.com",
  },
  {
    id: 2,
    ticket_id: 7,
    sender_type: "agent",
    sender_type_id: 1,
    communication_channel_id: 1, // Email
    is_visible_for_customer: true,
    create_time: "2024-06-01T11:00:00Z",
    create_by: 1,
    subject: "Second",
    from_address: "support@example.com",
    to_address: "jane.doe@example.com",
  },
  {
    id: 3,
    ticket_id: 7,
    sender_type: "agent",
    sender_type_id: 1,
    communication_channel_id: 3, // Internal
    is_visible_for_customer: false,
    create_time: "2024-06-01T12:00:00Z",
    create_by: 1,
    subject: "Note",
    from_address: "aturner",
    to_address: "",
  },
];

// Chat-dominant fixture (customer articles all on the Chat channel, id 4) —
// drives the auto view-switch to "conversation". See lib/articleChannel.ts:
// there's no whatsapp/sms channel row in this system, Chat is the closest
// available proxy.
const CHAT_ARTICLES = [
  {
    id: 10,
    ticket_id: 8,
    sender_type: "customer",
    sender_type_id: 3,
    communication_channel_id: 4, // Chat
    is_visible_for_customer: true,
    create_time: "2024-06-02T09:00:00Z",
    create_by: 20,
    subject: "Hi",
    from_address: "wa-customer@example.com",
    to_address: "support@example.com",
  },
  {
    id: 11,
    ticket_id: 8,
    sender_type: "agent",
    sender_type_id: 1,
    communication_channel_id: 4, // Chat
    is_visible_for_customer: true,
    create_time: "2024-06-02T09:05:00Z",
    create_by: 1,
    subject: "Re: Hi",
    from_address: "support@example.com",
    to_address: "wa-customer@example.com",
  },
];

const BODIES: Record<number, { is_html: boolean; body: string }> = {
  1: { is_html: false, body: "Hello, my printer is offline since this morning." },
  2: { is_html: true, body: "<p>Thanks, we reset the spooler.</p>" },
  3: { is_html: false, body: "Assigned to Level 2." },
  10: { is_html: false, body: "Hey, is anyone there? " + "x".repeat(420) },
  11: { is_html: false, body: "Yes, how can we help?" },
  20: { is_html: false, body: "&gt; Danke &amp; Gruße, das Team" },
};

// Dedicated single-article, email-dominant fixture (ticket 9) for the
// entity-decoding + Gravatar-avatar regression cases below — kept separate
// from ARTICLES so it doesn't shift the "newest selected" default there.
const ENTITY_ARTICLES = [
  {
    id: 20,
    ticket_id: 9,
    sender_type: "customer",
    sender_type_id: 3,
    communication_channel_id: 1, // Email
    is_visible_for_customer: true,
    create_time: "2024-06-03T09:00:00Z",
    create_by: 30,
    subject: "Re: quoted reply",
    from_address: "Jane Doe <jane.doe@example.com>",
    to_address: "support@example.com",
  },
];

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ArticleMasterDetail", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listArticles.mockReset().mockImplementation((ticketId: number) =>
      Promise.resolve(ticketId === 8 ? CHAT_ARTICLES : ticketId === 9 ? ENTITY_ARTICLES : ARTICLES),
    );
    getArticleBody.mockReset().mockImplementation((_ticketId: number, articleId: number) =>
      Promise.resolve({ article_id: articleId, content_type: "text/plain", ...BODIES[articleId] }),
    );
    listAttachments.mockReset().mockResolvedValue([]);
    getReplyDraft.mockReset().mockResolvedValue({
      to_address: "jane.doe@example.com",
      cc: "",
      subject: "Re: Second",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    listTemplates.mockReset().mockResolvedValue([]);
    deleteArticle.mockReset().mockResolvedValue(undefined);
  });

  it("renders one row per article with a preview and defaults to the newest selected", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    expect(await screen.findByTestId("article-list-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("article-list-item-2")).toBeInTheDocument();
    expect(screen.getByTestId("article-list-item-3")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("article-list-item-3")).toHaveTextContent(/Assigned to Level 2/),
    );
    // Newest article (id 3) is selected by default and shown in the reader.
    expect(screen.getByTestId("article-list-item-3")).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByTestId("article-reader")).getByText("Note")).toBeInTheDocument();
  });

  it("switches the visible articles when the filter changes", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");

    fireEvent.click(screen.getByTestId("article-filter-note"));
    expect(screen.queryByTestId("article-list-item-1")).toBeNull();
    expect(screen.queryByTestId("article-list-item-2")).toBeNull();
    expect(screen.getByTestId("article-list-item-3")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("article-filter-email"));
    expect(screen.getByTestId("article-list-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("article-list-item-2")).toBeInTheDocument();
    expect(screen.queryByTestId("article-list-item-3")).toBeNull();
  });

  it("reverses the list order on sort toggle", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");

    const idsInOrder = () =>
      screen
        .getByTestId("article-list")
        .querySelectorAll("[data-testid^='article-list-item-']")
        .length
        ? Array.from(
            screen.getByTestId("article-list").querySelectorAll<HTMLElement>(
              "[data-testid^='article-list-item-']",
            ),
          ).map((el) => el.dataset.testid)
        : [];

    expect(idsInOrder()).toEqual([
      "article-list-item-3",
      "article-list-item-2",
      "article-list-item-1",
    ]);

    fireEvent.click(screen.getByTestId("article-sort-toggle"));

    expect(idsInOrder()).toEqual([
      "article-list-item-1",
      "article-list-item-2",
      "article-list-item-3",
    ]);
  });

  it("updates the reading pane when a different row is selected", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");

    fireEvent.click(screen.getByTestId("article-list-item-1"));
    expect(screen.getByTestId("article-list-item-1")).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByTestId("article-reader")).getByText("First")).toBeInTheDocument();
  });

  it("moves the selection with ArrowDown/ArrowUp on the list", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");
    // Descending default order: 3, 2, 1 — starts on 3.
    expect(screen.getByTestId("article-list-item-3")).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByTestId("article-list"), { key: "ArrowDown" });
    expect(screen.getByTestId("article-list-item-2")).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByTestId("article-list"), { key: "ArrowDown" });
    expect(screen.getByTestId("article-list-item-1")).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByTestId("article-list"), { key: "ArrowUp" });
    expect(screen.getByTestId("article-list-item-2")).toHaveAttribute("aria-selected", "true");
  });

  it("opens the reply dialog for the selected article from the reader pane", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");
    fireEvent.click(screen.getByTestId("article-list-item-2"));

    fireEvent.click(screen.getByTestId("article-reader-reply"));
    expect(await screen.findByTestId("reply-dialog")).toBeInTheDocument();
    await waitFor(() => expect(getReplyDraft).toHaveBeenCalledWith(7, 2, false));
  });
});

describe("ArticleMasterDetail view switching (split vs. conversation)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listArticles.mockReset().mockImplementation((ticketId: number) =>
      Promise.resolve(ticketId === 8 ? CHAT_ARTICLES : ticketId === 9 ? ENTITY_ARTICLES : ARTICLES),
    );
    getArticleBody.mockReset().mockImplementation((_ticketId: number, articleId: number) =>
      Promise.resolve({ article_id: articleId, content_type: "text/plain", ...BODIES[articleId] }),
    );
    listAttachments.mockReset().mockResolvedValue([]);
    getReplyDraft.mockReset().mockResolvedValue({
      to_address: "wa-customer@example.com",
      cc: "",
      subject: "Re: Hi",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    listTemplates.mockReset().mockResolvedValue([]);
    deleteArticle.mockReset().mockResolvedValue(undefined);
  });

  it("auto-selects the split view for an email-dominant ticket, with the Auto badge shown", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-1");
    expect(screen.getByTestId("article-view-tab-split")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("article-view-auto-badge")).toBeInTheDocument();
  });

  it("auto-selects the conversation view for a chat-dominant ticket, with the Auto badge shown", async () => {
    wrap(<ArticleMasterDetail ticketId={8} />);
    expect(await screen.findByTestId("article-conversation")).toBeInTheDocument();
    expect(screen.getByTestId("article-view-tab-conversation")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("article-view-auto-badge")).toBeInTheDocument();
  });

  it("persists a manual view switch per ticket and drops the Auto badge", async () => {
    const first = wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-view-tab-split");
    expect(screen.getByTestId("article-view-auto-badge")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("article-view-tab-conversation"));
    expect(screen.getByTestId("article-view-tab-conversation")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("article-view-auto-badge")).toBeNull();
    first.unmount();

    // Remount (fresh component instance, same ticket) — the manual choice
    // must win over the (still email-dominant, i.e. "split") auto-detection.
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-conversation");
    expect(screen.getByTestId("article-view-tab-conversation")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("article-view-auto-badge")).toBeNull();
  });

  it("renders customer bubbles on the left and agent bubbles on the right", async () => {
    wrap(<ArticleMasterDetail ticketId={8} />);
    await screen.findByTestId("article-conversation");
    expect(screen.getByTestId("conversation-bubble-10")).toHaveAttribute("data-side", "left");
    expect(screen.getByTestId("conversation-bubble-11")).toHaveAttribute("data-side", "right");
  });

  it("resolves a Gravatar avatar for a bubble sender with a real from_address", async () => {
    wrap(<ArticleMasterDetail ticketId={8} />);
    await screen.findByTestId("article-conversation");
    const bubble = screen.getByTestId("conversation-bubble-10");
    // "wa-customer@example.com" (CHAT_ARTICLES id 10) — Avatar prefers
    // Gravatar(email) over the initials fallback once an email is passed.
    expect(within(bubble).getByTestId("avatar-image")).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/),
    );
  });

  it("decodes HTML-escaped entities in the split-view list preview", async () => {
    // The API stores plain-text bodies HTML-escaped — the preview must
    // decode them (decodeEntities), not show literal &gt;/&amp;.
    wrap(<ArticleMasterDetail ticketId={9} />);
    const row = await screen.findByTestId("article-list-item-20");
    expect(row).toHaveTextContent("> Danke & Gruße, das Team");
    expect(row).not.toHaveTextContent("&gt;");
    // Same fixture doubles as the split-view Avatar/Gravatar regression case
    // ("Jane Doe <jane.doe@example.com>" → a resolvable email).
    expect(within(row).getByTestId("avatar-image")).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/www\.gravatar\.com\/avatar\/[0-9a-f]{32}\?/),
    );
  });

  it("expands a long bubble body via 'Show full message'", async () => {
    wrap(<ArticleMasterDetail ticketId={8} />);
    await screen.findByTestId("article-conversation");
    const bubble = screen.getByTestId("conversation-bubble-10");
    expect(within(bubble).getByText(/…$/)).toBeInTheDocument();

    fireEvent.click(within(bubble).getByTestId("conversation-expand-10"));
    expect(within(bubble).getByText(/Hey, is anyone there\? x+$/)).toBeInTheDocument();
  });

  it("expands an internal-note pill on click", async () => {
    const longNote = "Assigned to Level 2 — likely the fuser unit. " + "y".repeat(80);
    getArticleBody.mockImplementation((_ticketId: number, articleId: number) =>
      Promise.resolve(
        articleId === 3
          ? { article_id: 3, content_type: "text/plain", is_html: false, body: longNote }
          : { article_id: articleId, content_type: "text/plain", ...BODIES[articleId] },
      ),
    );
    wrap(<ArticleMasterDetail ticketId={7} />);
    // Manually switch to conversation to see the note pill (email-dominant defaults to split).
    fireEvent.click(await screen.findByTestId("article-view-tab-conversation"));
    const pill = await screen.findByTestId("conversation-note-3");
    // Collapsed: only the first 60 chars.
    expect(within(pill).queryByText(longNote)).toBeNull();
    expect(pill).toHaveTextContent(longNote.slice(0, 60));

    fireEvent.click(within(pill).getByRole("button"));
    expect(within(pill).getByText(longNote)).toBeInTheDocument();
  });

  it("opens the reply dialog for the correct article from a bubble's hover actions", async () => {
    wrap(<ArticleMasterDetail ticketId={8} />);
    await screen.findByTestId("article-conversation");
    const bubble = screen.getByTestId("conversation-bubble-11");
    fireEvent.click(within(bubble).getByTitle("Reply"));
    expect(await screen.findByTestId("reply-dialog")).toBeInTheDocument();
    await waitFor(() => expect(getReplyDraft).toHaveBeenCalledWith(8, 11, false));
  });
});

describe("ArticleMasterDetail — delete internal note", () => {
  beforeEach(() => {
    window.localStorage.clear();
    listArticles.mockReset().mockResolvedValue(ARTICLES);
    getArticleBody.mockReset().mockImplementation((_ticketId: number, articleId: number) =>
      Promise.resolve({ article_id: articleId, content_type: "text/plain", ...BODIES[articleId] }),
    );
    listAttachments.mockReset().mockResolvedValue([]);
    listTemplates.mockReset().mockResolvedValue([]);
    deleteArticle.mockReset().mockResolvedValue(undefined);
  });

  // ARTICLES (ticket 7): article 3 is the internal note (channel 3, not
  // customer-visible) and is selected by default in the split reader pane
  // (newest-first). Article 2 is a customer-visible email.

  it("hides the delete action for an internal note when canDelete is false", async () => {
    wrap(<ArticleMasterDetail ticketId={7} />);
    await screen.findByTestId("article-list-item-3");
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByTestId("article-delete-3")).toBeNull();
  });

  it("shows the delete action for an internal note when canDelete is true and deletes after confirm", async () => {
    wrap(<ArticleMasterDetail ticketId={7} canDelete />);
    await screen.findByTestId("article-list-item-3");
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByTestId("article-delete-3"));

    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(deleteArticle).toHaveBeenCalledWith(7, 3));
    // Success invalidates the articles query — a refetch follows the initial load.
    await waitFor(() => expect(listArticles.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("does not offer delete for a customer-visible article even when canDelete is true", async () => {
    wrap(<ArticleMasterDetail ticketId={7} canDelete />);
    await screen.findByTestId("article-list-item-3");
    fireEvent.click(screen.getByTestId("article-list-item-2"));
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByTestId("article-delete-2")).toBeNull();
  });

  it("shows an error message when delete fails with 409 (not an internal note)", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    deleteArticle.mockRejectedValue(
      new ApiError(409, "Only internal notes can be deleted", "/api/v1/tickets/7/articles/3"),
    );
    wrap(<ArticleMasterDetail ticketId={7} canDelete />);
    await screen.findByTestId("article-list-item-3");
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByTestId("article-delete-3"));

    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByTestId("confirm-dialog-confirm"));

    expect(await screen.findByTestId("article-delete-error-3")).toHaveTextContent(
      "Only internal notes not visible to the customer can be deleted.",
    );
  });

  it("offers delete for an internal note pill in the conversation view", async () => {
    wrap(<ArticleMasterDetail ticketId={7} canDelete />);
    fireEvent.click(await screen.findByTestId("article-view-tab-conversation"));
    const pill = await screen.findByTestId("conversation-note-3");
    fireEvent.click(within(pill).getByTestId("article-delete-3"));

    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(deleteArticle).toHaveBeenCalledWith(7, 3));
  });
});
