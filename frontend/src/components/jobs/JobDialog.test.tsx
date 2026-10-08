import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import type { Job } from "@/lib/jobsApi";
import { JobDialog } from "./JobDialog";

const { get, cancel } = vi.hoisted(() => ({ get: vi.fn(), cancel: vi.fn() }));

vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get, cancel } };
});

function Providers({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </I18nextProvider>
  );
}

function makeJob(overrides: Partial<Job>): Job {
  return {
    id: 7,
    kind: "core.ticket_export",
    status: "running",
    title_key: "jobs.kinds.core.ticket_export",
    progress_done: 0,
    progress_total: null,
    progress_message: null,
    cancel_requested: false,
    error: null,
    result: null,
    files: [],
    created_by: 5,
    created_at: "2026-10-08T10:00:00Z",
    started_at: "2026-10-08T10:00:01Z",
    finished_at: null,
    ...overrides,
  } as Job;
}

describe("JobDialog", () => {
  beforeEach(() => {
    get.mockReset();
    cancel.mockReset();
    void i18n.changeLanguage("de");
  });

  it("shows progress, then a download link", async () => {
    get.mockResolvedValueOnce(makeJob({ progress_done: 3, progress_total: 10 }));
    get.mockResolvedValue(
      makeJob({
        status: "succeeded",
        progress_done: 10,
        progress_total: 10,
        finished_at: "2026-10-08T10:00:05Z",
        files: [{ id: 1, name: "tickets.csv", content_type: "text/csv", size_bytes: 64 }],
      }),
    );
    render(
      <Providers>
        <JobDialog jobId={7} title="Export" onClose={() => {}} />
      </Providers>,
    );
    expect(await screen.findByTestId("job-dialog-progress")).toHaveTextContent("3 von 10");
    const link = await screen.findByTestId("job-dialog-file-1", undefined, { timeout: 3000 });
    expect(link).toHaveAttribute("href", "/api/v1/jobs/7/files/1");
    expect(screen.queryByTestId("job-dialog-cancel")).toBeNull();
    expect(screen.getByTestId("job-dialog-close")).toBeInTheDocument();
  });

  it("cancel calls the API while running", async () => {
    get.mockResolvedValue(makeJob({ progress_done: 1, progress_total: 10 }));
    cancel.mockResolvedValue(makeJob({ cancel_requested: true, progress_done: 1, progress_total: 10 }));
    render(
      <Providers>
        <JobDialog jobId={7} title="Export" onClose={() => {}} />
      </Providers>,
    );
    fireEvent.click(await screen.findByTestId("job-dialog-cancel"));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(7));
  });

  it("shows the server's error text on failure", async () => {
    get.mockResolvedValue(makeJob({ status: "failed", error: "Zeitlimit" }));
    render(
      <Providers>
        <JobDialog jobId={7} title="Export" onClose={() => {}} />
      </Providers>,
    );
    expect(await screen.findByTestId("job-dialog-error")).toHaveTextContent("Zeitlimit");
    expect(screen.getByTestId("job-dialog-close")).toBeInTheDocument();
  });

  it("renders nothing without a job id", () => {
    render(
      <Providers>
        <JobDialog jobId={null} title="Export" onClose={() => {}} />
      </Providers>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });
});
