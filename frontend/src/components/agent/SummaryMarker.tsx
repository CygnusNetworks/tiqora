import { useTranslation } from "react-i18next";
import { formatDateTime } from "@/lib/format";

/**
 * "Summarized up to here" marker for the article lists (AI-panel design
 * "Variante 1 + Timeline-Marker"): a dashed accent rule rendered between the
 * articles the current AI summary covers and those that arrived later.
 * Position is computed by `useSummaryBoundary`.
 */
export function SummaryMarker({
  createdAt,
  locale,
}: {
  createdAt: string | null;
  locale: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="flex items-center gap-2 px-1 py-0.5 text-[11px] font-medium text-accent"
      data-testid="summary-marker"
      role="separator"
    >
      <span aria-hidden className="flex-1 border-t border-dashed border-accent/50" />
      <span className="shrink-0">
        ✦ {t("ticket.ai.summaryMarker")}
        {createdAt && (
          <span className="font-normal text-muted"> · {formatDateTime(createdAt, locale)}</span>
        )}
      </span>
      <span aria-hidden className="flex-1 border-t border-dashed border-accent/50" />
    </div>
  );
}
