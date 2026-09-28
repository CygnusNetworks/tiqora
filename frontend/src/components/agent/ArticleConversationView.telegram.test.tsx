import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError, type ArticleListItem, type TelegramChatOut } from "@/lib/api";
import { ArticleConversationView } from "./ArticleConversationView";
import { useComposerRequests } from "./telegram/composerBus";
import { ticketDraftsKey, type ReplyDraft } from "@/lib/replyDrafts";

const api = vi.hoisted(() => ({
  getArticleBody: vi.fn(),
  getTelegramChat: vi.fn(),
  listAttachments: vi.fn(),
  editTelegramMessage: vi.fn(),
  retractTelegramMessage: vi.fn(),
  removeTelegramButtons: vi.fn(),
  attachmentDownloadUrl: vi.fn(() => "/att"),
}));
const { getState } = vi.hoisted(() => ({ getState: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api };
});

vi.mock("@/lib/ticketAiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ticketAiApi")>("@/lib/ticketAiApi");
  return { ...actual, ticketAiApi: { getState } };
});

function article(id: number, sender: "agent" | "customer", channel = "Telegram"): ArticleListItem {
  return {
    id,
    ticket_id: 7,
    sender_type: sender,
    sender_type_id: sender === "agent" ? 1 : 3,
    communication_channel_id: 9,
    communication_channel_name: channel,
    is_visible_for_customer: true,
    create_time: `2026-09-26T14:0${id}:00Z`,
    create_by: 10,
    subject: `Article ${id}`,
    from_address: sender === "agent" ? "Kim" : "Jona M.",
    to_address: null,
    content_type: null,
    incoming_time: null,
    ai_origin: false,
  } as ArticleListItem;
}

const BODIES: Record<number, string> = {
  1: "Ich bekomme immer diesen Fehler",
  2: "Welche WP-Nummer hast du?",
  3: "Diese Nachricht ist weg",
};

function chat(): TelegramChatOut {
  return {
    chat_id: 5550001,
    username: "jona_m",
    display_name: "Jona M.",
    identity_verified: false,
    customer_user_login: null,
    consent_time: "2026-09-26T13:59:00Z",
    ai_escalated_at: null,
    messages: [
      {
        article_id: 1,
        direction: "in",
        reply_to_article_id: null,
        buttons: [],
        answered_button: null,
        edited_at: null,
        retracted_at: null,
        editable: false,
      },
      {
        article_id: 2,
        direction: "out",
        reply_to_article_id: 1,
        buttons: [
          { label: "Ja", action: "resolve_yes" },
          { label: "Nein", action: "resolve_no" },
        ],
        answered_button: 0,
        edited_at: "2026-09-26T14:03:00Z",
        retracted_at: null,
        editable: true,
      },
      {
        article_id: 3,
        direction: "out",
        reply_to_article_id: null,
        buttons: [],
        answered_button: null,
        edited_at: null,
        retracted_at: "2026-09-26T14:04:00Z",
        editable: false,
      },
      {
        // Attachment-only reply: its message is the file itself.
        article_id: 4,
        direction: "out",
        reply_to_article_id: null,
        buttons: [],
        answered_button: null,
        edited_at: null,
        retracted_at: null,
        editable: false,
      },
    ],
  };
}

function setup(
  articles: ArticleListItem[],
  canNote = true,
  opts: { newestFirst?: boolean; drafts?: ReplyDraft[] } = {},
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (opts.drafts) qc.setQueryData(ticketDraftsKey(7), opts.drafts);
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const ui = (list: ArticleListItem[], newestFirst = opts.newestFirst ?? false) => (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ArticleConversationView
          ticketId={7}
          articles={list}
          canNote={canNote}
          locale="de"
          newestFirst={newestFirst}
          composer={<div data-testid="fake-composer" />}
        />
      </I18nextProvider>
    </QueryClientProvider>
  );
  const r = render(ui(articles));
  return {
    ...r,
    invalidate,
    rerenderWith: (list: ArticleListItem[], newestFirst?: boolean) =>
      r.rerender(ui(list, newestFirst)),
  };
}

/** data-testids of the thread's rows (bubbles, day labels, marker, drafts)
 * plus header/composer, in document order. */
function readingOrder(): string[] {
  return Array.from(
    document.querySelectorAll(
      '[data-testid^="conversation-bubble-"], [data-testid="conversation-day"], [data-testid="summary-marker"], [data-testid^="conversation-draft-"], [data-testid="telegram-contact-header"], [data-testid="fake-composer"], [data-testid="article-conversation"]',
    ),
  )
    .map((el) => el.getAttribute("data-testid") ?? "")
    .filter((id) => !id.startsWith("conversation-draft-edit-"));
}

function dayArticle(id: number, sender: "agent" | "customer", createTime: string): ArticleListItem {
  return { ...article(id, sender), create_time: createTime };
}

const DRAFT: ReplyDraft = {
  ticketId: 7,
  articleId: 3,
  replyAll: false,
  subject: "",
  body: "Entwurf",
  to: "",
  cc: "",
  bcc: "",
  replyTo: "",
  aiDraftId: null,
  updatedAt: Date.parse("2026-09-27T10:00:00Z"),
};

const THREAD = [article(1, "customer"), article(2, "agent"), article(3, "agent")];

describe("ArticleConversationView on a Telegram ticket", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    api.getArticleBody.mockReset().mockImplementation((_t: number, aid: number) =>
      Promise.resolve({ article_id: aid, content_type: "text/plain", is_html: false, body: BODIES[aid] ?? "" }),
    );
    api.getTelegramChat.mockReset().mockResolvedValue(chat());
    api.listAttachments.mockReset().mockResolvedValue([]);
    api.editTelegramMessage.mockReset().mockResolvedValue(undefined);
    api.retractTelegramMessage.mockReset().mockResolvedValue(undefined);
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
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the contact header and the per-message extras", async () => {
    setup(THREAD);
    expect(await screen.findByTestId("telegram-contact-header")).toHaveTextContent("Jona M.");
    expect(api.getTelegramChat).toHaveBeenCalledWith(7, expect.anything());

    const b2 = screen.getByTestId("conversation-bubble-2");
    expect(within(b2).getByTestId("telegram-quote-1")).toHaveTextContent("Antwort auf Jona M.");
    expect(await within(b2).findByText(BODIES[1])).toBeInTheDocument();
    expect(within(b2).getByText("bearbeitet")).toBeInTheDocument();
    expect(within(b2).getByTestId("telegram-delivered")).toBeInTheDocument();
    expect(within(b2).getByTestId("telegram-button-0")).toHaveAttribute("data-answered", "true");

    const b3 = screen.getByTestId("conversation-bubble-3");
    expect(within(b3).getByText("zurückgezogen")).toBeInTheDocument();
    expect(within(b3).getByTestId("telegram-retracted-body")).toHaveClass("line-through");
    expect(within(b3).queryByTestId("telegram-delivered")).toBeNull();

    const b1 = screen.getByTestId("conversation-bubble-1");
    expect(within(b1).queryByTestId("telegram-delivered")).toBeNull();
    // Attachments render inline inside the bubbles.
    await waitFor(() => expect(api.listAttachments).toHaveBeenCalledWith(7, 1));
  });

  it("keeps the scroll container when the header arrives", async () => {
    setup(THREAD);
    const before = screen.getByTestId("article-conversation");
    await screen.findByTestId("telegram-contact-header");
    expect(screen.getByTestId("article-conversation")).toBe(before);
  });

  it("offers edit/retract only on delivered agent messages", async () => {
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b1 = screen.getByTestId("conversation-bubble-1");
    const b2 = screen.getByTestId("conversation-bubble-2");
    const b3 = screen.getByTestId("conversation-bubble-3");
    expect(within(b1).getByRole("button", { name: "Zitieren" })).toBeInTheDocument();
    expect(within(b1).queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(within(b2).getByRole("button", { name: "Bearbeiten" })).toBeInTheDocument();
    expect(within(b2).getByRole("button", { name: "Zurückziehen" })).toBeInTheDocument();
    expect(within(b3).queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(within(b3).queryByRole("button", { name: "Zurückziehen" })).toBeNull();
  });

  it("offers no edit on an attachment-only message, only retract", async () => {
    setup([...THREAD, article(4, "agent")]);
    await screen.findByTestId("telegram-contact-header");
    const b4 = screen.getByTestId("conversation-bubble-4");
    expect(within(b4).queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(within(b4).getByRole("button", { name: "Zurückziehen" })).toBeInTheDocument();
  });

  it("never shows a validation list or a proxy page as the action error", async () => {
    api.editTelegramMessage.mockRejectedValue(
      new ApiError(422, { detail: [{ type: "string_too_long", loc: ["body", "body"], msg: "too long" }] }, "/x"),
    );
    api.retractTelegramMessage.mockRejectedValue(
      new ApiError(502, "<html><body>502 Bad Gateway</body></html>", "/x"),
    );
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    await within(b2).findByText(BODIES[2]);
    fireEvent.click(within(b2).getByRole("button", { name: "Bearbeiten" }));
    fireEvent.change(within(b2).getByRole("textbox"), { target: { value: "Neu" } });
    fireEvent.click(within(b2).getByRole("button", { name: "Speichern" }));
    const editError = await within(b2).findByTestId("telegram-action-error-2");
    expect(editError).not.toHaveTextContent("[object Object]");
    expect(editError).toHaveTextContent(i18n.t("ticket.telegram.actionError"));

    fireEvent.click(within(b2).getByRole("button", { name: "Abbrechen" }));
    fireEvent.click(within(b2).getByRole("button", { name: "Zurückziehen" }));
    fireEvent.click(within(b2).getByRole("button", { name: "Ja" }));
    await waitFor(() => expect(api.retractTelegramMessage).toHaveBeenCalled());
    const retractError = await within(b2).findByTestId("telegram-action-error-2");
    expect(retractError).not.toHaveTextContent("html");
    expect(retractError).toHaveTextContent(i18n.t("ticket.telegram.actionError"));
  });

  it("quote asks the composer to quote that message", async () => {
    const handler = vi.fn();
    function Listener() {
      useComposerRequests(7, handler);
      return null;
    }
    render(<Listener />);
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    fireEvent.click(
      within(screen.getByTestId("conversation-bubble-1")).getByRole("button", { name: "Zitieren" }),
    );
    expect(handler).toHaveBeenCalledWith({ quoteArticleId: 1, focus: true });
  });

  it("edits a message inline and refreshes articles and chat", async () => {
    const { invalidate } = setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    await within(b2).findByText(BODIES[2]);
    fireEvent.click(within(b2).getByRole("button", { name: "Bearbeiten" }));
    const box = within(b2).getByRole("textbox");
    expect(box).toHaveValue(BODIES[2]);
    fireEvent.change(box, { target: { value: "Welche PKZ hast du?" } });
    fireEvent.click(within(b2).getByRole("button", { name: "Speichern" }));
    await waitFor(() =>
      expect(api.editTelegramMessage).toHaveBeenCalledWith(7, 2, "Welche PKZ hast du?"),
    );
    await waitFor(() => expect(within(b2).queryByRole("textbox")).toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "articles"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "telegram"] });
  });

  it("retracts only after the inline confirmation", async () => {
    const { invalidate } = setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    const confirmSpy = vi.spyOn(window, "confirm");
    fireEvent.click(within(b2).getByRole("button", { name: "Zurückziehen" }));
    expect(api.retractTelegramMessage).not.toHaveBeenCalled();
    expect(within(b2).getByText("Wirklich zurückziehen?")).toBeInTheDocument();
    fireEvent.click(within(b2).getByRole("button", { name: "Ja" }));
    await waitFor(() => expect(api.retractTelegramMessage).toHaveBeenCalledWith(7, 2));
    await waitFor(() => expect(within(b2).queryByText("Wirklich zurückziehen?")).toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "telegram"] });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("surfaces the readable 409 reason when Telegram refuses the retract", async () => {
    api.retractTelegramMessage.mockRejectedValue(
      new ApiError(
        409,
        { detail: "Telegram lässt das Löschen nach 48 Stunden nicht mehr zu." },
        "/api/v1/tickets/7/articles/2/telegram/retract",
      ),
    );
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    fireEvent.click(within(b2).getByRole("button", { name: "Zurückziehen" }));
    fireEvent.click(within(b2).getByRole("button", { name: "Ja" }));
    expect(await within(b2).findByTestId("telegram-action-error-2")).toHaveTextContent(
      "Telegram lässt das Löschen nach 48 Stunden nicht mehr zu.",
    );
  });

  it("renders no header and no extras when the ticket has no Telegram chat", async () => {
    api.getTelegramChat.mockRejectedValue(
      new ApiError(404, { detail: "Not Found" }, "/api/v1/tickets/7/telegram"),
    );
    setup(THREAD);
    await waitFor(() => expect(api.getTelegramChat).toHaveBeenCalled());
    await within(screen.getByTestId("conversation-bubble-1")).findByText(BODIES[1]);
    expect(screen.queryByTestId("telegram-contact-header")).toBeNull();
    expect(screen.queryByRole("button", { name: "Zitieren" })).toBeNull();
    expect(screen.queryByText("zurückgezogen")).toBeNull();
  });

  it("does not query the chat for non-Telegram tickets", () => {
    setup([article(1, "customer", "Email"), article(2, "agent", "Email")]);
    expect(api.getTelegramChat).not.toHaveBeenCalled();
    expect(screen.queryByTestId("telegram-contact-header")).toBeNull();
  });

  it("newest-first: composer under the header, newest day and message on top, day label heads its block", async () => {
    getState.mockResolvedValue({
      manual_assist_available: false,
      summary_available: true,
      can_summarize: false,
      operation_mode_ready: true,
      drafts: [],
      summary_body: "Zusammenfassung",
      last_summary_upto_article_id: 2,
      summary_created_at: "2026-09-27T10:00:00Z",
    });
    const list = [
      dayArticle(1, "customer", "2026-09-26T09:00:00Z"),
      dayArticle(2, "agent", "2026-09-26T10:00:00Z"),
      dayArticle(3, "customer", "2026-09-28T09:00:00Z"),
    ];
    setup(list, true, { newestFirst: true, drafts: [DRAFT] });
    await screen.findByTestId("telegram-contact-header");
    await screen.findByTestId("summary-marker");
    await screen.findByTestId("conversation-draft-3");
    expect(readingOrder()).toEqual([
      "telegram-contact-header",
      "fake-composer",
      "article-conversation",
      "conversation-draft-3",
      "conversation-day",
      "conversation-bubble-3",
      "conversation-day",
      // Directly above the newest summarized message.
      "summary-marker",
      "conversation-bubble-2",
      "conversation-bubble-1",
    ]);
    const days = screen.getAllByTestId("conversation-day").map((d) => d.textContent);
    expect(days[0]).toMatch(/28/);
    expect(days[1]).toMatch(/26/);
    expect(screen.getByTestId("article-conversation")).toHaveAttribute("data-order", "newest-first");
  });

  it("oldest-first keeps the chronological layout with the composer below", async () => {
    getState.mockResolvedValue({
      manual_assist_available: false,
      summary_available: true,
      can_summarize: false,
      operation_mode_ready: true,
      drafts: [],
      summary_body: "Zusammenfassung",
      last_summary_upto_article_id: 2,
      summary_created_at: "2026-09-27T10:00:00Z",
    });
    setup(THREAD, true, { drafts: [DRAFT] });
    await screen.findByTestId("telegram-contact-header");
    await screen.findByTestId("summary-marker");
    await screen.findByTestId("conversation-draft-3");
    expect(readingOrder()).toEqual([
      "telegram-contact-header",
      "article-conversation",
      "conversation-day",
      "conversation-bubble-1",
      "conversation-bubble-2",
      "summary-marker",
      "conversation-bubble-3",
      "conversation-draft-3",
      "fake-composer",
    ]);
  });

  it("newest-first doesn't scroll to the bottom; flipping the order keeps the composer mounted", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    const { rerenderWith } = setup(THREAD.slice(0, 2), true, { newestFirst: true });
    await screen.findByTestId("telegram-contact-header");
    const composer = screen.getByTestId("fake-composer");
    const thread = screen.getByTestId("article-conversation");
    thread.scrollTop = 120;
    rerenderWith(THREAD, true);
    expect(scroll).not.toHaveBeenCalled();
    expect(thread.scrollTop).toBe(0);

    rerenderWith(THREAD, false);
    expect(scroll).toHaveBeenCalled();
    expect(screen.getByTestId("fake-composer")).toBe(composer);
    expect(screen.getByTestId("article-conversation")).toBe(thread);
  });

  it("removes the buttons of an own message with an open keyboard", async () => {
    const open = chat();
    open.messages[1] = { ...open.messages[1], answered_button: null };
    api.getTelegramChat.mockResolvedValue(open);
    api.removeTelegramButtons.mockReset().mockResolvedValue(undefined);
    const { invalidate } = setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    fireEvent.click(await within(b2).findByRole("button", { name: "Buttons entfernen" }));
    await waitFor(() => expect(api.removeTelegramButtons).toHaveBeenCalledWith(7, 2));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tickets", 7, "telegram"] }),
    );
  });

  it("offers no button removal once answered, without buttons or on a customer message", async () => {
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    // Article 2 has an answered keyboard, 1 is the customer's, 3 is retracted.
    for (const id of [1, 2, 3]) {
      expect(
        within(screen.getByTestId(`conversation-bubble-${id}`)).queryByRole("button", {
          name: "Buttons entfernen",
        }),
      ).toBeNull();
    }
  });

  it("shows Telegram's reason when the button removal is refused", async () => {
    const open = chat();
    open.messages[1] = { ...open.messages[1], answered_button: null };
    api.getTelegramChat.mockResolvedValue(open);
    api.removeTelegramButtons
      .mockReset()
      .mockRejectedValue(new ApiError(409, { detail: "Bad Request: chat not found" }, "/x"));
    setup(THREAD);
    await screen.findByTestId("telegram-contact-header");
    const b2 = screen.getByTestId("conversation-bubble-2");
    fireEvent.click(await within(b2).findByRole("button", { name: "Buttons entfernen" }));
    expect(await within(b2).findByTestId("telegram-action-error-2")).toHaveTextContent(
      "Bad Request: chat not found",
    );
  });

  it("scrolls to the bottom when a newer message arrives", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    const { rerenderWith } = setup(THREAD.slice(0, 2));
    await screen.findByTestId("telegram-contact-header");
    const before = scroll.mock.calls.length;
    rerenderWith(THREAD);
    expect(scroll.mock.calls.length).toBeGreaterThan(before);
  });
});
