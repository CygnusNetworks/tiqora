import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { ReportingAdminPage } from "./ReportingAdminPage";
import type { ReportingStatus } from "@/lib/reportsApi";

const adminStatus = vi.fn();
const adminBackfill = vi.fn();
const jobGet = vi.fn();

vi.mock("@/lib/jobsApi", () => ({ jobsApi: { get: (...a: unknown[]) => jobGet(...a) } }));

vi.mock("@/lib/reportsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/reportsApi")>()),
  reportsApi: {
    adminStatus: (...a: unknown[]) => adminStatus(...a),
    adminBackfill: (...a: unknown[]) => adminBackfill(...a),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: ReactNode }) => (
    <a href={to.replace("$section", params?.section ?? "")} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/jobs/JobDialog", () => ({
  JobDialog: ({ jobId, testId }: { jobId: number | null; testId?: string }) =>
    jobId == null ? null : <div data-testid={testId}>job {jobId}</div>,
}));

const status: ReportingStatus = {
  ready: true,
  as_of: "2026-10-08T10:00:00Z",
  backfill_status: "done",
  backfill_job_id: null,
  backfill_progress: 1,
  last_history_id: 48213,
  history_lag: 12,
  gap_count: 0,
  dirty_count: 3,
  last_snapshot_date: "2026-10-07",
  snapshot_time_zone: "Europe/Berlin",
  fact_rows: 1840,
  event_rows: 9620,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ReportingAdminPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ReportingAdminPage", () => {
  beforeEach(async () => {
    adminStatus.mockReset().mockResolvedValue(status);
    jobGet.mockReset().mockResolvedValue({ id: 9202, status: "queued" });
    adminBackfill.mockReset().mockResolvedValue({ job_id: 9202, status: "queued" });
    await i18n.changeLanguage("de");
  });

  it("shows status numbers", async () => {
    renderPage();
    expect(await screen.findByTestId("admin-reporting-state")).toHaveTextContent("Bereit");
    expect(screen.getByTestId("admin-reporting-lag")).toHaveTextContent("12");
    expect(screen.getByTestId("admin-reporting-dirty")).toHaveTextContent("3");
    expect(screen.getByTestId("admin-reporting-fact-rows")).toHaveTextContent("1.840");
    expect(screen.getByTestId("admin-reporting-last-snapshot")).toHaveTextContent("07.10.2026");
    expect(screen.getByTestId("admin-reporting-as-of")).not.toHaveTextContent("—");
  });

  it("shows build progress while the first build runs", async () => {
    adminStatus.mockResolvedValue({ ...status, ready: false, as_of: null, backfill_status: "running", backfill_progress: 0.42 });
    renderPage();
    expect(await screen.findByTestId("admin-reporting-state")).toHaveTextContent("42 %");
  });

  it("starts a rebuild with an optional start date and shows progress", async () => {
    renderPage();
    await screen.findByTestId("admin-reporting-state");
    fireEvent.change(screen.getByTestId("admin-reporting-since"), { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByTestId("admin-reporting-rebuild"));
    expect(adminBackfill).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        "Alle Berichtsdaten werden neu berechnet. Das kann bei vielen Tickets Stunden dauern. Bis dahin zeigen die Berichte einen Hinweis und sind noch nicht vollständig.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() =>
      expect(adminBackfill).toHaveBeenCalledWith({ since: "2024-01-01", restart: true, snapshots: true }),
    );
    expect(await screen.findByTestId("admin-reporting-job")).toHaveTextContent("job 9202");
  });

  it("shows a dash for missing dates", async () => {
    adminStatus.mockResolvedValue({ ...status, ready: false, as_of: null, last_snapshot_date: null, snapshot_time_zone: null });
    renderPage();
    await screen.findByTestId("admin-reporting-state");
    expect(screen.getByTestId("admin-reporting-as-of")).toHaveTextContent("—");
    expect(screen.getByTestId("admin-reporting-last-snapshot")).toHaveTextContent("—");
  });

  it("disables the rebuild button while a build runs", async () => {
    adminStatus.mockResolvedValue({ ...status, ready: false, backfill_status: "running", backfill_progress: 0.1, backfill_job_id: 9202 });
    renderPage();
    await screen.findByTestId("admin-reporting-state");
    expect(screen.getByTestId("admin-reporting-rebuild")).toBeDisabled();
  });

  it("shows a queued build and disables the button", async () => {
    adminStatus.mockResolvedValue({ ...status, ready: false, backfill_status: "pending", backfill_progress: null, backfill_job_id: 9202 });
    renderPage();
    expect(await screen.findByText("Aufbau ist eingeplant")).toBeInTheDocument();
    expect(screen.getByTestId("admin-reporting-rebuild")).toBeDisabled();
  });

  it("shows a failed build and allows a new rebuild", async () => {
    jobGet.mockResolvedValue({ id: 9202, status: "failed" });
    adminStatus.mockResolvedValue({ ...status, ready: false, backfill_status: "pending", backfill_progress: null, backfill_job_id: 9202 });
    renderPage();
    expect(await screen.findByText("Der letzte Aufbau ist fehlgeschlagen")).toBeInTheDocument();
    expect(screen.getByTestId("admin-reporting-rebuild")).toBeEnabled();
  });

  it("sends since null without a date", async () => {
    renderPage();
    await screen.findByTestId("admin-reporting-state");
    fireEvent.click(screen.getByTestId("admin-reporting-rebuild"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() =>
      expect(adminBackfill).toHaveBeenCalledWith({ since: null, restart: true, snapshots: true }),
    );
  });

  it("links to the settings section", () => {
    renderPage();
    expect(screen.getByTestId("admin-reporting-settings")).toHaveAttribute("href", "/admin/settings/reporting");
  });
});
