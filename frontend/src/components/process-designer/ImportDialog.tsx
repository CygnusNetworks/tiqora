import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { processDesignerApi, type ProcessImportOut } from "@/lib/processDesignerApi";

function readText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

/**
 * Import of a Znuny process file (`.yml`/`.yaml`). The file is read in the browser and posted as
 * `content`; the result is always a draft (nothing runs until someone deploys).
 */
export function ImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: (out: ProcessImportOut) => void;
}) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    if (busy) return;
    setFile(null);
    setOverwrite(false);
    setError(null);
    onClose();
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    let content: string;
    try {
      content = await readText(file);
    } catch {
      setBusy(false);
      setError(t("admin.processDesigner.import.readError"));
      return;
    }
    try {
      const out = await processDesignerApi.importFile(content, overwrite);
      setFile(null);
      setOverwrite(false);
      onImported(out);
    } catch (err) {
      setError(
        t("admin.processDesigner.import.refused", {
          message: apiErrorMessage(err, t("admin.processDesigner.import.unknown")),
        }),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title={t("admin.processDesigner.import.title")}
      description={t("admin.processDesigner.import.description")}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={busy} data-testid="import-cancel">
            {t("common.cancel")}
          </Button>
          <Button size="sm" onClick={() => void submit()} disabled={!file || busy} data-testid="import-submit">
            {busy ? <Spinner /> : t("admin.processDesigner.import.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-3" data-testid="import-dialog">
        <label className="block text-sm text-ink">
          {t("admin.processDesigner.import.file")}
          <input
            type="file"
            accept=".yml,.yaml"
            data-testid="import-file"
            className="mt-1 block w-full text-sm text-ink"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
        </label>
        <label className="flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
            data-testid="import-overwrite"
            className="mt-0.5"
          />
          <span>{t("admin.processDesigner.import.overwrite")}</span>
        </label>
        {error ? (
          <p role="alert" className="text-sm text-danger" data-testid="import-error">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
