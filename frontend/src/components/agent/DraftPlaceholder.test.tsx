import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { getDraft, useTicketReplyDrafts, type ReplyDraft } from "@/lib/replyDrafts";
import { DraftListRow, DraftBubble } from "./DraftPlaceholder";
import { ArticleQuickActions } from "./ArticleQuickActions";

const { getReplyDraft, listTemplates, formDrafts } = vi.hoisted(() => ({
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
  formDrafts: { list: vi.fn(), upsert: vi.fn(), remove: vi.fn() },
}));

// Drafts live on the server; serve them from an in-memory stand-in.
vi.mock("@/lib/formDraftApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/formDraftApi")>("@/lib/formDraftApi");
  return { ...actual, formDraftApi: formDrafts };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      getReplyDraft,
      listTemplates,
      listQueues: vi.fn().mockResolvedValue([]),
      createArticle: vi.fn().mockResolvedValue({ id: 999 }),
    },
  };
});

const ARTICLE = {
  id: 2,
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
};

const DRAFT_BODY =
  "Thanks for reaching out, we will look into it.\n\n> On 2024-06-01, Jane wrote:\n> My printer is offline.";

let qc: QueryClient;

function wrap(ui: React.ReactElement) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const DRAFT_CONTENT = {
  replyAll: false,
  subject: "Re: First",
  body: DRAFT_BODY,
  to: "jane.doe@example.com",
  cc: "",
  bcc: "",
  replyTo: "",
  aiDraftId: null,
};

/** The draft as the two placeholders receive it (a plain prop). */
function makeDraft(): ReplyDraft {
  return { ticketId: 7, articleId: 2, ...DRAFT_CONTENT, updatedAt: Date.now() };
}

/** Same draft, but served by the API so the hooks pick it up. */
function serveDraft() {
  formDrafts.list.mockResolvedValue([
    {
      id: 1,
      ticket_id: 7,
      user_id: 1,
      action: "AgentTicketCompose",
      article_id: 2,
      title: null,
      content: JSON.stringify(DRAFT_CONTENT),
      created: "2026-08-07T10:00:00",
      changed: "2026-08-07T10:00:00",
    },
  ]);
}

describe("DraftPlaceholder", () => {
  beforeEach(() => {
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    formDrafts.list.mockReset().mockResolvedValue([]);
    formDrafts.upsert.mockReset();
    formDrafts.remove.mockReset().mockResolvedValue(undefined);
    getReplyDraft.mockReset().mockResolvedValue({
      to_address: "jane.doe@example.com",
      cc: "",
      subject: "Re: First",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    listTemplates.mockReset().mockResolvedValue([]);
  });

  it("DraftListRow renders the unsent label and a preview of only the reply text", () => {
    const draft = makeDraft();
    wrap(<DraftListRow draft={draft} locale="en" />);

    const row = screen.getByTestId("article-draft-row-2");
    expect(row).toHaveTextContent("Draft — not sent");
    expect(row).toHaveTextContent("Thanks for reaching out, we will look into it.");
    // Quoted original must not leak into the preview.
    expect(row).not.toHaveTextContent("My printer is offline.");
  });

  it("DraftBubble renders label, 'not sent' marker and preview", () => {
    const draft = makeDraft();
    wrap(<DraftBubble draft={draft} locale="en" />);

    const bubble = screen.getByTestId("conversation-draft-2");
    expect(within(bubble).getByText("Draft")).toBeInTheDocument();
    expect(within(bubble).getByText("not sent")).toBeInTheDocument();
    expect(bubble).toHaveTextContent("Thanks for reaching out, we will look into it.");
    expect(bubble).not.toHaveTextContent("My printer is offline.");
  });

  it("clicking the DraftListRow opens the reply dialog", async () => {
    const draft = makeDraft();
    wrap(<DraftListRow draft={draft} locale="en" />);

    fireEvent.click(screen.getByTestId("article-draft-row-2"));
    expect(await screen.findByTestId("reply-dialog")).toBeInTheDocument();
    await waitFor(() => expect(getReplyDraft).toHaveBeenCalledWith(7, 2, false));
  });

  it("clicking the DraftBubble's edit button opens the reply dialog", async () => {
    const draft = makeDraft();
    wrap(<DraftBubble draft={draft} locale="en" />);

    fireEvent.click(screen.getByTestId("conversation-draft-edit-2"));
    expect(await screen.findByTestId("reply-dialog")).toBeInTheDocument();
    await waitFor(() => expect(getReplyDraft).toHaveBeenCalledWith(7, 2, false));
  });

  it("DraftListRow opens the reply dialog in Telegram mode when given a Telegram channelName", async () => {
    const draft = makeDraft();
    getReplyDraft.mockResolvedValue({
      to_address: null,
      cc: "",
      subject: "",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    wrap(<DraftListRow draft={draft} locale="en" channelName="Telegram" />);

    fireEvent.click(screen.getByTestId("article-draft-row-2"));
    await screen.findByTestId("reply-dialog");
    expect(screen.getByTestId("reply-telegram-hint")).toBeInTheDocument();
    expect(screen.queryByTestId("reply-to")).not.toBeInTheDocument();
  });

  it("DraftBubble's edit button opens the reply dialog with today's email behavior when channelName is Email", async () => {
    const draft = makeDraft();
    wrap(<DraftBubble draft={draft} locale="en" channelName="Email" />);

    fireEvent.click(screen.getByTestId("conversation-draft-edit-2"));
    await screen.findByTestId("reply-dialog");
    expect(screen.queryByTestId("reply-telegram-hint")).toBeNull();
    expect(screen.getByTestId("reply-to")).toBeInTheDocument();
  });

  it("discarding a draft removes it from the store after confirmation", async () => {
    serveDraft();
    // Render the bubble the way the article views do — driven by the store,
    // so the discard has to actually take effect for it to disappear.
    function DraftsOfTicket() {
      const drafts = useTicketReplyDrafts(7);
      return (
        <div data-testid="drafts">
          {drafts.map((d) => (
            <DraftBubble key={d.articleId} draft={d} locale="en" />
          ))}
        </div>
      );
    }
    wrap(<DraftsOfTicket />);

    expect(await screen.findByTestId("conversation-draft-2")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Discard draft"));

    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(screen.queryByTestId("conversation-draft-2")).toBeNull());
    expect(getDraft(qc, 7, 2)).toBeNull();
    expect(formDrafts.remove).toHaveBeenCalledWith(7, "AgentTicketCompose", 2);
  });
});

describe("ArticleQuickActions — draft affordance on the reply button", () => {
  beforeEach(() => {
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    formDrafts.list.mockReset().mockResolvedValue([]);
    formDrafts.upsert.mockReset();
    formDrafts.remove.mockReset().mockResolvedValue(undefined);
    getReplyDraft.mockReset().mockResolvedValue({
      to_address: "jane.doe@example.com",
      cc: "",
      subject: "Re: First",
      body: "quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    listTemplates.mockReset().mockResolvedValue([]);
  });

  it("shows the normal reply label and no data-has-draft when there is no draft", () => {
    wrap(
      <ArticleQuickActions
        ticketId={7}
        article={ARTICLE as never}
        canNote
        replyTestId="article-reader-reply"
      />,
    );

    const button = screen.getByTestId("article-reader-reply");
    expect(button).toHaveTextContent("Reply");
    expect(button).not.toHaveAttribute("data-has-draft");
    expect(screen.queryByTestId("article-draft-dot-2")).toBeNull();
  });

  it("shows the 'continue draft' label, data-has-draft and the dot when a draft exists", async () => {
    serveDraft();
    wrap(
      <ArticleQuickActions
        ticketId={7}
        article={ARTICLE as never}
        canNote
        replyTestId="article-reader-reply"
      />,
    );

    const button = screen.getByTestId("article-reader-reply");
    await waitFor(() => expect(button).toHaveAttribute("data-has-draft", "true"));
    expect(button).toHaveTextContent("Continue draft");
    expect(screen.getByTestId("article-draft-dot-2")).toBeInTheDocument();
  });
});
