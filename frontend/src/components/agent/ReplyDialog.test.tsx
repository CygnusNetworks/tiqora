import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { getDraft } from "@/lib/replyDrafts";
import { ReplyDialog } from "./ReplyDialog";

const {
  getReplyDraft,
  listTemplates,
  createArticle,
  createTicketMention,
  createTicketTimeAccounting,
  listReferenceAgents,
  acquireTicketLock,
  formDrafts,
} = vi.hoisted(() => ({
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
  createArticle: vi.fn(),
  createTicketMention: vi.fn(),
  createTicketTimeAccounting: vi.fn(),
  listReferenceAgents: vi.fn(),
  acquireTicketLock: vi.fn(),
  formDrafts: { list: vi.fn(), upsert: vi.fn(), remove: vi.fn() },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      getReplyDraft,
      listTemplates,
      createArticle,
      createTicketMention,
      createTicketTimeAccounting,
      listReferenceAgents,
      acquireTicketLock,
    },
  };
});

// Drafts live on the server; keep an in-memory stand-in so the composer's
// autosave is observable without a backend.
vi.mock("@/lib/formDraftApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/formDraftApi")>("@/lib/formDraftApi");
  return { ...actual, formDraftApi: formDrafts };
});

let qc: QueryClient;

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  acquireTicketLock.mockReset().mockResolvedValue({ result: "acquired" });
  formDrafts.list.mockReset().mockResolvedValue([]);
  formDrafts.upsert.mockReset().mockImplementation((ticketId, body) =>
    Promise.resolve({
      id: 1,
      ticket_id: ticketId,
      user_id: 1,
      action: body.action,
      article_id: body.article_id,
      title: null,
      content: body.content,
      created: "2026-08-07T10:00:00",
      changed: "2026-08-07T10:00:00",
    }),
  );
  formDrafts.remove.mockReset().mockResolvedValue(undefined);
});

function wrap(ui: React.ReactElement) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const baseDraft = {
  to_address: "customer@example.com",
  cc: "",
  subject: "Re: Hello",
  body: "> quoted",
  in_reply_to: null as string | null,
  references: null as string | null,
  signature: "",
  signature_is_html: false,
};

describe("ReplyDialog recipient toggles", () => {
  beforeEach(() => {
    getReplyDraft.mockReset();
    listTemplates.mockReset().mockResolvedValue([]);
    createArticle.mockReset().mockResolvedValue({ id: 99 });
  });

  it("toggles collapse a shown Cc field and shows a count badge when collapsed non-empty", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      cc: "a@x.com, b@x.com",
    });

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    // Draft with Cc addresses → field expanded by default; toggle still present.
    expect(screen.getByTestId("reply-cc")).toBeTruthy();
    expect(screen.getByTestId("reply-toggle-cc")).toBeTruthy();
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    expect(screen.getAllByTestId("reply-cc-chip")).toHaveLength(2);

    // Collapse: field hidden, count badge = 2.
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.queryByTestId("reply-cc")).toBeNull();
    expect(screen.getByTestId("reply-toggle-cc-count").textContent).toBe("2");
    expect(screen.getByTestId("reply-toggle-cc").getAttribute("aria-expanded")).toBe(
      "false",
    );

    // Expand again: full field, no badge, chips still there.
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.getByTestId("reply-cc")).toBeTruthy();
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    expect(screen.getAllByTestId("reply-cc-chip")).toHaveLength(2);
  });

  it("count badge updates when addresses are added or removed while collapsed", async () => {
    getReplyDraft.mockResolvedValue({ ...baseDraft, cc: "only@x.com" });

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll={false}
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-cc")).toBeTruthy());

    // Expand Bcc, add two addresses, then collapse — badge shows 2.
    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    const bccInput = screen.getByTestId("reply-bcc-input");
    fireEvent.change(bccInput, { target: { value: "one@x.com" } });
    fireEvent.keyDown(bccInput, { key: "Enter" });
    fireEvent.change(bccInput, { target: { value: "two@x.com" } });
    fireEvent.keyDown(bccInput, { key: "Enter" });
    expect(screen.getAllByTestId("reply-bcc-chip")).toHaveLength(2);

    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    expect(screen.queryByTestId("reply-bcc")).toBeNull();
    expect(screen.getByTestId("reply-toggle-bcc-count").textContent).toBe("2");

    // Re-expand, remove one, collapse again — badge shows 1.
    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    fireEvent.click(screen.getAllByTestId("reply-bcc-remove")[0]);
    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    expect(screen.getByTestId("reply-toggle-bcc-count").textContent).toBe("1");
  });

  it("includes collapsed Cc/Bcc addresses on submit", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
      cc: "cc-hidden@x.com",
    });
    const onClose = vi.fn();

    wrap(
      <ReplyDialog
        ticketId={5}
        articleId={9}
        replyAll
        open
        onClose={onClose}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    // Collapse non-empty Cc (addresses must still be sent).
    fireEvent.click(screen.getByTestId("reply-toggle-cc"));
    expect(screen.queryByTestId("reply-cc")).toBeNull();
    expect(screen.getByTestId("reply-toggle-cc-count").textContent).toBe("1");

    // Add Bcc, then collapse it too.
    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    const bccInput = screen.getByTestId("reply-bcc-input");
    fireEvent.change(bccInput, { target: { value: "bcc-hidden@x.com" } });
    fireEvent.keyDown(bccInput, { key: "Enter" });
    fireEvent.click(screen.getByTestId("reply-toggle-bcc"));
    expect(screen.getByTestId("reply-toggle-bcc-count").textContent).toBe("1");

    // Ensure body is non-empty for canSend (seeded with blank + quote).
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "Thanks\n\n> quoted" } });

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as {
      to_address: string | null;
      cc: string | null;
      bcc: string | null;
    };
    expect(payload.to_address).toContain("to@x.com");
    expect(payload.cc).toContain("cc-hidden@x.com");
    expect(payload.bcc).toContain("bcc-hidden@x.com");
  });

  it("starts empty Cc/Bcc/Reply-To collapsed with no count badge", async () => {
    getReplyDraft.mockResolvedValue(baseDraft);

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll={false}
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    expect(screen.queryByTestId("reply-cc")).toBeNull();
    expect(screen.queryByTestId("reply-bcc")).toBeNull();
    expect(screen.queryByTestId("reply-replyto")).toBeNull();
    expect(screen.queryByTestId("reply-toggle-cc-count")).toBeNull();
    expect(screen.queryByTestId("reply-toggle-bcc-count")).toBeNull();
    expect(screen.queryByTestId("reply-toggle-replyto-count")).toBeNull();
  });

  it("signals Cc toggle on/off with active classes when enabled", async () => {
    getReplyDraft.mockResolvedValue(baseDraft);

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll={false}
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const ccToggle = screen.getByTestId("reply-toggle-cc");
    // Collapsed and empty → inactive (muted outline, not accent fill).
    expect(ccToggle.getAttribute("data-active")).toBe("false");
    expect(ccToggle.className).not.toMatch(/bg-accent-dim/);
    expect(ccToggle.className).toMatch(/border-hairline/);
    expect(ccToggle.className).toMatch(/text-muted/);

    // Expand → active (filled accent style).
    fireEvent.click(ccToggle);
    expect(ccToggle.getAttribute("data-active")).toBe("true");
    expect(ccToggle.className).toMatch(/bg-accent-dim/);
    expect(ccToggle.className).toMatch(/text-accent/);
    expect(ccToggle.className).toMatch(/border-accent\/50/);

    // Collapse again with no addresses → inactive.
    fireEvent.click(ccToggle);
    expect(ccToggle.getAttribute("data-active")).toBe("false");
    expect(ccToggle.className).not.toMatch(/bg-accent-dim/);
  });

  it("keeps Cc toggle active when collapsed but non-empty", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      cc: "a@x.com, b@x.com",
    });

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-cc")).toBeTruthy());

    const ccToggle = screen.getByTestId("reply-toggle-cc");
    expect(ccToggle.getAttribute("data-active")).toBe("true");
    expect(ccToggle.className).toMatch(/bg-accent-dim/);

    // Collapse while addresses remain → still "on".
    fireEvent.click(ccToggle);
    expect(screen.queryByTestId("reply-cc")).toBeNull();
    expect(ccToggle.getAttribute("data-active")).toBe("true");
    expect(ccToggle.className).toMatch(/bg-accent-dim/);
    expect(screen.getByTestId("reply-toggle-cc-count").textContent).toBe("2");
  });

  it("shows a read-only signature preview and does not send it in the body", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
      signature: "Alice Example\nSupport Team",
      signature_is_html: false,
    });

    wrap(
      <ReplyDialog
        ticketId={3}
        articleId={4}
        replyAll={false}
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-signature-preview")).toBeTruthy());
    expect(screen.getByTestId("reply-signature-plain").textContent).toContain(
      "Alice Example",
    );

    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    // Seeded body is blank answer + quote only — signature stays out of the textarea.
    expect(body.value).not.toContain("Alice Example");
    expect(body.value).not.toContain("Support Team");

    fireEvent.change(body, { target: { value: "Thanks\n\n> quoted" } });
    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as { body: string };
    expect(payload.body).toBe("Thanks\n\n> quoted");
    expect(payload.body).not.toContain("Alice Example");
    expect(payload.body).not.toContain("Support Team");
  });

  it("surfaces the server error detail when send fails", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
    });
    createArticle.mockRejectedValue(
      new ApiError(
        502,
        "Outbound email delivery failed: SMTP refused",
        "/api/v1/tickets/1/articles",
      ),
    );

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll={false}
        open
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "Thanks\n\n> quoted" } });
    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(screen.getByTestId("reply-send-error")).toBeTruthy());
    expect(screen.getByTestId("reply-send-error").textContent).toContain(
      "Outbound email delivery failed: SMTP refused",
    );
  });

  it("seeds the body from an AI draft and sends with ai_draft_id", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
    });

    wrap(
      <ReplyDialog
        ticketId={1}
        articleId={2}
        replyAll={false}
        open
        onClose={vi.fn()}
        initialDraft={{ id: 42, subject: "Re: AI subject", body: "AI drafted answer" }}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    expect(body.value).toContain("AI drafted answer");
    expect(body.value).toContain("> quoted");

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as { ai_draft_id: number | null };
    expect(payload.ai_draft_id).toBe(42);
  });

  it("sends ai_draft_id null when no AI draft is used", async () => {
    getReplyDraft.mockResolvedValue({ ...baseDraft, to_address: "to@x.com" });

    wrap(<ReplyDialog ticketId={1} articleId={2} replyAll={false} open onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());
    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as { ai_draft_id: number | null };
    expect(payload.ai_draft_id).toBeNull();
  });
});

/** Wrapper with a controllable `open` prop — the component stays mounted
 * across close/reopen, mirroring how the real caller (ArticleList) uses it,
 * so these tests can exercise "reopen after send" and "reopen after
 * close-without-sending" against the same instance. */
function ControlledReplyDialog({
  ticketId,
  articleId,
  onCloseSpy,
}: {
  ticketId: number;
  articleId: number;
  onCloseSpy?: () => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" data-testid="reopen" onClick={() => setOpen(true)}>
        reopen
      </button>
      <ReplyDialog
        ticketId={ticketId}
        articleId={articleId}
        replyAll={false}
        open={open}
        onClose={() => {
          setOpen(false);
          onCloseSpy?.();
        }}
      />
    </>
  );
}

describe("ReplyDialog reset after send / persistence without send", () => {
  beforeEach(() => {
    getReplyDraft.mockReset();
    listTemplates.mockReset().mockResolvedValue([]);
    createArticle.mockReset().mockResolvedValue({ id: 99 });
  });

  it("resets body and subject to the server seed after a successful send, on reopen", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
    });

    wrap(<ControlledReplyDialog ticketId={7} articleId={11} />);

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const subjectInput = screen.getByDisplayValue("Re: Hello") as HTMLInputElement;
    fireEvent.change(subjectInput, { target: { value: "Re: Changed subject" } });

    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "My typed reply\n\n> quoted" } });

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    // Dialog closed after send.
    await waitFor(() => expect(screen.queryByTestId("reply-dialog")).toBeNull());

    // Reopen the same (still-mounted) component.
    fireEvent.click(screen.getByTestId("reopen"));
    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const reopenedBody = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    expect(reopenedBody.value).not.toContain("My typed reply");
    expect(reopenedBody.value).toBe("\n\n> quoted");

    const reopenedSubject = screen.getByDisplayValue("Re: Hello") as HTMLInputElement;
    expect(reopenedSubject.value).not.toBe("Re: Changed subject");
  });

  it("clears the draft store after a successful send", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
    });

    wrap(<ControlledReplyDialog ticketId={7} articleId={11} />);

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "My typed reply\n\n> quoted" } });

    // Let the debounced draft-store write happen before sending, so we can
    // be sure clearing (not "never having saved") is what emptied it.
    await waitFor(
      () => expect(getDraft(qc, 7, 11)).not.toBeNull(),
      { timeout: 1000 },
    );

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    await waitFor(() => expect(getDraft(qc, 7, 11)).toBeNull());
  });

  it("keeps typed text on close without sending, and restores it on reopen", async () => {
    getReplyDraft.mockResolvedValue({
      ...baseDraft,
      to_address: "to@x.com",
    });
    const onCloseSpy = vi.fn();

    wrap(<ControlledReplyDialog ticketId={8} articleId={12} onCloseSpy={onCloseSpy} />);

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "Draft in progress\n\n> quoted" } });

    // Close via Cancel, without sending.
    fireEvent.click(screen.getByText(i18n.t("ticket.composerCancel")));
    expect(onCloseSpy).toHaveBeenCalled();
    expect(createArticle).not.toHaveBeenCalled();

    await waitFor(() => expect(screen.queryByTestId("reply-dialog")).toBeNull());

    // The debounced store write should have persisted the typed text.
    await waitFor(
      () => expect(getDraft(qc, 8, 12)?.body).toContain("Draft in progress"),
      { timeout: 1000 },
    );

    // Reopen — the typed text must still be there.
    fireEvent.click(screen.getByTestId("reopen"));
    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());

    const reopenedBody = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    expect(reopenedBody.value).toContain("Draft in progress");
  });
});

describe("ReplyDialog composer extras (mentions + time)", () => {
  beforeEach(() => {
    getReplyDraft.mockReset().mockResolvedValue(baseDraft);
    listTemplates.mockReset().mockResolvedValue([]);
    createArticle.mockReset().mockResolvedValue({ id: 99 });
    createTicketMention.mockReset().mockResolvedValue({ id: 1 });
    createTicketTimeAccounting.mockReset().mockResolvedValue({ id: 1 });
    listReferenceAgents
      .mockReset()
      .mockResolvedValue([{ id: 2, login: "ada", full_name: "Ada Lovelace" }]);
  });

  async function openDialog(onClose = vi.fn()) {
    wrap(<ReplyDialog ticketId={1} articleId={2} replyAll={false} open onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());
    return onClose;
  }

  it("books nothing extra for a plain reply", async () => {
    const onClose = await openDialog();
    fireEvent.change(screen.getByTestId("reply-body"), { target: { value: "Antwort" } });
    fireEvent.click(screen.getByTestId("reply-send"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(createTicketTimeAccounting).not.toHaveBeenCalled();
    expect(createTicketMention).not.toHaveBeenCalled();
  });

  it("books the minutes typed into the footer chip", async () => {
    const onClose = await openDialog();
    fireEvent.change(screen.getByTestId("reply-body"), { target: { value: "Antwort" } });
    fireEvent.change(screen.getByTestId("reply-time"), { target: { value: "15" } });
    fireEvent.click(screen.getByTestId("reply-send"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(1, { time_unit: 15 }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("records an agent picked from the @ typeahead", async () => {
    await openDialog();
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "bitte @ad" } });
    body.setSelectionRange(9, 9);
    fireEvent.keyUp(body, { key: "d" });
    fireEvent.mouseDown(await screen.findByTestId("mention-option-2"));
    await waitFor(() => expect(body.value).toContain("@Ada Lovelace"));
    fireEvent.click(screen.getByTestId("reply-send"));
    await waitFor(() => expect(createTicketMention).toHaveBeenCalledWith(1, { user_id: 2 }));
  });

  it("keeps the dialog open with a retry when only the booking fails", async () => {
    createTicketTimeAccounting.mockRejectedValueOnce(new Error("boom"));
    const onClose = await openDialog();
    fireEvent.change(screen.getByTestId("reply-body"), { target: { value: "Antwort" } });
    fireEvent.change(screen.getByTestId("reply-time"), { target: { value: "15" } });
    fireEvent.click(screen.getByTestId("reply-send"));

    // The reply itself went out exactly once and the dialog stays put.
    await screen.findByTestId("reply-extras-error");
    expect(createArticle).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByTestId("reply-send")).toBeNull();

    fireEvent.click(screen.getByTestId("reply-extras-retry"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    // Retrying books the time; it never re-sends the article.
    expect(createTicketTimeAccounting).toHaveBeenCalledTimes(2);
    expect(createArticle).toHaveBeenCalledTimes(1);
  });
});
