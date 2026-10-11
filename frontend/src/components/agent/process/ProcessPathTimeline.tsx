import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import type { ProcessPath } from "@/lib/processApi";

/** The steps a ticket has taken through its process, oldest first; the current one is marked. */
export function ProcessPathTimeline({ path }: { path: ProcessPath }) {
  const { t, i18n } = useTranslation();
  if (path.steps.length === 0) {
    return (
      <p className="text-sm text-muted" data-testid="process-path-empty">
        {t("process.path.empty")}
      </p>
    );
  }
  return (
    <ol className="space-y-2" data-testid="process-path-list">
      {path.steps.map((step, i) => (
        <li
          key={`${i}-${step.activity_entity_id}`}
          className="flex flex-wrap items-baseline gap-x-2 text-sm"
          data-testid="process-path-step"
          aria-current={step.current ? "step" : undefined}
        >
          <span className={step.current ? "font-medium text-ink" : "text-ink"}>
            {step.activity_name || t("process.path.deleted")}
          </span>
          {step.current && <Badge tone="accent">{t("process.path.current")}</Badge>}
          <span className="text-xs text-muted">
            {formatDateTime(step.entered_at, i18n.language)}
            {step.entered_by_name
              ? ` · ${t("process.path.by", { name: step.entered_by_name })}`
              : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
