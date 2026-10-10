import { useTranslation } from "react-i18next";

const WIDTHS = ["92%", "74%", "84%", "62%"];

/** Loading placeholder filling the widget body; `rows` follows the widget's height. */
export function WidgetSkeleton({ rows }: { rows: number }) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="widget-skeleton"
      role="status"
      aria-busy="true"
      aria-label={t("dashboards.state.loading")}
      className="flex h-full flex-col gap-3 pt-1"
    >
      {Array.from({ length: Math.max(1, rows) }, (_, i) => (
        <div
          key={i}
          data-skeleton-row
          className="h-3.5 shrink-0 rounded bg-surface-subtle motion-safe:animate-pulse"
          style={{ width: WIDTHS[i % WIDTHS.length] }}
        />
      ))}
    </div>
  );
}
