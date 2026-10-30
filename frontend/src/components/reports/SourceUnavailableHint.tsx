import { useTranslation } from "react-i18next";

/** A report on a data basis Tiqora keeps only in Mode B: why the figures can be empty or old. */
export function SourceUnavailableHint({ testId }: { testId: string }) {
  const { t } = useTranslation();
  return (
    <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-ink" role="status" data-testid={testId}>
      {t("reports.builder.sourceUnavailable")}
    </p>
  );
}
