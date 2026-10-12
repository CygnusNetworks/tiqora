import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { createDemoSchedules } from "@/demo/schedulesMock";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { reportSchedulesApi, type ReportRun } from "@/lib/reportSchedulesApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { ScheduleRunsTable } from "./ScheduleRunsTable";
import { RUN_POLL_MS } from "./scheduleUi";

function renderTable(canRun = true, isOwner = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ScheduleRunsTable scheduleId={1} canRun={canRun} isOwner={isOwner} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const RUNS = createDemoSchedules().runs(1);

beforeAll(async () => {
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function run(over: Partial<ReportRun>): ReportRun {
  return { ...RUNS[0], ...over };
}

describe("ScheduleRunsTable", () => {
  it("shows status chips, details and file links", async () => {
    const withFile = run({
      id: 40,
      status: "succeeded",
      files: [{ id: 8, name: "bericht.pdf", content_type: "application/pdf", size_bytes: 2048 }],
      row_count: 12,
      recipient_count: 3,
      error: null,
      detail: null,
    });
    const failed = run({ id: 41, status: "failed", error: "Der Versand ist fehlgeschlagen.", files: [] });
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([failed, withFile]);
    renderTable();
    expect(await screen.findByTestId("schedule-run-40-status")).toHaveTextContent("Versendet");
    expect(screen.getByTestId("schedule-run-41-status")).toHaveTextContent("Fehlgeschlagen");
    const row = screen.getByTestId("schedule-run-40");
    expect(row).toHaveTextContent("12 Zeilen");
    expect(row).toHaveTextContent("3 Empfänger");
    expect(within(row).getByTestId("schedule-run-file-8")).toHaveAttribute("href", reportSchedulesApi.fileUrl(40, 8));
    expect(screen.getByTestId("schedule-run-41")).toHaveTextContent("Der Versand ist fehlgeschlagen.");
  });

  it("shows an empty state without runs", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
    renderTable();
    expect(await screen.findByTestId("schedule-runs-empty")).toHaveTextContent("Es gab noch keinen Versand.");
  });

  it("polls every 5 s while a run is queued or running, and stops afterwards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const runs = vi
      .spyOn(reportSchedulesApi, "runs")
      .mockResolvedValueOnce([run({ id: 50, status: "running", files: [] })])
      .mockResolvedValue([run({ id: 50, status: "succeeded", files: [] })]);
    renderTable();
    expect(await screen.findByTestId("schedule-run-50-status")).toHaveTextContent("Läuft");
    expect(runs).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_MS + 100);
    });
    await waitFor(() => expect(screen.getByTestId("schedule-run-50-status")).toHaveTextContent("Versendet"));
    expect(runs).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_MS * 3);
    });
    expect(runs).toHaveBeenCalledTimes(2); // nothing active any more: no further polling
  });

  it("sends a test to me or to everybody from the menu", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
    const runNow = vi.spyOn(reportSchedulesApi, "runNow").mockResolvedValue({ run_id: 5, job: { job_id: 9, status: "queued" } } as never);
    renderTable();
    fireEvent.click(await screen.findByTestId("schedule-run-now"));
    fireEvent.click(await screen.findByTestId("schedule-run-test"));
    await waitFor(() => expect(runNow).toHaveBeenLastCalledWith(1, true));
    expect(await screen.findByTestId("schedule-run-notice")).toHaveTextContent("Der Versand wurde gestartet.");
    fireEvent.click(screen.getByTestId("schedule-run-now"));
    fireEvent.click(await screen.findByTestId("schedule-run-all"));
    await waitFor(() => expect(runNow).toHaveBeenLastCalledWith(1, false));
  });

  it("names the owner as test recipient when an admin opens someone else's schedule", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
    renderTable(true, false);
    fireEvent.click(await screen.findByTestId("schedule-run-now"));
    expect(await screen.findByTestId("schedule-run-test")).toHaveTextContent("Testversand an den Ersteller");
  });

  it("says 1 Zeile for a single row", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([run({ id: 42, status: "succeeded", row_count: 1, recipient_count: 1, files: [] })]);
    renderTable();
    const row = await screen.findByTestId("schedule-run-42");
    expect(row).toHaveTextContent("1 Zeile");
    expect(row).not.toHaveTextContent("1 Zeilen");
  });

  it("explains a 409: a delivery is already running", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
    vi.spyOn(reportSchedulesApi, "runNow").mockRejectedValue(
      new ApiError(409, { detail: { message: "irrelevant" } }, "/api/v1/report-schedules/1/run"),
    );
    renderTable();
    fireEvent.click(await screen.findByTestId("schedule-run-now"));
    fireEvent.click(await screen.findByTestId("schedule-run-all"));
    expect(await screen.findByTestId("schedule-run-notice")).toHaveTextContent("Ein Versand läuft bereits.");
  });

  it("offers no send menu to somebody who cannot edit", async () => {
    vi.spyOn(reportSchedulesApi, "runs").mockResolvedValue([]);
    renderTable(false);
    await screen.findByTestId("schedule-runs-empty");
    expect(screen.queryByTestId("schedule-run-now")).toBeNull();
  });
});
