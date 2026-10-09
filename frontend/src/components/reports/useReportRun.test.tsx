import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { jobsApi, type Job } from "@/lib/jobsApi";
import { reportErrorMessage, reportsApi, type ReportResult, type ReportRunIn } from "@/lib/reportsApi";
import { useReportRun } from "./useReportRun";

vi.mock("@/lib/reportsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/reportsApi")>()),
  reportsApi: { run: vi.fn() },
}));
vi.mock("@/lib/jobsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/jobsApi")>()),
  jobsApi: { get: vi.fn(), cancel: vi.fn() },
}));

const body: ReportRunIn = { report_key: "builtin.volume" } as ReportRunIn;
const resultOf = (rows: number): ReportResult =>
  ({ report_key: "builtin.volume", columns: [], rows: [], row_count: rows }) as unknown as ReportResult;
const job = (status: Job["status"], extra: Partial<Job> = {}): Job =>
  ({ id: 5, status, result: null, error: null, files: [], ...extra }) as Job;

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.mocked(reportsApi.run).mockReset();
  vi.mocked(jobsApi.get).mockReset();
});

describe("useReportRun", () => {
  it("returns an inline result", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: resultOf(7), job: null });
    const { result } = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.result?.row_count).toBe(7));
    expect(result.current.jobId).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(jobsApi.get).not.toHaveBeenCalled();
  });

  it("does nothing without a body or when disabled", () => {
    const { result } = renderHook(() => useReportRun(null), { wrapper: wrapper() });
    expect(result.current.isLoading).toBe(false);
    renderHook(() => useReportRun(body, { enabled: false }), { wrapper: wrapper() });
    expect(reportsApi.run).not.toHaveBeenCalled();
  });

  it("polls the job and returns its result", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get)
      .mockResolvedValueOnce(job("running"))
      .mockResolvedValue(job("succeeded", { result: resultOf(3) as unknown as Job["result"] }));
    const { result } = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    expect(result.current.result).toBeUndefined();
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.result?.row_count).toBe(3), { timeout: 4000 });
    expect(result.current.isLoading).toBe(false);
    expect(vi.mocked(jobsApi.get).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("surfaces a failed job as error", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("failed", { error: "Zu groß" }));
    const { result } = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    expect((result.current.error as Error).message).toBe("Zu groß");
    expect(result.current.result).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });

  it("keeps the server's German message for 429 and 504", async () => {
    const msg = "Es laufen schon 3 Berichte. Bitte warten.";
    vi.mocked(reportsApi.run).mockRejectedValue(
      new ApiError(429, { detail: { message: msg } }, "/api/v1/reports/run"),
    );
    const { result } = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(reportErrorMessage(result.current.error, "Fehler")).toBe(msg);
    expect(result.current.isLoading).toBe(false);
    expect(reportErrorMessage(new Error("Zu groß"), "Fehler")).toBe("Zu groß");
    expect(reportErrorMessage("boom", "Fehler")).toBe("Fehler");
  });
});
