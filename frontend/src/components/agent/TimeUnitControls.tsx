import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { TIME_PRESET_MINUTES, type TimeUnitMode } from "@/lib/timeUnits";

/** Minutes/hours switch shared by every time-booking field (header popover,
 * composer chip) so the choice reads and behaves identically everywhere. */
export function TimeUnitToggle({
  mode,
  onChange,
  size = "md",
  testId,
}: {
  mode: TimeUnitMode;
  onChange: (mode: TimeUnitMode) => void;
  size?: "sm" | "md";
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t("ticket.timeUnitMode")}
      className="inline-flex shrink-0 overflow-hidden rounded-md border border-hairline"
    >
      {(["min", "hours"] as const).map((m) => (
        <button
          key={m}
          type="button"
          data-testid={testId ? `${testId}-${m}` : undefined}
          aria-pressed={mode === m}
          onClick={() => onChange(m)}
          className={cn(
            "font-semibold transition-colors duration-100",
            size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-xs",
            mode === m
              ? "bg-accent text-accent-ink"
              : "bg-surface-subtle text-muted hover:text-ink",
          )}
        >
          {m === "min" ? t("ticket.timeUnitMin") : t("ticket.timeUnitHours")}
        </button>
      ))}
    </div>
  );
}

/** Quick-pick minute presets — always labeled in minutes; the caller converts
 * to the field's current display mode when applying the pick. */
export function TimePresetButtons({
  onPick,
  testId,
}: {
  onPick: (minutes: number) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-1">
      {TIME_PRESET_MINUTES.map((m) => (
        <button
          key={m}
          type="button"
          data-testid={testId ? `${testId}-${m}` : undefined}
          onClick={() => onPick(m)}
          className="rounded-full border border-hairline bg-surface px-2 py-0.5 text-[11px] tabular-nums text-ink transition-colors duration-100 hover:bg-surface-subtle"
        >
          {m} {t("ticket.timeUnitAbbrev")}
        </button>
      ))}
    </div>
  );
}
