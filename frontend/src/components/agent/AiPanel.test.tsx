import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { AiPanel } from "./AiPanel";

const { getState, requestDraft, summarize, discardDraft, listArticles, createArticle, getReplyDraft, listTemplates } =
  vi.hoisted(() => ({
    getState: vi.fn(),
    requestDraft: vi.fn(),
    summarize: vi.fn(),
    discardDraft: vi.fn(),
    listArticles: vi.fn(),
    createArticle: vi.fn(),
    getReplyDraft: vi.fn(),
    listTemplates: vi.fn(),
  }));

vi.mock("@/lib/ticketAiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ticketAiApi")>("@/lib/ticketAiApi");
  return {
    ...actual,
    ticketAiApi: { getState, requestDraft, summarize, discardDraft },
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { listArticles, createArticle, getReplyDraft, listTemplates },
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

const baseState = {
  manual_assist_available: false,
  summary_available: false,
  can_summarize: false,
  operation_mode_ready: true,
  drafts: [],
  summary_body: null,
  last_summary_upto_article_id: null,
};

describe("AiPanel", () => {
  beforeEach(() => {
    getState.mockReset();
    requestDraft.mockReset();
    summarize.mockReset();
    discardDraft.mockReset();
    listArticles.mockReset().mockResolvedValue([]);
    createArticle.mockReset().mockResolvedValue({ id: 1 });
    getReplyDraft.mockReset().mockResolvedValue({
      to_address: "customer@example.com",
      cc: "",
      subject: "Re: Hello",
      body: "> quoted",
      in_reply_to: null,
      references: null,
      signature: "",
      signature_is_html: false,
    });
    listTemplates.mockReset().mockResolvedValue([]);
  });

  it("renders nothing when neither feature is available", async () => {
    getState.mockResolvedValue(baseState);
    const { container } = wrap(<AiPanel ticketId={1} canNote />);
    await waitFor(() => expect(getState).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders the summary section, calls summarize, and shows the up_to_date message", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: true,
      summary_body: "Existing summary text",
      last_summary_upto_article_id: 42,
    });
    summarize.mockResolvedValue({ status: "up_to_date", summary_body: null, upto_article_id: null });

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-summary-body")).toBeTruthy());
    expect(screen.getByTestId("ai-panel-summary-body").textContent).toBe("Existing summary text");

    fireEvent.click(screen.getByTestId("ai-panel-summarize-button"));
    await waitFor(() => expect(summarize).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId("ai-panel-summary-uptodate")).toBeTruthy());
  });

  it("shows the empty summary state and disables the button when can_summarize is false", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: false,
      summary_body: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-summary-empty")).toBeTruthy());
    expect(screen.getByTestId("ai-panel-summarize-button")).toBeDisabled();
  });

  it("lists open drafts, creates a new one, and maps a 429 error", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          id: 7,
          ticket_id: 1,
          kind: "reply",
          subject: "Re: Hello",
          body: "Draft body text",
          based_on_article_id: 3,
          status: "open",
          source: "manual",
          accepted_article_id: null,
        },
      ],
    });
    requestDraft.mockRejectedValue(new ApiError(429, "Too many requests", "/api/v1/tickets/1/ai/draft"));

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-7")).toBeTruthy());

    fireEvent.click(screen.getByTestId("ai-panel-create-draft-button"));
    await waitFor(() => expect(requestDraft).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy());
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toContain("later");
  });

  it("discards a draft after confirmation", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          id: 7,
          ticket_id: 1,
          kind: "reply",
          subject: null,
          body: "Draft body",
          based_on_article_id: 3,
          status: "open",
          source: "auto",
          accepted_article_id: null,
        },
      ],
    });
    discardDraft.mockResolvedValue(undefined);

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-discard-7")).toBeTruthy());
    fireEvent.click(screen.getByTestId("ai-panel-draft-discard-7"));

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(discardDraft).toHaveBeenCalledWith(1, 7));
  });

  it("does not discard when the confirm is dismissed", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          id: 8,
          ticket_id: 1,
          kind: "clarify",
          subject: null,
          body: "Draft body",
          based_on_article_id: null,
          status: "open",
          source: "auto",
          accepted_article_id: null,
        },
      ],
    });
    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-discard-8")).toBeTruthy());
    fireEvent.click(screen.getByTestId("ai-panel-draft-discard-8"));

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    expect(discardDraft).not.toHaveBeenCalled();
  });

  it("opens the reply editor prefilled with the draft body and sends with ai_draft_id", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          id: 9,
          ticket_id: 1,
          kind: "reply",
          subject: "Re: Draft subject",
          body: "AI drafted answer",
          based_on_article_id: 3,
          status: "open",
          source: "manual",
          accepted_article_id: null,
        },
      ],
    });

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-use-9")).toBeTruthy());
    fireEvent.click(screen.getByTestId("ai-panel-draft-use-9"));

    await waitFor(() => expect(screen.getByTestId("reply-dialog")).toBeTruthy());
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    expect(body.value).toContain("AI drafted answer");

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as { ai_draft_id: number | null };
    expect(payload.ai_draft_id).toBe(9);
  });
});
