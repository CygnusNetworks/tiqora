import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

/**
 * Shown when "Speichern" answered 409: someone saved this dashboard meanwhile. "Neu laden" leaves
 * edit mode and loads the new version (the session stays as a draft); "Weiter bearbeiten" (also
 * Escape) keeps editing.
 */
export function ConflictDialog({
  open,
  onReload,
  onContinue,
}: {
  open: boolean;
  onReload: () => void;
  onContinue: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      role="alertdialog"
      title={t("dashboards.edit.conflictTitle")}
      message={t("dashboards.edit.conflictMessage")}
      confirmLabel={t("dashboards.edit.conflictReload")}
      cancelLabel={t("dashboards.edit.continue")}
      onConfirm={onReload}
      onCancel={onContinue}
    />
  );
}
