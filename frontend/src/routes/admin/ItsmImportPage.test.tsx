import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ItsmImportPage } from "./ItsmImportPage";
import type { ImportRunOut, ImportStatus } from "@/lib/itsmImportApi";

const status = vi.fn();
const runs = vi.fn();
const startRun = vi.fn();
const releaseLock = vi.fn();

vi.mock("@/lib/itsmImportApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/itsmImportApi")>()),
  itsmImportApi: {
    status: (...a: unknown[]) => status(...a),
    runs: (...a: unknown[]) => runs(...a),
    startRun: (...a: unknown[]) => startRun(...a),
    releaseLock: (...a: unknown[]) => releaseLock(...a),
  },
}));
vi.mock("@/lib/jobsApi", () => ({
  jobsApi: { get: vi.fn().mockRejectedValue(new Error("gone")), fileUrl: () => "#" },
}));
vi.mock("@/components/jobs/JobDialog", () => ({
  JobDialog: ({ jobId, testId = "job-dialog" }: { jobId: number | null; testId?: string }) =>
    jobId == null ? null : <div data-testid={testId}>job {jobId}</div>,
}));

function statusFixture(over: Partial<ImportStatus> = {}): ImportStatus {
  return {
    mode: "znuny_readthrough",
    znuny_itsm_present: true,
    profile: "ITSM 6.5",
    can_import: true,
    can_switch: false,
    blocked_reason: null,
    active_job_id: null,
    readonly_flag: false,
    switched: null,
    znuny_changes_since_switch: null,
    znuny_counts: { cis: 41, classes: 3, changes: 12, workorders: 30 },
    last_run: null,
    ...over,
  };
}

const RUN: ImportRunOut = {
  id: 3,
  kind: "dry_run",
  status: "done",
  started_at: "2026-10-12T09:00:00Z",
  finished_at: null,
  started_by_name: "Erika Beispiel",
  job_id: null,
  profile: null,
  message: null,
  issue_count: 0,
  issues: [],
  steps: { links: { created: 1, updated: 0, unchanged: 0, skipped: 0, problems: 0 } },
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ItsmImportPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ItsmImportPage", () => {
  beforeEach(async () => {
    status.mockReset().mockResolvedValue(statusFixture());
    runs.mockReset().mockResolvedValue([]);
    startRun.mockReset().mockResolvedValue({ job_id: 7, status: "queued" });
    releaseLock.mockReset().mockResolvedValue(statusFixture());
    await i18n.changeLanguage("de");
  });

  it("shows the mode and the Znuny counts", async () => {
    renderPage();
    expect(await screen.findByText("Znuny führt das ITSM")).toBeInTheDocument();
    expect(screen.getByText("Znuny ITSM gefunden: ITSM 6.5")).toBeInTheDocument();
    expect(screen.getByTestId("itsm-import-count-cis")).toHaveTextContent("41");
  });

  it("starts a dry run and opens the job dialog", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Probelauf" }));
    await waitFor(() => expect(startRun).toHaveBeenCalledWith(true));
    expect(await screen.findByTestId("job-dialog")).toHaveTextContent("job 7");
  });

  it("disables import with the blocked reason", async () => {
    const reason = "Die ITSM-Daten werden bereits in Tiqora geführt.";
    status.mockResolvedValue(statusFixture({ can_import: false, blocked_reason: reason, mode: "tiqora" }));
    renderPage();
    const btn = await screen.findByRole("button", { name: "Übernehmen" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", reason);
    expect(screen.getByRole("button", { name: "Probelauf" })).toBeDisabled();
    expect(screen.getByText(reason)).toBeInTheDocument();
  });

  it("asks for confirmation before a real import", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Übernehmen" }));
    expect(startRun).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/Die Daten werden in die CMDB von Tiqora geschrieben\./),
    ).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Übernehmen" });
    fireEvent.click(buttons[buttons.length - 1]!);
    await waitFor(() => expect(startRun).toHaveBeenCalledWith(false));
    expect(await screen.findByTestId("job-dialog")).toBeInTheDocument();
  });

  it("lists runs and expands the report", async () => {
    runs.mockResolvedValue([RUN]);
    renderPage();
    expect(await screen.findByText("Probelauf", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("Erika Beispiel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Bericht" }));
    expect(await screen.findByTestId("itsm-import-step-links")).toBeInTheDocument();
  });

  it("offers to release a stale read-only lock", async () => {
    status.mockResolvedValue(statusFixture({ readonly_flag: true, can_import: false, blocked_reason: "Gesperrt." }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Sperre aufheben" }));
    await waitFor(() => expect(releaseLock).toHaveBeenCalled());
  });
});
