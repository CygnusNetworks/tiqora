import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { AutopilotSwitch } from "./AutopilotSwitch";

const { getState, autopilot } = vi.hoisted(() => ({
  getState: vi.fn(),
  autopilot: vi.fn(),
}));

vi.mock("@/lib/ticketAiApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/ticketAiApi")>("@/lib/ticketAiApi");
  return { ...actual, ticketAiApi: { getState, autopilot } };
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

describe("AutopilotSwitch", () => {
  beforeEach(() => {
    getState.mockReset();
    autopilot.mockReset();
  });

  it("is absent when the queue has no auto-reply", async () => {
    getState.mockResolvedValue({ ...baseState, autopilot: { mode: "unavailable" } });
    const { container } = wrap(<AutopilotSwitch ticketId={7} canNote />);
    await waitFor(() => expect(getState).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("shows the remaining replies of a release and stops it", async () => {
    getState.mockResolvedValue({
      ...baseState,
      autopilot: { mode: "active", grant_remaining: 2, grant_total: 3 },
    });
    autopilot.mockResolvedValue({ mode: "stopped", by_name: "Erika" });
    wrap(<AutopilotSwitch ticketId={7} canNote />);

    const pill = await screen.findByTestId("autopilot-switch");
    expect(pill).toHaveAttribute("data-mode", "active");
    expect(pill.textContent).toContain("2 of 3 replies left");

    fireEvent.click(pill);
    fireEvent.click(await screen.findByTestId("autopilot-stop"));
    await waitFor(() => expect(autopilot).toHaveBeenCalledWith(7, { action: "stop" }));
  });

  it("switches a handed-over ticket back on for N replies and answers now", async () => {
    getState.mockResolvedValue({
      ...baseState,
      autopilot: {
        mode: "handed_over",
        reason: "max_clarifications",
        unanswered_customer_message: true,
      },
    });
    autopilot.mockResolvedValue({ mode: "active", grant_remaining: 4, grant_total: 5 });
    wrap(<AutopilotSwitch ticketId={7} canNote />);

    const pill = await screen.findByTestId("autopilot-switch");
    expect(pill.textContent).toContain("Clarification limit reached");
    fireEvent.click(pill);

    fireEvent.click(await screen.findByTestId("autopilot-runs-5"));
    expect(screen.getByTestId("autopilot-runs-5")).toHaveAttribute("aria-pressed", "true");
    const answerNow = screen.getByTestId("autopilot-answer-now");
    expect(answerNow).toBeChecked();
    fireEvent.click(screen.getByTestId("autopilot-start"));

    await waitFor(() =>
      expect(autopilot).toHaveBeenCalledWith(7, {
        action: "start",
        runs: 5,
        answer_latest: true,
      }),
    );
  });

  it("offers no 'answer now' when nothing is waiting", async () => {
    getState.mockResolvedValue({
      ...baseState,
      autopilot: { mode: "stopped", by_name: "Erika", unanswered_customer_message: false },
    });
    autopilot.mockResolvedValue({ mode: "active", grant_remaining: 3, grant_total: 3 });
    wrap(<AutopilotSwitch ticketId={7} canNote />);

    const pill = await screen.findByTestId("autopilot-switch");
    expect(pill.textContent).toContain("Erika");
    fireEvent.click(pill);
    await screen.findByTestId("autopilot-start");
    expect(screen.queryByTestId("autopilot-answer-now")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("autopilot-start"));
    await waitFor(() =>
      expect(autopilot).toHaveBeenCalledWith(7, {
        action: "start",
        runs: 3,
        answer_latest: false,
      }),
    );
  });

  it("is read-only without note permission", async () => {
    getState.mockResolvedValue({ ...baseState, autopilot: { mode: "active" } });
    wrap(<AutopilotSwitch ticketId={7} canNote={false} />);
    expect(await screen.findByTestId("autopilot-switch")).toBeDisabled();
  });

  it("says so when the change fails", async () => {
    getState.mockResolvedValue({ ...baseState, autopilot: { mode: "active" } });
    autopilot.mockRejectedValue(new Error("boom"));
    wrap(<AutopilotSwitch ticketId={7} canNote />);
    fireEvent.click(await screen.findByTestId("autopilot-switch"));
    fireEvent.click(await screen.findByTestId("autopilot-stop"));
    expect(await screen.findByTestId("autopilot-error")).toBeInTheDocument();
  });
});
