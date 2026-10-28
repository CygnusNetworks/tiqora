import { StrictMode } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { ApprovalLinkInspect } from "@/lib/approvalLinkApi";
import { ApprovalLinkPage } from "./ApprovalLinkPage";

const { inspect, decide } = vi.hoisted(() => ({ inspect: vi.fn(), decide: vi.fn() }));
vi.mock("@/lib/approvalLinkApi", () => ({ approvalLinkApi: { inspect, decide } }));

const INFO: ApprovalLinkInspect = {
  valid: true,
  code: null,
  title: "Change freigeben",
  ticket_number: "2026101012345",
  ticket_title: "Switch tauschen",
  step_name: "CAB",
  rule: "all",
  approver_name: "Erika Muster",
  due_at: "2030-01-15T12:00:00Z",
  description: "Bitte prüfen.",
  describe_lines: ["Risiko: niedrig", "Fenster: Sonntag"],
};

function apiError(status: number, code: string, retryAfter?: number) {
  return new ApiError(status, { detail: { code, detail: "x" } }, "/api/v1/approval-links/x", retryAfter);
}

function show(strict = false) {
  const page = (
    <I18nextProvider i18n={i18n}>
      <ApprovalLinkPage />
    </I18nextProvider>
  );
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}

let replaceState: ReturnType<typeof vi.spyOn>;

beforeEach(async () => {
  await i18n.changeLanguage("de");
  inspect.mockReset();
  decide.mockReset();
  window.history.replaceState(null, "", "/approve#t=tok-123");
  replaceState = vi.spyOn(window.history, "replaceState");
});
afterEach(() => {
  replaceState.mockRestore();
  vi.useRealTimers();
});

describe("ApprovalLinkPage", () => {
  it("reads the token from the fragment, strips it to /approve and inspects exactly once under StrictMode", async () => {
    inspect.mockResolvedValue(INFO);
    show(true);
    await screen.findByTestId("approval-link-approver");
    expect(inspect).toHaveBeenCalledTimes(1);
    expect(inspect).toHaveBeenCalledWith("tok-123");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/approve");
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("");
    expect(window.location.pathname).toBe("/approve");
  });

  it("shows the request details", async () => {
    inspect.mockResolvedValue(INFO);
    show();
    expect(await screen.findByTestId("approval-link-request-title")).toHaveTextContent("Change freigeben");
    expect(screen.getByTestId("approval-link-ticket")).toHaveTextContent("Ticket 2026101012345 · Switch tauschen");
    expect(screen.getByTestId("approval-link-step")).toHaveTextContent("Schritt: CAB");
    expect(screen.getByTestId("approval-link-rule")).toHaveTextContent("Alle Freigeber müssen zustimmen");
    expect(screen.getByTestId("approval-link-due")).toHaveTextContent(/^Frist: .*2030/);
    expect(screen.getByTestId("approval-link-lines").querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByTestId("approval-link-approver")).toHaveTextContent("Sie entscheiden als Erika Muster");
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeEnabled();
  });

  it("makes no request without a fragment token", async () => {
    window.history.replaceState(null, "", "/approve");
    show();
    expect(await screen.findByText("Der Link ist unvollständig.")).toBeInTheDocument();
    expect(inspect).not.toHaveBeenCalled();
  });

  it("approves without a comment and shows the saved request status", async () => {
    inspect.mockResolvedValue(INFO);
    decide.mockResolvedValue({ decision: "approved", status: "approved" });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));
    expect(await screen.findByText("Danke, Ihre Freigabe wurde gespeichert.")).toBeInTheDocument();
    expect(decide).toHaveBeenCalledWith("tok-123", "approved", "");
    expect(screen.getByTestId("approval-link-status")).toHaveTextContent("Status der Anfrage: Freigegeben");
  });

  it("rejecting needs a comment, then sends it", async () => {
    inspect.mockResolvedValue(INFO);
    decide.mockResolvedValue({ decision: "rejected", status: "rejected" });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Ablehnen" }));
    expect(screen.getByTestId("approval-link-comment-error")).toHaveTextContent(
      "Bitte geben Sie eine Begründung für die Ablehnung an.",
    );
    expect(decide).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("approval-link-comment"), { target: { value: "Zu riskant" } });
    fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));
    expect(await screen.findByText("Ihre Ablehnung wurde gespeichert.")).toBeInTheDocument();
    expect(decide).toHaveBeenCalledWith("tok-123", "rejected", "Zu riskant");
    expect(screen.getByTestId("approval-link-status")).toHaveTextContent("Status der Anfrage: Abgelehnt");
  });

  it("caps the comment at 2000 characters and counts", async () => {
    inspect.mockResolvedValue(INFO);
    show();
    const box = await screen.findByTestId("approval-link-comment");
    expect(box).toHaveAttribute("maxlength", "2000");
    fireEvent.change(box, { target: { value: "abc" } });
    expect(screen.getByTestId("approval-link-counter")).toHaveTextContent("3 / 2000");
  });

  it.each([
    ["link_invalid", "Dieser Link ist nicht mehr gültig. Möglicherweise wurde bereits entschieden oder die Freigabe zurückgezogen."],
    ["link_expired", "Dieser Link ist abgelaufen. Bitte melden Sie sich an und entscheiden Sie unter „Meine Freigaben“."],
    ["link_used", "Über diese Freigabe wurde mit diesem Link bereits entschieden."],
  ])("inspect answering %s shows its own text", async (code, text) => {
    inspect.mockResolvedValue({ valid: false, code });
    show();
    expect(await screen.findByTestId("approval-link-error")).toHaveTextContent(text);
    expect(screen.queryByRole("button", { name: "Freigeben" })).toBeNull();
  });

  it.each([
    ["link_used", "Über diese Freigabe wurde mit diesem Link bereits entschieden."],
    ["link_expired", "Dieser Link ist abgelaufen. Bitte melden Sie sich an und entscheiden Sie unter „Meine Freigaben“."],
  ])("decide failing with %s replaces the card with the error", async (code, text) => {
    inspect.mockResolvedValue(INFO);
    decide.mockRejectedValue(apiError(410, code));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));
    expect(await screen.findByTestId("approval-link-error")).toHaveTextContent(text);
  });

  it("inspect rate limit shows the text and the Retry-After wait", async () => {
    inspect.mockRejectedValue(apiError(429, "rate_limited", 120));
    show();
    const box = await screen.findByTestId("approval-link-error");
    expect(box).toHaveTextContent("Zu viele Versuche. Bitte warten Sie einige Minuten.");
    expect(box).toHaveTextContent("Bitte versuchen Sie es in 120 Sekunden erneut.");
  });

  it("decide rate limit disables both buttons until Retry-After has passed", async () => {
    inspect.mockResolvedValue(INFO);
    decide.mockRejectedValue(apiError(429, "rate_limited", 3));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));
    expect(await screen.findByTestId("approval-link-form-error")).toHaveTextContent(
      "Zu viele Versuche. Bitte warten Sie einige Minuten.",
    );
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeDisabled();
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeEnabled();
  });

  it("an unexpected decide error keeps the card and allows another try", async () => {
    inspect.mockResolvedValue(INFO);
    decide.mockRejectedValue(new ApiError(500, "boom", "/api/v1/approval-links/decide"));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));
    await waitFor(() =>
      expect(screen.getByTestId("approval-link-form-error")).toHaveTextContent(
        "Die Freigabe konnte nicht verarbeitet werden. Bitte versuchen Sie es erneut.",
      ),
    );
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeEnabled();
  });
});
