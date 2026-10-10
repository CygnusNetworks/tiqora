import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

/**
 * The settings seam of edit mode: opened by a widget's "Einstellungen", by Enter on a focused
 * widget and by a 422 that names a widget. Task 14 replaces this body with `SettingsDrawer`
 * (generated forms, editors, live preview); until then it names the widget and shows the
 * server's message for it.
 */
export function WidgetSettingsDialog({
  title,
  message,
  onClose,
}: {
  /** The widget's shown title. */
  title: string;
  /** Server message for this widget (422), if any. */
  message: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open
      placement="right"
      title={t("dashboards.edit.settings")}
      description={title}
      onClose={onClose}
      footer={
        <Button variant="secondary" size="sm" onClick={onClose} data-autofocus>
          {t("dashboards.edit.close")}
        </Button>
      }
    >
      {message && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {message}
        </p>
      )}
    </Dialog>
  );
}
