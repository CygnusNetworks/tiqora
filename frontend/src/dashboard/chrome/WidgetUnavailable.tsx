import { useTranslation } from "react-i18next";
import type { WidgetMode } from "@/plugins/dashboardWidgets";
import { useWidgetChrome } from "./WidgetFrame";

/**
 * Tile for a stored widget whose type is not registered (plugin removed, key renamed, newer
 * version). The row stays stored; only edit mode offers "Entfernen".
 */
export function WidgetUnavailable({
  typeKey,
  mode,
  onRemove,
}: {
  typeKey: string;
  mode: WidgetMode;
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  const { handle } = useWidgetChrome();
  return (
    <article
      data-testid="widget-unavailable"
      className="flex h-full min-h-0 min-w-0 flex-col gap-2 rounded-[14px] border border-dashed border-hairline bg-surface px-[18px] py-4"
    >
      <header className="flex min-w-0 items-center gap-2">
        {mode === "edit" && handle}
        <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink">{t("dashboards.state.unavailable")}</h2>
      </header>
      <p className="text-sm text-muted">{t("dashboards.state.unavailableHint")}</p>
      <code className="self-start truncate rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-xs text-muted">
        {typeKey}
      </code>
      {mode === "edit" && onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="mt-auto self-start rounded-md border border-danger/40 px-3 py-1.5 text-xs font-medium text-danger transition-colors hover:bg-danger/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-danger"
        >
          {t("dashboards.state.remove")}
        </button>
      )}
    </article>
  );
}
