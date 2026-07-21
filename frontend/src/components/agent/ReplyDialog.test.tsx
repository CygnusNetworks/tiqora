import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { ReplyDialog } from "./ReplyDialog";

const { getReplyDraft, listTemplates, createArticle } = vi.hoisted(() => ({
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
  createArticle: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      getReplyDraft,
      listTemplates,
      createArticle,
    },
  };
});

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
});
