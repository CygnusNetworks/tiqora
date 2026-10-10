import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { ShareFields, type ShareValue } from "./ShareFields";
import type { BuilderIssue } from "./builderState";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

export type SaveError = { message: string; conflict: boolean };

/** Name, description and sharing of a report about to be saved. The page validates and sends. */
export function SaveReportDialog({
  open,
  onClose,
  mode,
  name,
  description,
  onMeta,
  share,
  onShare,
  canShare,
  pending,
  error,
  issues,
  onSubmit,
  onReload,
}: {
  open: boolean;
  onClose: () => void;
  /** `copy`: save as a new report although an existing one is being edited. */
  mode: "create" | "update" | "copy";
  name: string;
  description: string;
  onMeta: (patch: { name?: string; description?: string }) => void;
  share: ShareValue;
  onShare: (next: ShareValue) => void;
  canShare: boolean;
  pending: boolean;
  error: SaveError | null;
  /** Problems of the name / sharing choice, shown after a first try. */
  issues: BuilderIssue[];
  onSubmit: () => void;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const nameIssue = issues.find((i) => i.key === "nameRequired");
  const groupsIssue = issues.find((i) => i.key === "groupsRequired");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={mode === "update" ? t("reports.builder.save") : mode === "copy" ? t("reports.builder.saveCopy") : t("reports.builder.saveTitle")}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} data-testid="save-report-dialog-cancel">
            {t("reports.builder.cancel")}
          </Button>
          <Button variant="primary" onClick={onSubmit} disabled={pending} data-testid="save-report-dialog-confirm">
            {pending ? t("reports.builder.saving") : t("reports.builder.save")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        data-testid="save-report-dialog"
        onSubmit={(e) => {
          e.preventDefault();
          if (!pending) onSubmit();
        }}
      >
        <div className="space-y-1">
          <label htmlFor={`${id}-name`} className="block text-xs font-medium text-muted">
            {t("reports.builder.name")}
          </label>
          <input
            id={`${id}-name`}
            className={inputCls}
            value={name}
            maxLength={200}
            onChange={(e) => onMeta({ name: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !pending) {
                e.preventDefault();
                onSubmit();
              }
            }}
            aria-invalid={nameIssue ? true : undefined}
            aria-describedby={nameIssue ? `${id}-name-error` : undefined}
            data-testid="save-report-dialog-name"
          />
          {nameIssue && (
            <p id={`${id}-name-error`} className="text-xs text-danger">
              {t(`reports.builder.issues.${nameIssue.key}`)}
            </p>
          )}
        </div>
        <div className="space-y-1">
          <label htmlFor={`${id}-description`} className="block text-xs font-medium text-muted">
            {t("reports.builder.description")}
          </label>
          <textarea
            id={`${id}-description`}
            className={inputCls}
            rows={3}
            maxLength={2000}
            value={description}
            onChange={(e) => onMeta({ description: e.target.value })}
            data-testid="save-report-dialog-description"
          />
        </div>
        <ShareFields value={share} onChange={onShare} canShare={canShare} testId="share-fields" />
        {groupsIssue && <p className="text-xs text-danger">{t(`reports.builder.issues.${groupsIssue.key}`)}</p>}
        {error && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2">
            <p className="text-sm text-ink" role="alert" data-testid="save-report-dialog-error">
              {error.message}
            </p>
            {error.conflict && (
              <Button size="sm" variant="secondary" onClick={onReload} data-testid="save-report-dialog-reload">
                {t("reports.builder.reload")}
              </Button>
            )}
          </div>
        )}
      </form>
    </Dialog>
  );
}
