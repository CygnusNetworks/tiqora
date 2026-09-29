import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import {
  CALLBACK_PRESETS,
  callbackPresetDate,
  toLocalInputValue,
  type CallbackPreset,
} from "@/lib/phoneCall";

const presetCls =
  "rounded-md border border-hairline px-2 py-0.5 text-xs text-muted transition-colors duration-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * Callback / pending-until picker: presets (in 1 h · today 16:00 · tomorrow
 * 9:00) plus a free date-time field. `value` is a `datetime-local` string
 * (local time), empty when nothing is picked yet.
 */
export function PendingTimeInput({
  value,
  onChange,
  testId = "pending-time",
}: {
  value: string;
  onChange: (value: string) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const now = new Date();
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-testid={testId}>
      {CALLBACK_PRESETS.map((preset: CallbackPreset) => {
        const at = callbackPresetDate(preset, now);
        const on = at !== null && value === toLocalInputValue(at);
        return (
          <button
            key={preset}
            type="button"
            disabled={at === null}
            data-testid={`${testId}-preset-${preset}`}
            aria-pressed={on}
            onClick={() => at && onChange(toLocalInputValue(at))}
            className={cn(presetCls, on && "border-accent/50 bg-accent-dim text-accent")}
          >
            {t(`phone.preset.${preset}`)}
          </button>
        );
      })}
      <input
        type="datetime-local"
        value={value}
        min={toLocalInputValue(now)}
        aria-label={t("phone.preset.custom")}
        data-testid={`${testId}-input`}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-hairline bg-surface px-1.5 py-0.5 text-xs text-ink focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      />
    </span>
  );
}
