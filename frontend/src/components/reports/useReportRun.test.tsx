import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, type ReactNode } from "react";
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

function wrapper(strict = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => {
    const inner = <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    return strict ? <StrictMode>{inner}</StrictMode> : inner;
  };
}

beforeEach(async () => {
  // Let the deferred cancels of the previous test's unmounted hooks fire first.
  await new Promise((r) => setTimeout(r, 10));
  vi.mocked(reportsApi.run).mockReset();
  vi.mocked(jobsApi.get).mockReset();
  vi.mocked(jobsApi.cancel).mockReset();
  vi.mocked(jobsApi.cancel).mockResolvedValue(job("cancelled"));
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

  it("cancels the old job when the request changes", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    const { result, rerender } = renderHook(({ b }) => useReportRun(b), {
      wrapper: wrapper(),
      initialProps: { b: body },
    });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    vi.mocked(reportsApi.run).mockResolvedValue({ result: resultOf(1), job: null });
    rerender({ b: { report_key: "builtin.backlog_trend" } as ReportRunIn });
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalledWith(5));
    await waitFor(() => expect(result.current.result?.row_count).toBe(1));
    expect(result.current.jobId).toBeNull();
  });

  it("cancels a running job on unmount, but not a finished one", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    const first = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(first.result.current.jobId).toBe(5));
    await waitFor(() => expect(jobsApi.get).toHaveBeenCalled());
    first.unmount();
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalledWith(5));

    vi.mocked(jobsApi.cancel).mockClear();
    vi.mocked(jobsApi.get).mockResolvedValue(job("succeeded", { result: resultOf(2) as unknown as Job["result"] }));
    const second = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(second.result.current.result?.row_count).toBe(2));
    second.unmount();
    await new Promise((r) => setTimeout(r, 20));
    expect(jobsApi.cancel).not.toHaveBeenCalled();
  });

  it("ignores a cancel failure", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    vi.mocked(jobsApi.cancel).mockRejectedValue(new Error("409"));
    const { result, unmount } = renderHook(() => useReportRun(body), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    unmount();
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalled());
  });

  it("never lets a stale response overwrite a newer one", async () => {
    let releaseOld: (v: { result: ReportResult; job: null }) => void = () => {};
    vi.mocked(reportsApi.run).mockImplementation((b) =>
      b.report_key === "builtin.volume"
        ? new Promise((res) => {
            releaseOld = res as typeof releaseOld;
          })
        : Promise.resolve({ result: resultOf(2), job: null }),
    );
    const { result, rerender } = renderHook(({ b }) => useReportRun(b), {
      wrapper: wrapper(),
      initialProps: { b: body },
    });
    rerender({ b: { report_key: "builtin.backlog_trend" } as ReportRunIn });
    await waitFor(() => expect(result.current.result?.row_count).toBe(2));
    releaseOld({ result: resultOf(99), job: null });
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.result?.row_count).toBe(2);
  });

  it("reruns a request whose job was cancelled when the user comes back to it", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    const other = { report_key: "builtin.backlog_trend" } as ReportRunIn;
    const { result, rerender } = renderHook(({ b }) => useReportRun(b), {
      wrapper: wrapper(),
      initialProps: { b: body },
    });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    vi.mocked(reportsApi.run).mockResolvedValue({ result: resultOf(1), job: null });
    rerender({ b: other });
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalledWith(5));
    await waitFor(() => expect(result.current.result?.row_count).toBe(1));

    vi.mocked(jobsApi.get).mockResolvedValue(job("cancelled"));
    vi.mocked(reportsApi.run).mockClear();
    vi.mocked(reportsApi.run).mockResolvedValue({ result: resultOf(8), job: null });
    rerender({ b: body });
    await waitFor(() => expect(result.current.result?.row_count).toBe(8));
    expect(reportsApi.run).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it("does not cancel a job when the user switches away and back within one tick", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    const other = { report_key: "builtin.backlog_trend" } as ReportRunIn;
    const { result, rerender } = renderHook(({ b }) => useReportRun(b), {
      wrapper: wrapper(),
      initialProps: { b: body },
    });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    rerender({ b: other });
    rerender({ b: body });
    await new Promise((r) => setTimeout(r, 30));
    expect(jobsApi.cancel).not.toHaveBeenCalled();
    expect(result.current.jobId).toBe(5);
  });

  it("does not cancel a live job under StrictMode's remount", async () => {
    vi.mocked(reportsApi.run).mockResolvedValue({ result: null, job: { job_id: 5, status: "queued" } });
    vi.mocked(jobsApi.get).mockResolvedValue(job("running"));
    const { result, unmount } = renderHook(() => useReportRun(body), { wrapper: wrapper(true) });
    await waitFor(() => expect(result.current.jobId).toBe(5));
    await new Promise((r) => setTimeout(r, 30));
    expect(jobsApi.cancel).not.toHaveBeenCalled();
    unmount();
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalledWith(5));
  });
});
