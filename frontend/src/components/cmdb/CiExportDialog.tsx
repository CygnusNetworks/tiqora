import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { apiErrorMessage } from "@/lib/avatarApi";
import { cmdbApi, type CmdbExportIn } from "@/lib/cmdbApi";

/** Export of the current filter (one class): CSV or XLSX, written by a background job. */
export function CiExportDialog({
  open,
  request,
  onClose,
  onStarted,
}: {
  open: boolean;
  /** The current class and filter; the format is chosen here. */
  request: Omit<CmdbExportIn, "file_format">;
  onClose: () => void;
  onStarted: (jobId: number) => void;
}) {
  const { t } = useTranslation();
  const [format, setFormat] = useState<"csv" | "xlsx">("csv");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const accepted = await cmdbApi.startExport({ ...request, file_format: format });
      onStarted(accepted.job_id);
    } catch (err) {
      setError(apiErrorMessage(err, t("cmdb.list.export.failed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("cmdb.list.export.title")}
      description={t("cmdb.list.export.description")}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={() => void start()} disabled={busy} data-testid="export-start">
            {t("cmdb.list.export.start")}
          </Button>
        </>
      }
    >
      <div className="space-y-3" data-testid="export-dialog">
        <SegmentedControl<"csv" | "xlsx">
          testId="export-format"
          aria-label={t("cmdb.list.export.format")}
          value={format}
          items={[
            { value: "csv", label: "CSV" },
            { value: "xlsx", label: "XLSX" },
          ]}
          onChange={setFormat}
        />
        {error && (
          <p role="alert" className="text-sm text-danger" data-testid="export-error">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
