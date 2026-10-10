import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ReportView } from "@/components/reports/ReportView";
import { RunState } from "@/components/reports/RunState";
import { useReportRun } from "@/components/reports/useReportRun";
import type { ReportDefinition } from "@/lib/reportsApi";
import type { BuilderIssue } from "./builderState";
import { previewBody } from "./previewBody";
import { useDebouncedPreview } from "./useDebounced";

/**
 * Centre column: the report as it is built so far. `definition` is `null` while the report is
 * incomplete (then `issues` say what is missing); otherwise the preview follows it after a short pause.
 */
export function BuilderPreview({ definition, issues }: { definition: ReportDefinition | null; issues: BuilderIssue[] }) {
  const { t } = useTranslation();
  const shown = useDebouncedPreview(definition);
  const body = useMemo(() => (shown ? previewBody(shown) : null), [shown]);
  const run = useReportRun(body);
  const result = run.result;

  return (
    <section className="space-y-3 lg:sticky lg:top-4 lg:self-start" aria-labelledby="builder-preview-title" data-testid="builder-preview">
      <p className="sr-only" role="status">
        {!shown ? t("reports.builder.previewInvalid") : result ? "" : t("reports.loading")}
      </p>
      <h2 id="builder-preview-title" className="text-xs font-semibold uppercase tracking-wide text-muted">
        {t("reports.builder.preview")}
      </h2>
      <div className="min-h-[16rem] rounded-lg border border-hairline bg-surface p-4">
        {!shown ? (
          <div data-testid="builder-preview-issues">
            <p className="text-sm text-muted">{t("reports.builder.previewInvalid")}</p>
            {issues.length > 0 && (
              <>
                <p className="mt-3 text-xs font-medium text-ink">{t("reports.builder.previewIssues")}</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink">
                  {issues.map((i) => (
                    <li key={`${i.path}-${i.key}`}>{t(`reports.builder.issues.${i.key}`)}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        ) : run.error || !result ? (
          <RunState run={run} testId="builder-preview-run" />
        ) : result.row_count === 0 ? (
          <p className="py-10 text-center text-sm text-muted" data-testid="builder-preview-empty">
            {t("reports.builder.previewEmpty")}
          </p>
        ) : (
          <div className="space-y-2" data-testid="builder-preview-view">
            <ReportView report={{ key: null, definition: shown }} result={result} testId="builder-report-view" />
            {result.truncated && <p className="text-xs text-muted">{t("reports.builder.previewTooLarge")}</p>}
          </div>
        )}
      </div>
    </section>
  );
}
