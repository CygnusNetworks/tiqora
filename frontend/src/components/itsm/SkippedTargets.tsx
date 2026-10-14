import { useTranslation } from "react-i18next";
import type { SkippedEntry } from "./skipped";

/**
 * Which tickets a bulk job refused and why. A plugin's veto text (German, written for agents)
 * is shown behind the reason `vetoed`.
 */
export function SkippedTargets({
  skipped,
  names,
  testId,
}: {
  skipped: SkippedEntry[];
  names?: Record<number, string>;
  testId: string;
}) {
  const { t } = useTranslation();
  if (skipped.length === 0) return null;
  return (
    <div>
      <p className="font-medium text-ink">{t("itsm.linkedNote.skipped", { count: skipped.length })}</p>
      <ul className="text-xs text-muted" data-testid={testId}>
        {skipped.map((s) => (
          <li key={s.ticket_id}>
            {names?.[s.ticket_id] ?? `#${s.ticket_id}`}:{" "}
            {t(`itsm.linkedNote.reasons.${s.reason}`, { defaultValue: t("itsm.linkedNote.reasons.error") })}
            {s.reason === "vetoed" && s.detail ? ` (${s.detail})` : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
