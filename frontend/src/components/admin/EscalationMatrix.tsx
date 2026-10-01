import { useTranslation } from "react-i18next";
import { UnitInput } from "@/components/ui/UnitInput";
import { cn } from "@/lib/cn";
import { ESCALATION_STAGES, humanizeMinutes, toMinutes, type DurationUnits } from "./escalation";

/**
 * The queue's three escalation stages as one table: minutes until the stage
 * escalates (with a readable "= 2 Std 30 Min" below) and the warning
 * threshold in percent, which only means something while a time is set.
 * Writes the same six fields the old one-input-per-field form did; values are
 * passed through untouched (a disabled % keeps its stored value).
 */
export function EscalationMatrix({
  values,
  onChange,
  idFor,
}: {
  values: Record<string, unknown>;
  onChange: (name: string, value: number | "") => void;
  /** id / data-testid for a field's input (the drawer's `${prefix}-${name}`). */
  idFor: (name: string) => string;
}) {
  const { t } = useTranslation();
  const units: DurationUnits = {
    day: t("admin.queues.dialog.unitDay"),
    hour: t("admin.queues.dialog.unitHour"),
    minute: t("admin.queues.dialog.unitMinute"),
  };
  const display = (v: unknown) => (typeof v === "number" ? v : typeof v === "string" ? v : "");
  const parse = (raw: string): number | "" => (raw === "" ? "" : Number(raw));
  const cols =
    "sm:grid-cols-[minmax(0,1fr)_minmax(0,10.5rem)_minmax(0,10.5rem)] sm:gap-x-5";

  return (
    <div data-testid={idFor("escalation_matrix")}>
      <div
        aria-hidden="true"
        className={cn(
          "hidden border-b border-hairline px-3.5 pb-1.5 pt-2 text-xs font-medium text-muted sm:grid",
          cols,
        )}
      >
        <span>{t("admin.queues.dialog.stage")}</span>
        <span>{t("admin.queues.dialog.escalatesAfter")}</span>
        <span>{t("admin.queues.dialog.warnAt")}</span>
      </div>
      {ESCALATION_STAGES.map((s, idx) => {
        const stage = t(`admin.queues.dialog.stages.${s.key}`);
        const minutes = toMinutes(values[s.time]);
        const pct = toMinutes(values[s.notify]);
        const on = minutes > 0;
        const timeId = idFor(s.time);
        const notifyId = idFor(s.notify);
        const timeHuman = on
          ? t("admin.queues.dialog.equals", { time: humanizeMinutes(minutes, units) })
          : "";
        const notifyHuman =
          on && pct > 0
            ? t("admin.queues.dialog.warnAfter", {
                time: humanizeMinutes(Math.max(1, Math.round((minutes * pct) / 100)), units),
              })
            : "";
        return (
          <div
            key={s.key}
            role="group"
            aria-labelledby={`${timeId}-stage`}
            data-testid={`${idFor("escalation")}-${s.key}`}
            data-active={on}
            className={cn(
              "grid grid-cols-2 items-start gap-x-3 gap-y-1.5 px-3.5 py-2",
              cols,
              idx > 0 && "border-t border-hairline",
            )}
          >
            <div className="col-span-2 flex items-center gap-2 sm:col-span-1 sm:min-h-[2.125rem]">
              <span id={`${timeId}-stage`} className="text-[13px] font-medium text-ink">
                {stage}
              </span>
              <span
                data-testid={`${idFor("escalation")}-${s.key}-state`}
                className={cn(
                  "rounded-full border px-1.5 text-[11px] font-medium leading-4",
                  on ? "border-current text-green" : "border-hairline text-muted",
                )}
              >
                {on ? t("admin.queues.dialog.stageActive") : t("admin.queues.dialog.stageOff")}
              </span>
            </div>
            <div className="min-w-0">
              <UnitInput
                id={timeId}
                data-testid={timeId}
                unit={t("admin.queues.dialog.unitMinutesShort")}
                min={0}
                value={display(values[s.time])}
                placeholder="0"
                aria-label={t("admin.queues.dialog.escalatesAfterAria", { stage })}
                aria-describedby={`${timeId}-human`}
                onChange={(e) => onChange(s.time, parse(e.target.value))}
              />
              <span
                id={`${timeId}-human`}
                data-testid={`${timeId}-human`}
                className="mt-0.5 block min-h-[1.2em] text-[11.5px] tabular-nums text-muted"
              >
                {timeHuman}
              </span>
            </div>
            <div className="min-w-0">
              <UnitInput
                id={notifyId}
                data-testid={notifyId}
                unit="%"
                min={0}
                max={100}
                value={display(values[s.notify])}
                placeholder="0"
                disabled={!on}
                aria-label={t("admin.queues.dialog.warnAtAria", { stage })}
                aria-describedby={`${notifyId}-human`}
                onChange={(e) => onChange(s.notify, parse(e.target.value))}
              />
              <span
                id={`${notifyId}-human`}
                data-testid={`${notifyId}-human`}
                className="mt-0.5 block min-h-[1.2em] text-[11.5px] tabular-nums text-muted"
              >
                {notifyHuman}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
