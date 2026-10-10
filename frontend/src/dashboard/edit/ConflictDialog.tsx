import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

/**
 * Shown when "Speichern" answered 409: the dashboard was saved meanwhile (another window or person).
 * "Neu laden" leaves edit mode and loads the new version; the session stays as a draft marked as
 * replacing that version if restored and saved. "Weiter bearbeiten" (also Escape) keeps editing.
 */
export function ConflictDialog({
  open,
  template = false,
  onReload,
  onContinue,
}: {
  open: boolean;
  /** A role template: the text says that restoring replaces other people's changes for all members. */
  template?: boolean;
  onReload: () => void;
  onContinue: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      role="alertdialog"
      title={t("dashboards.edit.conflictTitle")}
      message={t(template ? "dashboards.edit.conflictMessageTemplate" : "dashboards.edit.conflictMessage")}
      confirmLabel={t("dashboards.edit.conflictReload")}
      cancelLabel={t("dashboards.edit.continue")}
      onConfirm={onReload}
      onCancel={onContinue}
    />
  );
}
