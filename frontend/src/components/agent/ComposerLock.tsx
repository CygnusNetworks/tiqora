import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";

/** Warning strip shown while the ticket is locked by another agent — the
 * counterpart to `useComposerLock` (`@/lib/composerLock`). */
export function ComposerLockBanner({
  lockedBy,
  onTakeOver,
  busy,
}: {
  lockedBy: string | null;
  onTakeOver: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  if (!lockedBy) return null;
  return (
    <div
      className="flex items-center justify-between gap-2 rounded border border-escalation/30 bg-escalation/15 px-2 py-1.5 text-xs text-escalation"
      data-testid="composer-lock-banner"
    >
      <span>{t("ticket.lockedByBanner", { name: lockedBy })}</span>
      <Button
        size="sm"
        variant="secondary"
        data-testid="composer-lock-takeover"
        disabled={busy}
        onClick={onTakeOver}
      >
        {t("ticket.takeOver")}
      </Button>
    </div>
  );
}
