import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";
import { dayKey, groupByDay, isCurrentMonth, isSameDay, monthGridDays } from "@/lib/calendarMath";
import type { OccurrenceOut } from "@/lib/api";

const WEEKDAY_KEYS = [
  "calendar.weekday.mon",
  "calendar.weekday.tue",
  "calendar.weekday.wed",
  "calendar.weekday.thu",
  "calendar.weekday.fri",
  "calendar.weekday.sat",
  "calendar.weekday.sun",
] as const;

export function MonthGrid({
  anchor,
  occurrences,
  calendarColors,
  onSelectDay,
  onSelectOccurrence,
}: {
  anchor: Date;
  occurrences: OccurrenceOut[];
  calendarColors?: Record<number, string>;
  onSelectDay?: (day: Date) => void;
  onSelectOccurrence?: (occ: OccurrenceOut) => void;
}) {
  const { t } = useTranslation();
  const days = monthGridDays(anchor);
  const byDay = groupByDay(occurrences);
  const today = new Date();

  return (
    <div data-testid="calendar-month-grid" className="flex min-h-0 flex-1 flex-col">
      <div className="grid grid-cols-7 border-b border-hairline text-center text-[11px] font-semibold uppercase tracking-wide text-muted">
        {WEEKDAY_KEYS.map((key) => (
          <div key={key} className="py-1.5">
            {t(key)}
          </div>
        ))}
      </div>
      <div className="grid flex-1 grid-cols-7 grid-rows-6">
        {days.map((day) => {
          const key = dayKey(day);
          const dayOccurrences = byDay.get(key) ?? [];
          const inMonth = isCurrentMonth(day, anchor);
          const isToday = isSameDay(day, today);
          return (
            <button
              type="button"
              key={key}
              data-testid={`calendar-day-${key}`}
              onClick={() => onSelectDay?.(day)}
              className={cn(
                "flex min-h-[84px] flex-col items-stretch gap-0.5 border-b border-r border-hairline p-1 text-left align-top",
                !inMonth && "bg-surface-subtle/50 text-muted",
              )}
            >
              <span
                className={cn(
                  "self-start rounded-full px-1.5 text-xs font-medium",
                  isToday && "bg-accent text-accent-ink",
                )}
              >
                {day.getDate()}
              </span>
              <div className="flex flex-col gap-0.5 overflow-hidden">
                {dayOccurrences.slice(0, 3).map((occ) => (
                  <span
                    key={`${occ.appointment_id}-${occ.start_time}`}
                    data-testid={`calendar-occurrence-${occ.appointment_id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectOccurrence?.(occ);
                    }}
                    className="truncate rounded px-1 py-0.5 text-[11px] font-medium text-ink"
                    style={{
                      backgroundColor: calendarColors?.[occ.calendar_id] ?? "var(--color-accent-dim)",
                    }}
                    title={occ.title}
                  >
                    {occ.title}
                  </span>
                ))}
                {dayOccurrences.length > 3 && (
                  <span className="text-[10px] text-muted">
                    {t("calendar.more", { count: dayOccurrences.length - 3 })}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
