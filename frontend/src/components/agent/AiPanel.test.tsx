import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { AiPanel } from "./AiPanel";
import { useComposerRequests, useConversationViewRequests } from "./telegram/composerBus";

const {
  getState,
  requestDraft,
  summarize,
  customSummary,
  discardDraft,
  resume,
  pause,
  unpause,
  acceptTriage,
  rejectTriage,
  adminDeleteDraft,
  adminDeleteSummary,
  listArticles,
  createArticle,
  getReplyDraft,
  listTemplates,
} = vi.hoisted(() => ({
  getState: vi.fn(),
  requestDraft: vi.fn(),
  summarize: vi.fn(),
  customSummary: vi.fn(),
  discardDraft: vi.fn(),
  resume: vi.fn(),
  pause: vi.fn(),
  unpause: vi.fn(),
  acceptTriage: vi.fn(),
  rejectTriage: vi.fn(),
  adminDeleteDraft: vi.fn(),
  adminDeleteSummary: vi.fn(),
  listArticles: vi.fn(),
  createArticle: vi.fn(),
  getReplyDraft: vi.fn(),
  listTemplates: vi.fn(),
}));

vi.mock("@/lib/ticketAiApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/ticketAiApi")>(
      "@/lib/ticketAiApi",
    );
  return {
    ...actual,
    ticketAiApi: {
      getState,
      requestDraft,
      summarize,
      customSummary,
      discardDraft,
      resume,
      pause,
      unpause,
      acceptTriage,
      rejectTriage,
    },
  };
});

const { mockUser } = vi.hoisted(() => ({
  mockUser: {
    current: { id: 42, login: "agent", is_admin: false } as {
      id: number;
      login: string;
      is_admin: boolean;
    },
  },
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: mockUser.current }),
}));

vi.mock("@/lib/aiApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/aiApi")>("@/lib/aiApi");
  return {
    ...actual,
    aiApi: {
      ...actual.aiApi,
      adminDeleteDraft: adminDeleteDraft,
      adminDeleteSummary,
    },
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

/** The panel's content lives in hover cards behind the header chips —
 * click every chip so the card stays open for the rest of the test. */
async function openCards() {
  await waitFor(() => expect(screen.getByTestId("ai-chips")).toBeTruthy());
  for (const id of ["ai-chip-summary", "ai-chip-drafts"]) {
    const chip = screen.queryByTestId(id);
    if (chip && chip.getAttribute("aria-expanded") !== "true") fireEvent.click(chip);
  }
}

/** The pause switch lives in the summary line's ⋯ menu. */
async function clickPauseInMenu() {
  fireEvent.click(await screen.findByTestId("ai-summary-line-menu-trigger"));
  fireEvent.click(await screen.findByTestId("ai-panel-pause-button"));
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
    customSummary.mockReset();
    window.localStorage.clear();
    discardDraft.mockReset();
    resume.mockReset();
    pause.mockReset();
    unpause.mockReset();
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

  it("says why the auto worker skipped the ticket and that a draft still works", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      auto_skip_reason: "budget_tokens_day",
      auto_skip_at: "2026-10-01T15:16:20Z",
    });
    wrap(<AiPanel ticketId={1} canNote />);

    const banner = await screen.findByTestId("ai-panel-auto-skip-banner");
    expect(banner.textContent).toContain("daily token budget is used up");
    expect(banner.textContent).toContain(i18n.t("ticket.ai.autoSkip.manualHint"));
  });

  it("leaves the auto-skip notice to the pause banner while paused", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      ai_paused_at: "2026-10-01T15:00:00Z",
      auto_skip_reason: "ai_paused",
      auto_skip_at: "2026-10-01T15:16:20Z",
    });
    wrap(<AiPanel ticketId={1} canNote />);

    await screen.findByTestId("ai-panel-paused-banner");
    expect(screen.queryByTestId("ai-panel-auto-skip-banner")).not.toBeInTheDocument();
  });

  it("shows the escalated banner and resumes AI when clicked", async () => {
    getState
      .mockResolvedValueOnce({
        ...baseState,
        summary_available: true,
        ai_escalated_at: "2026-09-14T07:34:59",
      })
      .mockResolvedValueOnce({
        ...baseState,
        summary_available: true,
        ai_escalated_at: null,
      });
    resume.mockResolvedValue(undefined);

    wrap(<AiPanel ticketId={1} canNote />);

    const banner = await screen.findByTestId("ai-panel-escalated-banner");
    expect(banner.textContent).toContain("AI handed off to a human");

    fireEvent.click(screen.getByTestId("ai-panel-resume-button"));

    await waitFor(() => expect(resume).toHaveBeenCalledWith(1));
    await waitFor(() =>
      expect(
        screen.queryByTestId("ai-panel-escalated-banner"),
      ).not.toBeInTheDocument(),
    );
  });

  it("pauses AI automation and refreshes state", async () => {
    getState
      .mockResolvedValueOnce({ ...baseState, summary_available: true })
      .mockResolvedValueOnce({
        ...baseState,
        summary_available: true,
        ai_paused_at: "2026-09-30T08:00:00",
        ai_paused_by_name: "Erika",
      });
    pause.mockResolvedValue(undefined);

    wrap(<AiPanel ticketId={1} canNote />);

    await clickPauseInMenu();

    await waitFor(() => expect(pause).toHaveBeenCalledWith(1));
    const banner = await screen.findByTestId("ai-panel-paused-banner");
    expect(banner.textContent).toContain("Erika");
    expect(screen.queryByTestId("ai-panel-pause-button")).not.toBeInTheDocument();
    expect(screen.getByTestId("ai-banners")).toBeInTheDocument();
  });

  it("keeps the banners row empty while nothing applies", async () => {
    getState.mockResolvedValue({ ...baseState, summary_available: true });
    wrap(<AiPanel ticketId={1} canNote />);
    await screen.findByTestId("ai-summary-line");
    expect(screen.queryByTestId("ai-banners")).not.toBeInTheDocument();
  });

  it("uses the name-less banner when the pauser is unknown", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      ai_paused_at: "2026-09-30T08:00:00",
      ai_paused_by_name: null,
    });
    wrap(<AiPanel ticketId={1} canNote />);
    const banner = await screen.findByTestId("ai-panel-paused-banner");
    expect(banner.textContent).toContain("AI automation paused since");
    expect(banner.textContent).not.toContain("?");
  });

  it("shows the pause banner and unpause even without any AI feature access", async () => {
    getState.mockResolvedValue({
      ...baseState,
      ai_paused_at: "2026-09-30T08:00:00",
      ai_paused_by_name: "Erika",
    });
    unpause.mockResolvedValue(undefined);
    wrap(<AiPanel ticketId={1} canNote />);
    fireEvent.click(await screen.findByTestId("ai-panel-unpause-button"));
    await waitFor(() => expect(unpause).toHaveBeenCalledWith(1));
  });

  it("offers a standalone pause link when there is no summary line", async () => {
    getState.mockResolvedValue({ ...baseState, manual_assist_available: true });
    pause.mockResolvedValue(undefined);
    wrap(<AiPanel ticketId={1} canNote />);
    fireEvent.click(await screen.findByTestId("ai-panel-pause-button"));
    await waitFor(() => expect(pause).toHaveBeenCalledWith(1));
  });

  it("shows the paused banner, keeps manual AI enabled, and unpauses", async () => {
    getState
      .mockResolvedValueOnce({
        ...baseState,
        summary_available: true,
        can_summarize: true,
        manual_assist_available: true,
        ai_paused_at: "2026-09-30T08:00:00",
        ai_paused_by_name: "Erika",
      })
      .mockResolvedValueOnce({
        ...baseState,
        summary_available: true,
        can_summarize: true,
        manual_assist_available: true,
      });
    unpause.mockResolvedValue(undefined);

    wrap(<AiPanel ticketId={1} canNote />);

    const banner = await screen.findByTestId("ai-panel-paused-banner");
    expect(banner.textContent).toContain("AI automation paused by Erika");
    expect(banner.textContent).toContain("Manual AI features keep working");

    await openCards();
    expect(await screen.findByTestId("ai-panel-summarize-button")).not.toBeDisabled();
    const draftsChip = await screen.findByTestId("ai-chip-drafts");
    expect(draftsChip).not.toBeDisabled();

    fireEvent.click(screen.getByTestId("ai-panel-unpause-button"));
    await waitFor(() => expect(unpause).toHaveBeenCalledWith(1));
    await waitFor(() =>
      expect(screen.queryByTestId("ai-panel-paused-banner")).not.toBeInTheDocument(),
    );
  });

  it("shows an error when pausing fails", async () => {
    getState.mockResolvedValue({ ...baseState, summary_available: true });
    pause.mockRejectedValue(new Error("boom"));
    wrap(<AiPanel ticketId={1} canNote />);
    await clickPauseInMenu();
    expect(await screen.findByTestId("ai-panel-pause-error")).toBeInTheDocument();
  });

  it("offers no pause item without note permission", async () => {
    getState.mockResolvedValue({ ...baseState, summary_available: true });
    wrap(<AiPanel ticketId={1} canNote={false} />);
    fireEvent.click(await screen.findByTestId("ai-summary-line-menu-trigger"));
    await screen.findByTestId("ai-summary-line-pin");
    expect(screen.queryByTestId("ai-panel-pause-button")).not.toBeInTheDocument();
  });

  it("disables the resume button without note permission", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      ai_escalated_at: "2026-09-14T07:34:59",
    });

    wrap(<AiPanel ticketId={1} canNote={false} />);

    const button = await screen.findByTestId("ai-panel-resume-button");
    expect(button).toBeDisabled();
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
    summarize.mockResolvedValue({
      status: "up_to_date",
      summary_body: null,
      upto_article_id: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-body")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-summary-body").textContent).toBe(
      "Existing summary text",
    );

    // 42-of-43 covered → stale badge, coverage dots, created-at timestamp.
    await waitFor(() =>
      expect(screen.getByTestId("ai-summary-stale")).toBeTruthy(),
    );
    expect(
      screen.getByTestId("ai-summary-coverage").getAttribute("aria-label"),
    ).toBe("3/4");
    expect(screen.getByTestId("ai-summary-created-at").textContent).toContain(
      "2026",
    );

    fireEvent.click(screen.getByTestId("ai-panel-summarize-button"));
    await waitFor(() => expect(summarize).toHaveBeenCalledWith(1, "standard"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-uptodate")).toBeTruthy(),
    );
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
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-summary-current")).toBeTruthy(),
    );
    expect(
      screen.getByTestId("ai-summary-coverage").getAttribute("aria-label"),
    ).toBe("3/3");
  });

  it("shows the empty summary state and disables the button when can_summarize is false", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: false,
      summary_body: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-empty")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-summarize-button")).toBeDisabled();
  });

  it("lists open drafts, creates a new one, and maps a 429 error", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 7,
          kind: "reply",
          subject: "Re: Hello",
          body: "Draft body text",
          source: "manual",
        },
      ],
    });
    requestDraft.mockRejectedValue(
      new ApiError(429, "Too many requests", "/api/v1/tickets/1/ai/draft"),
    );

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-7")).toBeTruthy(),
    );
    // Draft card meta line: timestamp + source + based-on article.
    expect(screen.getByTestId("ai-panel-draft-meta-7").textContent).toContain(
      "2026",
    );

    fireEvent.click(screen.getByTestId("ai-panel-create-draft-button"));
    await waitFor(() => expect(requestDraft).toHaveBeenCalledWith(1));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toContain(
      "later",
    );
  });

  it("maps a known llm_empty_output detail code to the specific message", async () => {
    getState.mockResolvedValue({ ...baseState, manual_assist_available: true });
    requestDraft.mockRejectedValue(
      new ApiError(
        502,
        "llm_empty_output: LLM returned finish_reason='length' twice in a row.",
        "/api/v1/tickets/1/ai/draft",
      ),
    );

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toBe(
      "The AI model used up its token budget while reasoning. Please try again.",
    );
  });

  it("maps a known llm_timeout detail code to the specific message", async () => {
    getState.mockResolvedValue({ ...baseState, manual_assist_available: true });
    requestDraft.mockRejectedValue(
      new ApiError(504, "llm_timeout: timed out", "/api/v1/tickets/1/ai/draft"),
    );

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toBe(
      "Timed out waiting for the AI provider. Please try again.",
    );
  });

  it("maps a known llm_provider_error detail code to the specific message", async () => {
    getState.mockResolvedValue({ ...baseState, manual_assist_available: true });
    requestDraft.mockRejectedValue(
      new ApiError(
        502,
        "llm_provider_error: HTTP 503: Service Unavailable",
        "/api/v1/tickets/1/ai/draft",
      ),
    );

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toBe(
      "The AI provider reported an error. Please try again later.",
    );
  });

  it("falls back to the generic error message for an unknown detail code", async () => {
    getState.mockResolvedValue({ ...baseState, manual_assist_available: true });
    requestDraft.mockRejectedValue(
      new ApiError(
        500,
        "some_unmapped_code: boom",
        "/api/v1/tickets/1/ai/draft",
      ),
    );

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-error").textContent).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("polls after 'started', shows the running hint, then reveals the new draft once drafted", async () => {
    getState
      .mockResolvedValueOnce({ ...baseState, manual_assist_available: true })
      .mockResolvedValueOnce({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "running",
        manual_run_started_at: "2026-08-14T10:00:00",
      })
      .mockResolvedValue({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "drafted",
        manual_run_started_at: "2026-08-14T10:00:00",
        drafts: [
          {
            ...baseDraft,
            id: 50,
            kind: "reply",
            subject: "Re: Hello",
            body: "Fresh async draft",
            source: "manual",
          },
        ],
      });
    requestDraft.mockResolvedValue({
      status: "started",
      draft_id: null,
      article_id: null,
      notes: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));
    await waitFor(() => expect(requestDraft).toHaveBeenCalledWith(1));

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-running").textContent).toBe(
        "The AI is working on a draft — this can take several minutes…",
      ),
    );
    expect(screen.getByTestId("ai-panel-create-draft-button")).toBeDisabled();

    // The "drafted" outcome only arrives on the NEXT poll tick
    // (MANUAL_RUN_POLL_INTERVAL_MS, real timers) — past the default waitFor
    // window, so this one needs a longer timeout.
    await waitFor(
      () => expect(screen.getByTestId("ai-panel-draft-50")).toBeTruthy(),
      { timeout: 4000 },
    );
    expect(screen.getByTestId("ai-panel-draft-body-50").textContent).toBe(
      "Fresh async draft",
    );
    expect(
      screen.queryByTestId("ai-panel-create-draft-button"),
    ).not.toBeDisabled();
  });

  it("shows the skipped info box with notes for the run this panel started", async () => {
    getState
      .mockResolvedValueOnce({ ...baseState, manual_assist_available: true })
      .mockResolvedValueOnce({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "running",
        manual_run_started_at: "2026-08-14T10:00:00",
      })
      .mockResolvedValue({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "skipped",
        manual_run_notes: "No terminal tool call produced.",
        manual_run_started_at: "2026-08-14T10:00:00",
      });
    requestDraft.mockResolvedValue({
      status: "started",
      draft_id: null,
      article_id: null,
      notes: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));

    await waitFor(
      () => expect(screen.getByTestId("ai-panel-draft-skipped")).toBeTruthy(),
      { timeout: 4000 },
    );
    expect(screen.getByTestId("ai-panel-draft-skipped").textContent).toContain(
      "The AI did not produce a reply suggestion.",
    );
    expect(screen.getByTestId("ai-panel-draft-skipped-notes").textContent).toBe(
      "No terminal tool call produced.",
    );
  });

  it("shows the no-answer-needed run as a skipped box carrying the reason", async () => {
    // Regression: the agent recognised a
    // newsletter but had no way to end the run without customer text, so it
    // wrote one. "no_reply" is that exit — the panel must render it as a
    // finished run rather than leaving the spinner up forever.
    getState
      .mockResolvedValueOnce({ ...baseState, manual_assist_available: true })
      .mockResolvedValueOnce({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "running",
        manual_run_started_at: "2026-08-14T10:00:00",
      })
      .mockResolvedValue({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "no_reply",
        manual_run_notes: "Werbe-Newsletter, keine Anfrage.",
        manual_run_started_at: "2026-08-14T10:00:00",
      });
    requestDraft.mockResolvedValue({
      status: "started",
      draft_id: null,
      article_id: null,
      notes: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));

    await waitFor(
      () => expect(screen.getByTestId("ai-panel-draft-skipped")).toBeTruthy(),
      { timeout: 4000 },
    );
    expect(screen.getByTestId("ai-panel-draft-skipped-notes").textContent).toBe(
      "Werbe-Newsletter, keine Anfrage.",
    );
  });

  it("maps manual_run_error_code from a polled error status to the specific message", async () => {
    getState
      .mockResolvedValueOnce({ ...baseState, manual_assist_available: true })
      .mockResolvedValueOnce({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "running",
        manual_run_started_at: "2026-08-14T10:00:00",
      })
      .mockResolvedValue({
        ...baseState,
        manual_assist_available: true,
        manual_run_status: "error",
        manual_run_error_code: "llm_timeout",
        manual_run_notes: "provider timed out",
        manual_run_started_at: "2026-08-14T10:00:00",
      });
    requestDraft.mockResolvedValue({
      status: "started",
      draft_id: null,
      article_id: null,
      notes: null,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    fireEvent.click(await screen.findByTestId("ai-panel-create-draft-button"));

    await waitFor(
      () => expect(screen.getByTestId("ai-panel-draft-run-error")).toBeTruthy(),
      { timeout: 4000 },
    );
    expect(screen.getByTestId("ai-panel-draft-run-error").textContent).toBe(
      "Timed out waiting for the AI provider. Please try again.",
    );
  });

  it("does not show a stale running/error result from a run this panel instance did not start", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      manual_run_status: "error",
      manual_run_error_code: "llm_timeout",
      manual_run_started_at: "2020-01-01T00:00:00",
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await screen.findByTestId("ai-panel-create-draft-button");
    expect(screen.queryByTestId("ai-panel-draft-run-error")).toBeNull();
    expect(screen.queryByTestId("ai-panel-draft-running")).toBeNull();
    expect(
      screen.getByTestId("ai-panel-create-draft-button"),
    ).not.toBeDisabled();
  });

  it("discards a draft after confirmation", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 7,
          kind: "reply",
          body: "Draft body",
          source: "auto",
        },
      ],
    });
    discardDraft.mockResolvedValue(undefined);

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-discard-7")).toBeTruthy(),
    );
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
          ...baseDraft,
          id: 8,
          kind: "clarify",
          body: "Draft body",
          based_on_article_id: null,
          source: "auto",
        },
      ],
    });
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-discard-8")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-discard-8"));

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    expect(discardDraft).not.toHaveBeenCalled();
  });

  it("shows the admin hard-delete button only for admins and calls the admin API", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 21,
          kind: "reply",
          body: "Draft",
          source: "auto",
          tool_trace: [],
        },
      ],
    });
    adminDeleteDraft.mockResolvedValue(undefined);

    mockUser.current = { id: 42, login: "agent", is_admin: false };
    const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-21")).toBeTruthy(),
    );
    expect(screen.queryByTestId("ai-panel-draft-menu-trigger-21")).toBeNull();
    unmount();

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-menu-trigger-21")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-menu-trigger-21"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-admin-delete-21")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-admin-delete-21"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(adminDeleteDraft).toHaveBeenCalledWith(21));
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("shows non-open drafts with a status badge to admins only", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 30,
          kind: "reply",
          body: "Old draft",
          source: "auto",
          status: "discarded",
          tool_trace: [],
        },
      ],
    });

    mockUser.current = { id: 42, login: "agent", is_admin: false };
    const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-drafts-empty")).toBeTruthy(),
    );
    expect(screen.queryByTestId("ai-panel-draft-30")).toBeNull();
    unmount();

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-30")).toBeTruthy(),
    );
    expect(screen.getByTestId("ai-panel-draft-status-30")).toBeTruthy();
    expect(screen.getByTestId("ai-panel-draft-menu-trigger-30")).toBeTruthy();
    // Discard/use only make sense for open drafts.
    expect(screen.queryByTestId("ai-panel-draft-discard-30")).toBeNull();
    expect(screen.queryByTestId("ai-panel-draft-use-30")).toBeNull();
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("lets admins delete the stored summary after confirmation", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: true,
      summary_body: "Summary to delete",
      last_summary_upto_article_id: 42,
      summary_created_at: "2026-07-23T09:21:00",
    });
    adminDeleteSummary.mockResolvedValue(undefined);

    mockUser.current = { id: 42, login: "agent", is_admin: false };
    const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-body")).toBeTruthy(),
    );
    expect(screen.queryByTestId("ai-panel-summary-admin-delete")).toBeNull();
    unmount();

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-menu-trigger")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-summary-menu-trigger"));
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-summary-admin-delete")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-summary-admin-delete"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(adminDeleteSummary).toHaveBeenCalledWith(1));
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("summarizes with the detail chosen in the segment control", async () => {
    getState.mockResolvedValue({
      ...baseState,
      summary_available: true,
      can_summarize: true,
      summary_body: null,
    });
    summarize.mockResolvedValue({
      status: "updated",
      summary_body: "New",
      upto_article_id: 5,
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(
        screen.getByTestId("ai-panel-summary-detail-detailed"),
      ).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-summary-detail-detailed"));
    fireEvent.click(screen.getByTestId("ai-panel-summarize-button"));
    await waitFor(() => expect(summarize).toHaveBeenCalledWith(1, "detailed"));
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
        {
          ...baseDraft,
          id: 12,
          kind: "reply",
          body: "No trace",
          source: "auto",
          tool_trace: [],
        },
      ],
    });

    // Non-admins never see the trace toggle.
    mockUser.current = { id: 42, login: "agent", is_admin: false };
    const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-11")).toBeTruthy(),
    );
    expect(screen.queryByTestId("ai-panel-draft-trace-toggle-11")).toBeNull();
    unmount();

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-11")).toBeTruthy(),
    );
    // Draft without trace entries gets no toggle at all.
    expect(screen.queryByTestId("ai-panel-draft-trace-toggle-12")).toBeNull();

    const toggle = screen.getByTestId("ai-panel-draft-trace-toggle-11");
    expect(toggle.textContent).toContain("(2)");
    expect(screen.queryByTestId("ai-panel-draft-trace-11")).toBeNull();

    fireEvent.click(toggle);
    const trace = screen.getByTestId("ai-panel-draft-trace-11");
    expect(trace.textContent).toContain("kb_search");
    // Result content lives behind the per-tool card header.
    expect(trace.textContent).not.toContain("3 Treffer zu VPN");
    fireEvent.click(screen.getByTestId("ai-panel-draft-trace-step-11-0"));
    expect(
      screen.getByTestId("ai-panel-draft-trace-step-11-0-body").textContent,
    ).toContain("3 Treffer zu VPN");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("ai-panel-draft-trace-11")).toBeNull();
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("renders JSON tool results as a key/value grid with real newlines", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 13,
          kind: "reply",
          body: "Draft",
          source: "auto",
          tool_trace: [
            {
              name: "get_network_status",
              content:
                '{"status": "disruption", "affected_services": ["Internet", "IPTV"], "message": "Zeile 1\\nZeile 2"}',
            },
          ],
        },
      ],
    });

    mockUser.current = { id: 1, login: "root@localhost", is_admin: true };
    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();
    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-trace-toggle-13")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-trace-toggle-13"));
    fireEvent.click(screen.getByTestId("ai-panel-draft-trace-step-13-0"));
    const body = screen.getByTestId("ai-panel-draft-trace-step-13-0-body");
    // Key/value grid: keys and values present, no raw JSON braces or \n escapes.
    expect(body.textContent).toContain("status");
    expect(body.textContent).toContain("disruption");
    expect(body.textContent).toContain("Internet");
    expect(body.textContent).toContain("Zeile 1\nZeile 2");
    expect(body.textContent).not.toContain("\\n");
    expect(body.textContent).not.toContain("{");
    mockUser.current = { id: 42, login: "agent", is_admin: false };
  });

  it("opens the reply editor prefilled with the draft body and sends with ai_draft_id", async () => {
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 9,
          kind: "reply",
          subject: "Re: Draft subject",
          body: "AI drafted answer",
          source: "manual",
        },
      ],
    });

    wrap(<AiPanel ticketId={1} canNote />);
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-use-9")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-use-9"));

    await waitFor(() =>
      expect(screen.getByTestId("reply-dialog")).toBeTruthy(),
    );
    const body = screen.getByTestId("reply-body") as HTMLTextAreaElement;
    expect(body.value).toContain("AI drafted answer");

    fireEvent.click(screen.getByTestId("reply-send"));

    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    const payload = createArticle.mock.calls[0][1] as {
      ai_draft_id: number | null;
    };
    expect(payload.ai_draft_id).toBe(9);
  });

  it("routes 'Entwurf übernehmen' to the chat composer when the ticket is Telegram-dominant", async () => {
    listArticles.mockResolvedValue([
      {
        ...fakeArticle(3),
        communication_channel_id: 42,
        communication_channel_name: "Telegram",
        sender_type: "customer",
      },
    ]);
    getState.mockResolvedValue({
      ...baseState,
      manual_assist_available: true,
      drafts: [
        {
          ...baseDraft,
          id: 9,
          kind: "reply",
          subject: null,
          body: "AI drafted answer",
          source: "manual",
        },
      ],
    });

    const viewHandler = vi.fn();
    const composerHandler = vi.fn();
    function Listener() {
      useConversationViewRequests(1, viewHandler);
      useComposerRequests(1, composerHandler);
      return null;
    }

    wrap(
      <>
        <Listener />
        <AiPanel ticketId={1} canNote />
      </>,
    );
    await openCards();

    await waitFor(() =>
      expect(screen.getByTestId("ai-panel-draft-use-9")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("ai-panel-draft-use-9"));

    expect(viewHandler).toHaveBeenCalledOnce();
    expect(composerHandler).toHaveBeenCalledWith({
      draft: { id: 9, body: "AI drafted answer" },
      focus: true,
    });
    // The view switch must happen first — a split-view composer isn't
    // mounted yet to receive the draft (see composerBus.ts's buffering).
    expect(viewHandler.mock.invocationCallOrder[0]).toBeLessThan(
      composerHandler.mock.invocationCallOrder[0],
    );
    expect(screen.queryByTestId("reply-dialog")).not.toBeInTheDocument();
  });

  describe("header chips", () => {
    const summaryState = {
      ...baseState,
      summary_available: true,
      can_summarize: true,
      summary_body: "Stored summary",
      last_summary_upto_article_id: 42,
      summary_created_at: "2026-07-23T09:21:00",
    };

    it("shows the summary as one line and keeps the full controls closed until expanded", async () => {
      getState.mockResolvedValue({ ...summaryState, manual_assist_available: true });
      listArticles.mockResolvedValue([40, 41, 42].map(fakeArticle));

      wrap(<AiPanel ticketId={1} canNote />);

      expect(await screen.findByTestId("ai-summary-line-text")).toHaveTextContent("Stored summary");
      expect(screen.queryByTestId("ai-panel-summary-body")).toBeNull();
      expect(screen.getByTestId("ai-chip-drafts").textContent).toContain("0");
      // All three articles are covered: nothing stale to refresh.
      expect(screen.queryByTestId("ai-summary-stale-refresh")).toBeNull();

      fireEvent.click(screen.getByTestId("ai-chip-summary"));
      expect(await screen.findByTestId("ai-panel-summary-body")).toHaveTextContent("Stored summary");
      expect(screen.getByTestId("ai-chip-summary")).toHaveAttribute("aria-expanded", "true");
    });

    it("offers a one-click refresh when new articles arrived after the summary", async () => {
      getState.mockResolvedValue(summaryState);
      listArticles.mockResolvedValue([41, 42, 43, 44].map(fakeArticle));
      summarize.mockResolvedValue({ status: "started" });

      wrap(<AiPanel ticketId={1} canNote />);

      const stale = await screen.findByTestId("ai-summary-stale-refresh");
      expect(stale).toHaveTextContent("2");
      fireEvent.click(stale);
      await waitFor(() => expect(summarize).toHaveBeenCalledWith(1, "standard"));
    });

    it("hands its pieces to a render function instead of the default layout", async () => {
      getState.mockResolvedValue({ ...summaryState, manual_assist_available: true });
      wrap(
        <AiPanel ticketId={1} canNote>
          {(ai) => (
            <div>
              <div data-testid="slot-title">{ai.summaryLine}</div>
              <div data-testid="slot-reply">{ai.draftsButton}</div>
            </div>
          )}
        </AiPanel>,
      );
      await waitFor(() =>
        expect(screen.getByTestId("slot-title")).toContainElement(
          screen.getByTestId("ai-summary-line"),
        ),
      );
      expect(screen.getByTestId("slot-reply")).toContainElement(screen.getByTestId("ai-chip-drafts"));
      expect(screen.queryByTestId("ai-chips")).toBeNull();
    });

    it("renders the trailing chip even when no AI feature is available", async () => {
      getState.mockResolvedValue(baseState);
      wrap(<AiPanel ticketId={1} canNote trailing={<span data-testid="trailing-chip" />} />);
      await waitFor(() => expect(screen.getByTestId("trailing-chip")).toBeTruthy());
      expect(screen.queryByTestId("ai-panel")).toBeNull();
    });

    it("pins the summary below the chips and remembers it", async () => {
      getState.mockResolvedValue(summaryState);

      const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
      await openCards();
      fireEvent.click(await screen.findByTestId("ai-panel-summary-pin"));

      const pinnedCard = await screen.findByTestId("ai-panel-summary-pinned");
      expect(pinnedCard.textContent).toContain("Stored summary");
      expect(screen.getByTestId("ai-chip-summary").getAttribute("aria-pressed")).toBe("true");
      unmount();

      wrap(<AiPanel ticketId={1} canNote />);
      await waitFor(() => expect(screen.getByTestId("ai-panel-summary-pinned")).toBeTruthy());
      // The pressed chip unpins again.
      fireEvent.click(screen.getByTestId("ai-chip-summary"));
      expect(screen.queryByTestId("ai-panel-summary-pinned")).toBeNull();
    });

    it("runs a custom summary with the agent's own instruction", async () => {
      getState.mockResolvedValue({ ...summaryState, can_summarize: false });
      customSummary.mockResolvedValue({
        summary_body: "Für die Hausverwaltung: gesperrt wegen Malware.",
        created_at: "2026-09-23T08:00:00Z",
      });

      wrap(<AiPanel ticketId={1} canNote />);
      await openCards();
      fireEvent.click(await screen.findByTestId("ai-panel-summary-detail-custom"));
      // The stored-summary refresh button belongs to the other two views.
      expect(screen.queryByTestId("ai-panel-summarize-button")).toBeNull();

      const run = screen.getByTestId("ai-custom-summary-run");
      expect(run).toBeDisabled();
      fireEvent.change(screen.getByTestId("ai-custom-summary-input"), {
        target: { value: "  Für die Hausverwaltung, inkl. Timeline  " },
      });
      fireEvent.click(run);

      await waitFor(() =>
        expect(customSummary).toHaveBeenCalledWith(1, "Für die Hausverwaltung, inkl. Timeline"),
      );
      await waitFor(() =>
        expect(screen.getByTestId("ai-custom-summary-body").textContent).toContain(
          "gesperrt wegen Malware",
        ),
      );
      expect(summarize).not.toHaveBeenCalled();

      // Back to the stored summary is always possible, even with nothing to refresh.
      fireEvent.click(screen.getByTestId("ai-panel-summary-detail-standard"));
      expect(screen.getByTestId("ai-panel-summary-body").textContent).toBe("Stored summary");
    });

    it("saves instructions as templates and reuses them", async () => {
      getState.mockResolvedValue(summaryState);

      const { unmount } = wrap(<AiPanel ticketId={1} canNote />);
      await openCards();
      fireEvent.click(await screen.findByTestId("ai-panel-summary-detail-custom"));
      fireEvent.change(screen.getByTestId("ai-custom-summary-input"), {
        target: { value: "Nur offene Punkte" },
      });
      fireEvent.click(screen.getByTestId("ai-custom-summary-save"));
      expect(screen.getByTestId("ai-custom-summary-save")).toBeDisabled();
      unmount();

      wrap(<AiPanel ticketId={1} canNote />);
      await openCards();
      fireEvent.click(await screen.findByTestId("ai-panel-summary-detail-custom"));
      fireEvent.click(screen.getByTestId("ai-custom-summary-saved-0"));
      expect(
        (screen.getByTestId("ai-custom-summary-input") as HTMLTextAreaElement).value,
      ).toBe("Nur offene Punkte");

      fireEvent.click(screen.getByTestId("ai-custom-summary-remove-0"));
      expect(screen.queryByTestId("ai-custom-summary-saved-0")).toBeNull();
    });

    it("maps a failed custom summary to the shared LLM error text", async () => {
      getState.mockResolvedValue(summaryState);
      customSummary.mockRejectedValue(
        new ApiError(504, "llm_timeout: slow", "/api/v1/tickets/1/ai/summarize/custom"),
      );

      wrap(<AiPanel ticketId={1} canNote />);
      await openCards();
      fireEvent.click(await screen.findByTestId("ai-panel-summary-detail-custom"));
      fireEvent.change(screen.getByTestId("ai-custom-summary-input"), {
        target: { value: "Kurz" },
      });
      fireEvent.click(screen.getByTestId("ai-custom-summary-run"));
      await waitFor(() => expect(screen.getByTestId("ai-custom-summary-error")).toBeTruthy());
      expect(screen.getByTestId("ai-custom-summary-error").textContent).toBe(
        i18n.t("ticket.ai.errorLlmTimeout"),
      );
    });
  });

  describe("triage proposal", () => {
    const triage = {
      id: 7,
      status: "open",
      source_queue_id: 3,
      suggested_queue_id: 9,
      suggested_queue_name: "cn-nord",
      queue_confidence: 67,
      queue_reason: "technische stoerung",
      queue_votes: 2,
      extracted_email: null as string | null,
      suggested_customer_user_id: null as string | null,
      suggested_customer_name: null as string | null,
      customer_confidence: null as number | null,
      created_at: "2026-09-16T10:00:00",
    };

    it("renders the card even when manual assist and summary are both off", async () => {
      // A triage-only source queue enables neither feature, so the panel's
      // usual "nothing to show" early return must not hide the proposal.
      getState.mockResolvedValue({ ...baseState, triage });
      wrap(<AiPanel ticketId={1} canNote />);

      expect(await screen.findByTestId("ai-panel-triage")).toBeTruthy();
      expect(screen.getByTestId("ai-panel-triage-queue").textContent).toContain(
        "cn-nord",
      );
    });

    it("renders nothing when there is no proposal", async () => {
      getState.mockResolvedValue({ ...baseState, triage: null });
      const { container } = wrap(<AiPanel ticketId={1} canNote />);

      await waitFor(() => expect(getState).toHaveBeenCalled());
      expect(container.querySelector('[data-testid="ai-panel-triage"]')).toBe(
        null,
      );
    });

    it("accepts only the halves the proposal actually has", async () => {
      getState.mockResolvedValue({ ...baseState, triage });
      acceptTriage.mockResolvedValue(undefined);
      wrap(<AiPanel ticketId={1} canNote />);

      fireEvent.click(await screen.findByTestId("ai-panel-triage-accept"));

      await waitFor(() => expect(acceptTriage).toHaveBeenCalled());
      expect(acceptTriage).toHaveBeenCalledWith(1, 7, {
        queue: true,
        customer: false,
      });
    });

    it("sends the customer half when the proposal resolved a customer", async () => {
      getState.mockResolvedValue({
        ...baseState,
        triage: {
          ...triage,
          extracted_email: "s00mmust@uni.example.org",
          suggested_customer_user_id: "s00mmust@uni.example.org",
          suggested_customer_name: "Max Muster",
          customer_confidence: 100,
        },
      });
      acceptTriage.mockResolvedValue(undefined);
      wrap(<AiPanel ticketId={1} canNote />);

      expect(
        (await screen.findByTestId("ai-panel-triage-customer")).textContent,
      ).toContain("s00mmust@uni.example.org");

      fireEvent.click(screen.getByTestId("ai-panel-triage-accept"));
      await waitFor(() => expect(acceptTriage).toHaveBeenCalled());
      expect(acceptTriage).toHaveBeenCalledWith(1, 7, {
        queue: true,
        customer: true,
      });
    });

    it("rejects the proposal", async () => {
      getState.mockResolvedValue({ ...baseState, triage });
      rejectTriage.mockResolvedValue(undefined);
      wrap(<AiPanel ticketId={1} canNote />);

      fireEvent.click(await screen.findByTestId("ai-panel-triage-reject"));

      await waitFor(() => expect(rejectTriage).toHaveBeenCalledWith(1, 7));
    });

    it("disables both actions without note permission", async () => {
      getState.mockResolvedValue({ ...baseState, triage });
      wrap(<AiPanel ticketId={1} canNote={false} />);

      const accept = (await screen.findByTestId(
        "ai-panel-triage-accept",
      )) as HTMLButtonElement;
      const reject = screen.getByTestId(
        "ai-panel-triage-reject",
      ) as HTMLButtonElement;
      expect(accept.disabled).toBe(true);
      expect(reject.disabled).toBe(true);
    });

    it("surfaces a 403 from accept instead of failing silently", async () => {
      // The worker may move into a queue the agent cannot: the automatic
      // path skips the move_into check, the accept path does not.
      getState.mockResolvedValue({ ...baseState, triage });
      acceptTriage.mockRejectedValue(
        new ApiError(403, "Forbidden", "/api/v1/tickets/1/ai/triage/7/accept"),
      );
      wrap(<AiPanel ticketId={1} canNote />);

      fireEvent.click(await screen.findByTestId("ai-panel-triage-accept"));

      expect(await screen.findByTestId("ai-panel-triage-error")).toBeTruthy();
    });
  });
});
