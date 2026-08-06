import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { saveDraft, getDraft, resetDraftsForTest } from "@/lib/replyDrafts";
import { DraftListRow, DraftBubble } from "./DraftPlaceholder";
import { ArticleQuickActions } from "./ArticleQuickActions";

const { getReplyDraft, listTemplates } = vi.hoisted(() => ({
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
}));

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

function makeDraft() {
  saveDraft({
    ticketId: 7,
    articleId: 2,
    replyAll: false,
    subject: "Re: First",
    body: DRAFT_BODY,
    to: "jane.doe@example.com",
    cc: "",
    bcc: "",
    replyTo: "",
    aiDraftId: null,
  });
  return getDraft(7, 2)!;
}

describe("DraftPlaceholder", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetDraftsForTest();
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

  it("discarding a draft removes it from the store after confirmation", async () => {
    const draft = makeDraft();
    const { rerender } = wrap(<DraftBubble draft={draft} locale="en" />);

    expect(screen.getByTestId("conversation-draft-2")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Discard draft"));

    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(getDraft(7, 2)).toBeNull());

    // A consuming component re-rendering after the discard should no longer
    // show the placeholder — simulate that by re-mounting for the current
    // (now missing) draft.
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <div data-testid="after-discard">
            {getDraft(7, 2) ? <DraftBubble draft={getDraft(7, 2)!} locale="en" /> : null}
          </div>
        </I18nextProvider>
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("conversation-draft-2")).toBeNull();
  });
});

describe("ArticleQuickActions — draft affordance on the reply button", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetDraftsForTest();
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

  it("shows the 'continue draft' label, data-has-draft and the dot when a draft exists", () => {
    makeDraft();
    wrap(
      <ArticleQuickActions
        ticketId={7}
        article={ARTICLE as never}
        canNote
        replyTestId="article-reader-reply"
      />,
    );

    const button = screen.getByTestId("article-reader-reply");
    expect(button).toHaveAttribute("data-has-draft", "true");
    expect(button).toHaveTextContent("Continue draft");
    expect(screen.getByTestId("article-draft-dot-2")).toBeInTheDocument();
  });
});
