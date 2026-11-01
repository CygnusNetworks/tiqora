import { useTranslation } from "react-i18next";
import { useChangeStatus } from "@/components/itsm/change/changeUi";

const CHANGE_SOURCES = new Set(["changes", "change_events"]);

/** A report on a data basis Tiqora keeps only in Mode B: why the figures can be empty or old. */
export function SourceUnavailableHint({ testId, source }: { testId: string; source?: string | null }) {
  const { t } = useTranslation();
  const reason = useChangeStatus().data?.reason;
  const switchedOff = !!source && CHANGE_SOURCES.has(source) && (reason === "change_off" || reason === "approvals_off");
  return (
    <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-ink" role="status" data-testid={testId}>
      {t(switchedOff ? "reports.builder.sourceSwitchedOff" : "reports.builder.sourceUnavailable")}
    </p>
  );
}
