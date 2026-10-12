import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { reportsApi, type ReportResult, type ReportRunIn } from "@/lib/reportsApi";
import { ReportExportButton } from "./ReportExportButton";

vi.mock("@/components/jobs/JobDialog", () => ({
  JobDialog: ({ jobId }: { jobId: number | null }) => (jobId == null ? null : <div data-testid="job-dialog-stub">{jobId}</div>),
}));

const RUN_IN = { report_key: "builtin.volume", time_zone: "Europe/Berlin" } as unknown as ReportRunIn;
const RESULT = {
  report_key: "builtin.volume",
  columns: [{ key: "events", role: "measure", type: "count", label_key: "reports.measures.events", grain: null, i18n_values: null }],
  rows: [[3]],
  labels: {},
  totals: null,
  previous: null,
  row_count: 1,
  truncated: false,
  time_window: null,
  generated_at: "2026-10-01T08:00:00Z",
  cached: false,
  facts: { ready: true, as_of: null, backfill_status: "done", backfill_job_id: null, backfill_progress: 1 },
} as unknown as ReportResult;

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
afterEach(() => vi.restoreAllMocks());

describe("ReportExportButton", () => {
  it("starts a PDF export with the UI language and follows the job", async () => {
    const exportSpy = vi.spyOn(reportsApi, "export").mockResolvedValue({ job_id: 4, status: "queued" } as never);
    render(
      <I18nextProvider i18n={i18n}>
        <ReportExportButton runIn={RUN_IN} result={RESULT} title="rep52 Bericht" infoLines={[]} />
      </I18nextProvider>,
    );
    const btn = screen.getByTestId("report-export-pdf");
    expect(btn).toHaveTextContent("Als PDF-Datei");
    fireEvent.click(btn);
    await waitFor(() => expect(exportSpy).toHaveBeenCalledTimes(1));
    expect(exportSpy).toHaveBeenCalledWith(expect.objectContaining({ format: "pdf", locale: "de", title: "rep52 Bericht" }));
    expect(await screen.findByTestId("job-dialog-stub")).toHaveTextContent("4");
  });

  it("keeps the PDF button disabled without a result", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <ReportExportButton runIn={RUN_IN} result={undefined} title="x" infoLines={[]} />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("report-export-pdf")).toBeDisabled();
  });
});
