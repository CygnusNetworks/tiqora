import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { apiErrorMessage } from "@/lib/avatarApi";
import type { AttributeDef } from "@/lib/cmdbApi";
import { cmdbAdminApi, type DefinitionImportIn, type DefinitionImportOut } from "@/lib/cmdbAdminApi";

const FORMATS: DefinitionImportIn["format"][] = ["znuny_perl", "znuny_yaml", "tiqora_json"];
const FORMAT_LABEL: Record<DefinitionImportIn["format"], string> = {
  znuny_perl: "admin.cmdb.editor.import.formatPerl",
  znuny_yaml: "admin.cmdb.editor.import.formatYaml",
  tiqora_json: "admin.cmdb.editor.import.formatJson",
};

/** Largest file the dialog reads into the text field (a definition is a few KiB). */
const MAX_FILE_BYTES = 1024 * 1024;

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function countAttrs(defs: readonly AttributeDef[]): number {
  return defs.reduce((n, d) => n + 1 + countAttrs(d.sub ?? []), 0);
}

/**
 * Paste or upload a class definition (Znuny 6 Perl, Znuny 7 YAML, Tiqora JSON), preview what the server
 * made of it (warnings, missing catalog classes) and hand the attributes to the editor. Nothing is saved
 * here: the admin saves the result as a new version from the editor.
 */
export function DefinitionImportDialog({
  open,
  onClose,
  onApply,
}: {
  open: boolean;
  onClose: () => void;
  onApply: (attributes: AttributeDef[]) => void;
}) {
  const { t } = useTranslation();
  const [format, setFormat] = useState<DefinitionImportIn["format"]>("znuny_perl");
  const [text, setText] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DefinitionImportOut | null>(null);

  const previewM = useMutation({
    mutationFn: () => cmdbAdminApi.importDefinition({ format, text }),
    onSuccess: setPreview,
    onError: () => setPreview(null),
  });

  const onFile = async (file: File | undefined) => {
    setFileError(null);
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setFileError(t("admin.cmdb.editor.import.fileTooBig"));
      return;
    }
    try {
      setText(await readText(file));
      setPreview(null);
    } catch {
      setFileError(t("admin.cmdb.editor.import.fileError"));
    }
  };

  const canPreview = text.trim() !== "" && !previewM.isPending;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.cmdb.editor.import.title")}
      description={t("admin.cmdb.editor.import.help")}
      size="xl"
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button disabled={!canPreview} onClick={() => previewM.mutate()} data-testid="def-import-preview">
            {t("admin.cmdb.editor.import.preview")}
          </Button>
          <Button variant="primary" disabled={!preview} onClick={() => preview && onApply(preview.attributes as AttributeDef[])} data-testid="def-import-apply">
            {t("admin.cmdb.editor.import.apply")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted" htmlFor="def-import-format">
            {t("admin.cmdb.editor.import.format")}
          </label>
          <SelectField<DefinitionImportIn["format"]>
            id="def-import-format"
            testId="def-import-format"
            items={FORMATS.map((f) => ({ value: f, label: t(FORMAT_LABEL[f]) }))}
            value={format}
            onChange={(f) => {
              setFormat(f);
              setPreview(null);
            }}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted" htmlFor="def-import-text">
            {t("admin.cmdb.editor.import.text")}
          </label>
          <textarea
            id="def-import-text"
            data-testid="def-import-text"
            rows={10}
            spellCheck={false}
            className="w-full rounded border border-hairline bg-surface px-2 py-1.5 font-mono text-xs text-ink focus:outline-none focus:ring-1 focus:ring-accent"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setPreview(null);
            }}
          />
          <input type="file" data-testid="def-import-file" aria-label={t("admin.cmdb.editor.import.file")} className="mt-1 text-xs text-muted" onChange={(e) => void onFile(e.target.files?.[0])} />
          {fileError && <p role="alert" className="mt-1 text-xs text-danger">{fileError}</p>}
        </div>

        {previewM.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="def-import-error">
            {apiErrorMessage(previewM.error, t("admin.cmdb.editor.import.error"))}
          </p>
        )}

        {preview && (
          <div className="space-y-2 rounded border border-hairline bg-surface-subtle p-3 text-sm" data-testid="def-import-result">
            <p className="text-ink" data-testid="def-import-count">{t("admin.cmdb.editor.import.found", { count: countAttrs(preview.attributes as AttributeDef[]) })}</p>
            {preview.warnings.length > 0 && (
              <div data-testid="def-import-warnings">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">{t("admin.cmdb.editor.import.warnings")}</p>
                <ul className="list-disc pl-5 text-ink">
                  {preview.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            {preview.missing_catalog_classes.length > 0 && (
              <div data-testid="def-import-missing">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">{t("admin.cmdb.editor.import.missingCatalog")}</p>
                <ul className="list-disc pl-5 font-mono text-xs text-ink">
                  {preview.missing_catalog_classes.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-muted">{t("admin.cmdb.editor.import.missingHelp")}</p>
              </div>
            )}
            <p className="text-xs text-muted">{t("admin.cmdb.editor.import.notSaved")}</p>
          </div>
        )}
      </div>
    </Dialog>
  );
}
