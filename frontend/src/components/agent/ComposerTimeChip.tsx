import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

/**
 * Minute field for a composer footer, sized to sit next to the send button
 * rather than in a panel of its own. Empty means "book nothing" and the chip
 * stays quiet; a value tints it accent so it is obvious that sending will
 * also book. Sending is still called "Senden" — booking is a side effect of
 * the field being filled, not a second decision at the button.
 */
export function ComposerTimeChip({
  value,
  onChange,
  testId = "composer-time",
}: {
  value: string;
  onChange: (value: string) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const filled = value.trim() !== "" && Number(value) > 0;

  return (
    <label
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] transition-colors duration-100",
        filled
          ? "border-accent/40 bg-accent-dim text-accent"
          : "border-hairline text-muted hover:text-ink",
      )}
      title={t("ticket.timeChipHint")}
    >
      <span aria-hidden>⏱</span>
      <input
        type="number"
        min="0"
        step="0.25"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="0"
        aria-label={t("ticket.timeUnits")}
        data-testid={testId}
        className="w-9 border-none bg-transparent p-0 text-right font-mono text-[11px] tabular-nums text-current placeholder:text-muted focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      />
      <span aria-hidden>{t("ticket.timeUnitAbbrev")}</span>
    </label>
  );
}
