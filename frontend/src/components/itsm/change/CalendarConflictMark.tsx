import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

const TONE = {
  block: "border-danger text-danger",
  warn: "border-escalation text-escalation",
  info: "border-hairline text-muted",
} as const;

/** "!" marker of a change (or service day) that conflicts with a freeze window or another change. */
export function CalendarConflictMark({
  severity,
  className,
  testId,
}: {
  severity: "info" | "warn" | "block" | null | undefined;
  className?: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  if (!severity) return null;
  return (
    <span
      role="img"
      aria-label={t(`itsm.change.conflicts.severity.${severity}`)}
      title={t(`itsm.change.conflicts.severity.${severity}`)}
      data-testid={testId ?? "calendar-conflict-mark"}
      data-severity={severity}
      className={cn("mr-1 inline-block rounded-full border px-1 text-[10px] font-bold leading-4", TONE[severity], className)}
    >
      !
    </span>
  );
}
