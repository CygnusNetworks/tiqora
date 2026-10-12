import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DebugEntryDetail } from "@/lib/webservicesApi";
import { demoDryRunJob } from "@/demo/giMock";
import { DebuggerEntryDrawer } from "./DebuggerEntryDrawer";

const debuggerEntry = vi.fn();
const resend = vi.fn();
vi.mock("@/lib/webservicesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/webservicesApi")>("@/lib/webservicesApi");
  return {
    ...actual,
    webservicesApi: {
      ...actual.webservicesApi,
      debuggerEntry: (...a: unknown[]) => debuggerEntry(...a),
      resend: (...a: unknown[]) => resend(...a),
    },
  };
});
const getJob = vi.fn();
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: (...a: unknown[]) => getJob(...a) } };
});

function detail(canResend: boolean): DebugEntryDetail {
  return {
    entry: {
      source: "tiqora", id: 11, communication_id: "comm-11", communication_type: "Requester",
      name: "SyncTicket", remote_ip: null, ticket_id: 105, created: "2026-10-11T08:00:00Z",
      max_level: "error", can_resend: canResend,
    },
    contents: [
      { id: 1, debug_level: "info", subject: "Outgoing data", created: "2026-10-11T08:00:01Z",
        content: '{"TicketID":105,"Title":"Router"}', masked: false },
      { id: 2, debug_level: "error", subject: "Request failed", created: "2026-10-11T08:00:02Z",
        content: "Authorization: ***", masked: true },
    ],
  };
}

const onClose = vi.fn();
const onResent = vi.fn();
function renderDrawer(
  canResend = true,
  opts: { startResend?: boolean; debugThreshold?: string } = {},
) {
  debuggerEntry.mockResolvedValue(detail(canResend));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DebuggerEntryDrawer
          wsId={7}
          source="tiqora"
          entryId={11}
          onClose={onClose}
          onResent={onResent}
          startResend={opts.startResend}
          debugThreshold={opts.debugThreshold}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DebuggerEntryDrawer", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    debuggerEntry.mockReset();
    resend.mockReset().mockResolvedValue({ job_id: 9390, status: "queued" });
    getJob.mockReset().mockResolvedValue({
      ...demoDryRunJob,
      status: "succeeded",
      result: { communication_id: "c1", status: "ok" },
    });
    onClose.mockReset();
    onResent.mockReset();
  });

  it("collapses content rows, pretty-prints JSON and masks are flagged", async () => {
    renderDrawer();
    const toggle = await screen.findByTestId("debug-line-toggle-1");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("debug-line-content-1")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("debug-line-content-1").textContent).toContain('"TicketID": 105');
    fireEvent.click(toggle);
    expect(screen.queryByTestId("debug-line-content-1")).toBeNull();
    // error lines start open, and a masked line says so
    expect(screen.getByTestId("debug-line-content-2")).toHaveTextContent("Authorization: ***");
    expect(screen.getByTestId("debug-line-masked-2")).toHaveTextContent("Von Tiqora maskiert");
    expect(screen.queryByTestId("debug-line-masked-1")).toBeNull();
  });

  it("copies the raw content", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderDrawer();
    fireEvent.click(await screen.findByTestId("debug-line-copy-1"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('{"TicketID":105,"Title":"Router"}'));
    expect(await screen.findByTestId("debug-line-copy-1")).toHaveTextContent("Kopiert");
  });

  it("asks for confirmation with the exact text, then posts and follows the job", async () => {
    renderDrawer();
    fireEvent.click(await screen.findByTestId("debug-resend"));
    expect(screen.getByTestId("confirm-dialog")).toHaveTextContent(
      "Der Aufruf wird mit den aktuellen Ticketdaten erneut gesendet. Fortfahren?",
    );
    expect(resend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(resend).toHaveBeenCalledWith(7, 11));
    expect(await screen.findByTestId("debug-resend-job")).toBeInTheDocument();
    expect(await screen.findByTestId("debug-resend-done", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByTestId("debug-resend-sent")).toHaveTextContent("Der neue Eintrag steht oben im Protokoll.");
    fireEvent.click(screen.getByTestId("debug-resend-show-log"));
    expect(onResent).toHaveBeenCalled();
  });

  async function resendAndWait() {
    fireEvent.click(await screen.findByTestId("debug-resend"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await screen.findByTestId("debug-resend-job", {}, { timeout: 3000 });
  }

  it("reports a rescheduled resend in amber, not as sent", async () => {
    getJob.mockResolvedValue({
      ...demoDryRunJob,
      status: "succeeded",
      result: { communication_id: "c1", status: "rescheduled", next_at: "2026-10-12T10:00:00" },
    });
    renderDrawer();
    await resendAndWait();
    const note = await screen.findByTestId("debug-resend-rescheduled", {}, { timeout: 3000 });
    expect(note).toHaveTextContent("Das fremde System hat den Aufruf abgelehnt.");
    expect(note).toHaveTextContent(new Date("2026-10-12T10:00:00Z").toLocaleString());
    expect(note.className).toContain("text-amber");
    expect(screen.queryByTestId("debug-resend-sent")).toBeNull();
  });

  it("does not promise a new entry when the threshold logs no successful call", async () => {
    renderDrawer(true, { debugThreshold: "error" });
    await resendAndWait();
    const sent = await screen.findByTestId("debug-resend-sent", {}, { timeout: 3000 });
    expect(sent).toHaveTextContent("schreibt ein erfolgreicher Aufruf keinen Eintrag");
    expect(sent).not.toHaveTextContent("oben im Protokoll");
  });

  it("shows a failed job's error once and offers to send again", async () => {
    getJob.mockResolvedValue({ ...demoDryRunJob, status: "failed", result: null, error: "Remote kaputt" });
    renderDrawer();
    await resendAndWait();
    await waitFor(() => expect(screen.getAllByText("Remote kaputt")).toHaveLength(1), { timeout: 3000 });
    expect(screen.queryByTestId("debug-resend-job-failed")).toBeNull();
    expect(screen.getByTestId("debug-resend")).toBeInTheDocument();
  });

  it("Escape closes the drawer when the row's resend opened it for a non-resendable entry", async () => {
    renderDrawer(false, { startResend: true });
    await screen.findByTestId("debug-line-toggle-1");
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("cancelling the confirmation sends nothing and keeps the drawer open", async () => {
    renderDrawer();
    fireEvent.click(await screen.findByTestId("debug-resend"));
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    expect(resend).not.toHaveBeenCalled();
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows the API error message when resending fails", async () => {
    const { ApiError } = await import("@/lib/api");
    resend.mockRejectedValue(new ApiError(429, { detail: "Zu viele Aufträge" }, "/x"));
    renderDrawer();
    fireEvent.click(await screen.findByTestId("debug-resend"));
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("debug-resend-error")).toHaveTextContent("Zu viele Aufträge");
  });

  it("offers no resend for entries that cannot be resent", async () => {
    renderDrawer(false);
    await screen.findByTestId("debug-line-toggle-1");
    expect(screen.queryByTestId("debug-resend")).toBeNull();
  });

  it("closes on Escape", async () => {
    renderDrawer();
    await screen.findByTestId("debug-line-toggle-1");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
