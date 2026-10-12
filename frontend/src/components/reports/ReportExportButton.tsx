import { useState } from "react";
import { useTranslation } from "react-i18next";
import { JobDialog } from "@/components/jobs/JobDialog";
import { Button } from "@/components/ui/Button";
import {
  reportErrorMessage,
  reportsApi,
  type ReportExportIn,
  type ReportResult,
  type ReportRunIn,
} from "@/lib/reportsApi";
import { exportLabels } from "./reportFormat";

/**
 * "Als Excel-Datei" / "Als CSV-Datei": starts the export in the background
 * with the same request as the shown report plus the translated headers and
 * values (the server writes no UI texts), then follows it in the job dialog.
 */
export function ReportExportButton({
  runIn,
  result,
  title,
  infoLines,
  testId = "report-export",
}: {
  runIn: ReportRunIn;
  result: ReportResult | undefined;
  title: string;
  infoLines: string[];
  testId?: string;
}) {
  const { t, i18n } = useTranslation();
  const [jobId, setJobId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async (format: ReportExportIn["format"]) => {
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      const accepted = await reportsApi.export({
        ...runIn,
        format,
        title: title.slice(0, 120),
        info_lines: infoLines.map((l) => l.slice(0, 300)).slice(0, 20),
        // the PDF is written by the server in the language of the UI
        locale: i18n.language.slice(0, 10),
        ...exportLabels(result, t),
      });
      setJobId(accepted.job_id);
    } catch (err) {
      setError(reportErrorMessage(err, t("reports.exportFailed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("reports.export")}>
        <Button
          size="sm"
          variant="secondary"
          disabled={!result || busy}
          onClick={() => void start("xlsx")}
          data-testid={`${testId}-xlsx`}
        >
          {t("reports.exportXlsx")}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!result || busy}
          onClick={() => void start("csv")}
          data-testid={`${testId}-csv`}
        >
          {t("reports.exportCsv")}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!result || busy}
          onClick={() => void start("pdf")}
          data-testid={`${testId}-pdf`}
        >
          {t("reports.exportPdf")}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-xs text-danger" data-testid={`${testId}-error`}>
          {error}
        </p>
      )}
      <JobDialog jobId={jobId} title={t("jobs.kinds.core.report_export")} onClose={() => setJobId(null)} />
    </div>
  );
}
