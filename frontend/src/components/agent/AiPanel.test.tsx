import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { AiPanel } from "./AiPanel";

const { getState, requestDraft, summarize, discardDraft, adminDeleteDraft, listArticles, createArticle, getReplyDraft, listTemplates } =
  vi.hoisted(() => ({
    getState: vi.fn(),
    requestDraft: vi.fn(),
    summarize: vi.fn(),
    discardDraft: vi.fn(),
    adminDeleteDraft: vi.fn(),
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

const { mockUser } = vi.hoisted(() => ({
  mockUser: { current: { id: 42, login: "agent", is_admin: false } as {
    id: number;
    login: string;
    is_admin: boolean;
  } },
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: mockUser.current }),
}));

vi.mock("@/lib/aiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/aiApi")>("@/lib/aiApi");
  return { ...actual, aiApi: { ...actual.aiApi, adminDeleteDraft: adminDeleteDraft } };
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
  summary_created_at: null,
};

const baseDraft = {
  ticket_id: 1,
  subject: null as string | null,
  based_on_article_id: 3 as number | null,
  status: "open",
  accepted_article_id: null,
  create_time: "2026-07-23T10:00:00",
};

function fakeArticle(id: number) {
  return { id, create_time: "2026-07-01T10:00:00", incoming_time: null };
}

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
      summary_created_at: "2026-07-23T09:21:00",
    });
    listArticles.mockResolvedValue([40, 41, 42, 43].map(fakeArticle));
    summarize.mockResolvedValue({ status: "up_to_date", summary_body: null, upto_article_id: null });

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-summary-body")).toBeTruthy());
    expect(screen.getByTestId("ai-panel-summary-body").textContent).toBe("Existing summary text");

    // 42-of-43 covered → stale badge, coverage dots, created-at timestamp.
    await waitFor(() => expect(screen.getByTestId("ai-summary-stale")).toBeTruthy());
    expect(screen.getByTestId("ai-summary-coverage").getAttribute("aria-label")).toBe("3/4");
    expect(screen.getByTestId("ai-summary-created-at").textContent).toContain("2026");

    fireEvent.click(screen.getByTestId("ai-panel-summarize-button"));
    await waitFor(() => expect(summarize).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId("ai-panel-summary-uptodate")).toBeTruthy());
  });

  it("shows the current badge when no articles are newer than the summary", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: false,
      summary_body: "Summary",
      last_summary_upto_article_id: 42,
      summary_created_at: "2026-07-23T09:21:00",
    });
    listArticles.mockResolvedValue([40, 41, 42].map(fakeArticle));

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-summary-current")).toBeTruthy());
    expect(screen.getByTestId("ai-summary-coverage").getAttribute("aria-label")).toBe("3/3");
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
        { ...baseDraft, id: 7, kind: "reply", subject: "Re: Hello", body: "Draft body text", source: "manual" },
      ],
    });
    requestDraft.mockRejectedValue(new ApiError(429, "Too many requests", "/api/v1/tickets/1/ai/draft"));

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-7")).toBeTruthy());
    // Draft card meta line: timestamp + source + based-on article.
    expect(screen.getByTestId("ai-panel-draft-meta-7").textContent).toContain("2026");

    fireEvent.click(screen.getByTestId("ai-panel-create-draft-button"));
    await waitFor(() => expect(requestDraft).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy());
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toContain("later");
  });

  it("discards a draft after confirmation", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [{ ...baseDraft, id: 7, kind: "reply", body: "Draft body", source: "auto" }],
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
        { ...baseDraft, id: 8, kind: "clarify", body: "Draft body", based_on_article_id: null, source: "auto" },
      ],
    });
    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-discard-8")).toBeTruthy());
    fireEvent.click(screen.getByTestId("ai-panel-draft-discard-8"));

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    expect(discardDraft).not.toHaveBeenCalled();
  });

  it("shows the admin hard-delete button only for admins and calls the admin API", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [{ ...baseDraft, id: 21, kind: "reply", body: "Draft", source: "auto", tool_trace: [] }],
    });
    adminDeleteDraft.mockResolvedValue(undefined);

    mockUser.current = { id: 42, login: "agent", is_admin: false };
    const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-21")).toBeTruthy());
    expect(screen.queryByTestId("ai-panel-draft-admin-delete-21")).toBeNull();
    unmount();

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-admin-delete-21")).toBeTruthy());
    fireEvent.click(screen.getByTestId("ai-panel-draft-admin-delete-21"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(adminDeleteDraft).toHaveBeenCalledWith(21));
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("shows a collapsible tool trace on drafts that have one", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 11,
          kind: "reply",
          body: "Draft body",
          source: "manual",
          tool_trace: [
            { name: "kb_search", content: "3 Treffer zu VPN" },
            { name: "get_ticket", content: "{...}" },
          ],
        },
        { ...baseDraft, id: 12, kind: "reply", body: "No trace", source: "auto", tool_trace: [] },
      ],
    });

    wrap(<AiPanel ticketId={1} canNote />);

    await waitFor(() => expect(screen.getByTestId("ai-panel-draft-11")).toBeTruthy());
    // Draft without trace entries gets no toggle at all.
    expect(screen.queryByTestId("ai-panel-draft-trace-toggle-12")).toBeNull();

    const toggle = screen.getByTestId("ai-panel-draft-trace-toggle-11");
    expect(toggle.textContent).toContain("(2)");
    expect(screen.queryByTestId("ai-panel-draft-trace-11")).toBeNull();

    fireEvent.click(toggle);
    const trace = screen.getByTestId("ai-panel-draft-trace-11");
    expect(trace.textContent).toContain("kb_search");
    expect(trace.textContent).toContain("3 Treffer zu VPN");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("ai-panel-draft-trace-11")).toBeNull();
  });

  it("opens the reply editor prefilled with the draft body and sends with ai_draft_id", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        { ...baseDraft, id: 9, kind: "reply", subject: "Re: Draft subject", body: "AI drafted answer", source: "manual" },
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
