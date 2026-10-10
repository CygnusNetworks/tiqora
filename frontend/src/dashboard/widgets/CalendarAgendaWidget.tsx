import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import type { OccurrenceOut } from "@/lib/api";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";
import { formatCalendarDay, formatTimeOfDay } from "@/lib/format";
import { displayTimeZone, ymdInZone } from "@/lib/timeZone";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataBody, WidgetEmpty } from "../chrome/WidgetDataBody";

export interface CalendarAgendaSettings {
  days: number;
  limit: number;
}

export interface CalendarAgendaData {
  items: OccurrenceOut[];
}

/** The backend sends naive UTC times (no zone suffix); the demo sends `…Z`. Both are UTC. */
function parseUtc(value: string): Date {
  return new Date(/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`);
}

/** Today's (or the next days') appointments; times in the viewer's zone, all-day ones by date. */
export function CalendarAgendaWidget({ settings, size, mode }: WidgetProps<CalendarAgendaSettings>) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const days = Math.max(1, settings.days);
  const zone = displayTimeZone();
  const today = ymdInZone(new Date(), zone);
  return (
    <WidgetFrame
      title={days === 1 ? t("dashboards.widgets.calendarAgenda.today") : t("dashboards.widgets.calendarAgenda.days", { count: days })}
      link={{ label: t("dashboards.widgets.calendarAgenda.open"), to: "/agent/calendar" }}
      mode={mode}
      testId="calendar-agenda-widget"
    >
      <WidgetDataBody<CalendarAgendaData> h={size.h}>
        {(data) => {
          const items = Array.isArray(data?.items) ? data.items : [];
          if (items.length === 0)
            return (
              <WidgetEmpty>
                {days === 1
                  ? t("dashboards.widgets.calendarAgenda.empty")
                  : t("dashboards.widgets.calendarAgenda.emptyDays", { count: days })}
              </WidgetEmpty>
            );
          return (
            <ul className="flex flex-col gap-2.5">
              {items.map((o) => {
                const start = parseUtc(o.start_time);
                const otherDay = days > 1 && ymdInZone(start, zone) !== today;
                const when = o.all_day
                  ? formatCalendarDay(o.start_time.slice(0, 10), locale, { day: "2-digit", month: "2-digit" })
                  : otherDay
                    ? `${new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: zone }).format(start)} ${formatTimeOfDay(start, locale, zone)}`
                    : formatTimeOfDay(start, locale, zone);
                return (
                  <li key={`${o.appointment_id}-${o.start_time}`} className="flex items-baseline gap-3 text-sm">
                    <span
                      data-testid={`agenda-${o.appointment_id}-time`}
                      className={cn("shrink-0 font-mono text-xs tabular-nums text-muted", days > 1 ? "w-20" : "w-11")}
                    >
                      {when}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-ink">{o.title}</span>
                      {o.location && <span className="block truncate text-xs text-muted">{o.location}</span>}
                    </span>
                  </li>
                );
              })}
            </ul>
          );
        }}
      </WidgetDataBody>
    </WidgetFrame>
  );
}
