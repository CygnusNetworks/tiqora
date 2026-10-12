import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n/locales";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { reportSchedulesApi } from "@/lib/reportSchedulesApi";
import { timeZoneOptions } from "@/lib/timeZone";
import { inputCls } from "../builder/builderUi";
import { recurrencePreviewDebounce, zoneSuffix } from "./scheduleUi";
import {
  WEEKDAYS,
  recurrenceRequest,
  recurrenceErrors,
  type RecurrenceKind,
  type RecurrenceState,
} from "./scheduleForm";

const KINDS: RecurrenceKind[] = ["daily", "weekly", "monthly", "yearly"];
const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

function useDebounced<T>(value: T): T {
  const [shown, setShown] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    if (JSON.stringify(shown) === key) return;
    const timer = setTimeout(() => setShown(value), recurrencePreviewDebounce.ms);
    return () => clearTimeout(timer);
    // `value` is represented by `key`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return shown;
}

/** Weekday name in the UI language (ISO 1 = Monday), short. */
function weekdayLabel(iso: number, locale: string): string {
  // 2024-01-01 is a Monday
  return new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(new Date(Date.UTC(2024, 0, iso)));
}

function monthLabel(m: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2024, m - 1, 1)));
}


/**
 * Recurrence editor: kind, time, weekdays / day of month / month, time zone and a live list
 * of the next five dates (asked from the server, debounced). Errors are keys of
 * `reports.schedules.errors`, shown under the field they belong to.
 */
export function RecurrenceFields({
  value,
  onChange,
  timeZone,
  onTimeZoneChange,
  errors,
}: {
  value: RecurrenceState;
  onChange: (next: RecurrenceState) => void;
  timeZone: string;
  onTimeZoneChange: (zone: string) => void;
  errors: Record<string, string>;
}) {
  const { t, i18n } = useTranslation();
  const bcp = toBcp47(i18n.resolvedLanguage ?? i18n.language ?? "de");
  const id = useId();
  const set = (patch: Partial<RecurrenceState>) => onChange({ ...value, ...patch });

  const complete = Object.keys(recurrenceErrors(value)).length === 0;
  const request = useDebounced(complete ? { recurrence: recurrenceRequest(value), timeZone } : null);
  const previewQ = useQuery({
    queryKey: ["report-schedules", "preview", request],
    queryFn: ({ signal }) => reportSchedulesApi.previewRecurrence(request!.recurrence, request!.timeZone, signal),
    enabled: request !== null,
    staleTime: 60_000,
    retry: false,
  });

  const err = (k: string) =>
    errors[k] ? (
      <p className="mt-1 text-xs text-danger" role="alert" data-testid={`recurrence-error-${k}`}>
        {t(errors[k])}
      </p>
    ) : null;

  return (
    <div className="space-y-3" data-testid="recurrence-fields">
      <SegmentedControl
        items={KINDS.map((k) => ({ value: k, label: t(`reports.schedules.recurrenceKind.${k}`) }))}
        value={value.kind}
        onChange={(kind) => set({ kind })}
        fullWidth
        testId="recurrence-kind"
        aria-label={t("reports.schedules.recurrence")}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-time`} className="block text-xs font-medium text-muted">
            {t("reports.schedules.time")}
          </label>
          <input
            id={`${id}-time`}
            type="time"
            className={cn(inputCls, "mt-1 w-full")}
            value={value.time}
            onChange={(e) => set({ time: e.target.value })}
            aria-invalid={errors.time ? true : undefined}
            data-testid="recurrence-time"
          />
          {err("time")}
        </div>
        <div>
          <label htmlFor={`${id}-zone`} className="block text-xs font-medium text-muted">
            {t("reports.schedules.timeZone")}
          </label>
          <div className="mt-1">
            <SelectField
              id={`${id}-zone`}
              items={zoneItems(timeZone)}
              value={timeZone}
              onChange={onTimeZoneChange}
              testId="recurrence-zone"
            />
          </div>
        </div>
      </div>

      {value.kind === "daily" && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <Switch
            checked={value.workdays_only}
            onChange={(workdays_only) => set({ workdays_only })}
            testId="recurrence-workdays"
            aria-label={t("reports.schedules.workdaysOnly")}
          />
          {t("reports.schedules.workdaysOnly")}
        </label>
      )}

      {value.kind === "weekly" && (
        <div>
          <span className="block text-xs font-medium text-muted">{t("reports.schedules.weekdays")}</span>
          <div className="mt-1 flex flex-wrap gap-1.5" role="group" aria-label={t("reports.schedules.weekdays")}>
            {WEEKDAYS.map((d) => {
              const on = value.weekdays.includes(d);
              return (
                <button
                  key={d}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    set({ weekdays: on ? value.weekdays.filter((x) => x !== d) : [...value.weekdays, d].sort((a, b) => a - b) })
                  }
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                    on
                      ? "border-accent/50 bg-accent-dim text-accent"
                      : "border-hairline bg-surface text-ink hover:bg-surface-subtle",
                  )}
                  data-testid={`recurrence-weekday-${d}`}
                >
                  {weekdayLabel(d, bcp)}
                </button>
              );
            })}
          </div>
          {err("weekdays")}
        </div>
      )}

      {(value.kind === "monthly" || value.kind === "yearly") && (
        <div className="grid gap-3 sm:grid-cols-2">
          {value.kind === "yearly" && (
            <div>
              <label htmlFor={`${id}-month`} className="block text-xs font-medium text-muted">
                {t("reports.schedules.month")}
              </label>
              <div className="mt-1">
                <SelectField
                  id={`${id}-month`}
                  items={MONTHS.map((m) => ({ value: m, label: monthLabel(m, bcp) }))}
                  value={value.month}
                  onChange={(month) => set({ month })}
                  placeholder={t("reports.schedules.month")}
                  invalid={Boolean(errors.month)}
                  testId="recurrence-month"
                />
              </div>
              {err("month")}
            </div>
          )}
          <div>
            <label htmlFor={`${id}-dom`} className="block text-xs font-medium text-muted">
              {t("reports.schedules.dayOfMonth")}
            </label>
            <input
              id={`${id}-dom`}
              type="number"
              min={1}
              max={31}
              className={cn(inputCls, "mt-1 w-full")}
              value={value.day_of_month ?? ""}
              onChange={(e) => set({ day_of_month: e.target.value === "" ? null : Number(e.target.value) })}
              aria-invalid={errors.day_of_month ? true : undefined}
              aria-describedby={`${id}-dom-hint`}
              data-testid="recurrence-day-of-month"
            />
            <p id={`${id}-dom-hint`} className="mt-1 text-xs text-muted">
              {t("reports.schedules.dayOfMonthHint")}
            </p>
            {err("day_of_month")}
          </div>
        </div>
      )}

      <div data-testid="recurrence-preview" aria-live="polite">
        <span className="block text-xs font-medium text-muted">{t("reports.schedules.nextRuns")}</span>
        {previewQ.data ? (
          <>
            <p className="mt-1 text-sm text-ink" data-testid="recurrence-text">
              {zoneSuffix(previewQ.data.text, timeZone)}
            </p>
            <ul className="mt-1 space-y-0.5 text-sm text-muted" data-testid="recurrence-next-runs">
              {previewQ.data.next_runs.map((iso) => (
                <li key={iso}>{formatDateTime(iso, bcp)}</li>
              ))}
            </ul>
          </>
        ) : previewQ.isError ? (
          <p className="mt-1 text-sm text-muted">{t("reports.schedules.previewFailed")}</p>
        ) : null}
      </div>
    </div>
  );
}

let zoneCache: { value: string; label: string; hint: string }[] | null = null;
function zoneItems(current: string) {
  zoneCache ??= timeZoneOptions();
  return zoneCache.some((z) => z.value === current) ? zoneCache : [{ value: current, label: current, hint: "" }, ...zoneCache];
}
