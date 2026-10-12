import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";

/**
 * Shared frame of the designer side panels: header, optional save/discard row (for panels that save
 * on their own) and the "changed in the meantime" message when the stored version moved on.
 */
export function PanelFrame({
  title,
  children,
  dirty = false,
  saving = false,
  conflict = false,
  error = false,
  onSave,
  onDiscard,
  onReload,
  testId = "process-designer-panel-frame",
}: {
  title: string;
  children: ReactNode;
  dirty?: boolean;
  saving?: boolean;
  conflict?: boolean;
  error?: boolean;
  /** Present for panels that save themselves; the process panel is saved with the canvas. */
  onSave?: () => void;
  onDiscard?: () => void;
  onReload?: () => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <section className="space-y-3" data-testid={testId}>
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {conflict ? (
        <div
          role="alert"
          data-testid={`${testId}-conflict`}
          className="space-y-2 rounded-lg border border-danger bg-surface px-3 py-2 text-sm text-danger"
        >
          <p>{t("admin.processDesigner.conflict")}</p>
          {onReload ? (
            <Button size="sm" onClick={onReload}>
              {t("admin.processDesigner.reload")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {t("admin.processDesigner.saveError")}
        </p>
      ) : null}
      {children}
      {onSave ? (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={onSave} data-testid={`${testId}-save`}>
            {saving ? t("admin.processDesigner.saving") : t("admin.processDesigner.save")}
          </Button>
          {onDiscard ? (
            <Button size="sm" disabled={!dirty || saving} onClick={onDiscard} data-testid={`${testId}-discard`}>
              {t("admin.processDesigner.discard")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
