import { useTranslation } from "react-i18next";
import { escalationLevel, humanDuration } from "@/lib/status";
import { cn } from "@/lib/cn";
import { formatDurationSince, nearestEscalation, type EscalationEpochs } from "../legacy";

/** "Fällig" of a ticket: since a breach (danger), the time left before a deadline (escalation
 * colour inside the app's warning window, `escalationLevel`), or "—". */
export function DueText({ ticket, testId }: { ticket: EscalationEpochs; testId?: string }) {
  const { t } = useTranslation();
  const epoch = nearestEscalation(ticket);
  if (epoch == null)
    return (
      <span data-testid={testId} className="text-muted">
        —
      </span>
    );
  const now = Date.now() / 1000;
  if (epoch <= now)
    return (
      <span data-testid={testId} className="text-danger">
        {t("dashboards.due.since", { duration: formatDurationSince(epoch) })}
      </span>
    );
  return (
    <span
      data-testid={testId}
      className={cn(escalationLevel(epoch) === "approaching" ? "text-escalation" : "text-muted")}
    >
      {t("dashboards.due.in", { duration: humanDuration(t, epoch - now) })}
    </span>
  );
}
