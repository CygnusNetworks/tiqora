import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { cn } from "@/lib/cn";
import { dayKey } from "@/lib/calendarMath";
import type { CalendarChange, FreezeOccurrence } from "@/lib/changeApi";
import { CALENDAR_ZONE, PHASE_BAR, overlapsDay, spanDayKeys } from "@/lib/changeCalendar";
import { formatCalendarDay } from "@/lib/format";
import { changeDetailHref } from "@/lib/itsmRoutes";
import { ymdInZone } from "@/lib/timeZone";
import { CalendarConflictMark } from "./CalendarConflictMark";
import { ChangeTypeBadge } from "./ChangeTypeBadge";
import { PHASE_TONE, utcText } from "./changeUi";

/** The seven days of a week, each with the freeze windows of the day and the changes running on it. */
export function ChangeWeekList({
  days,
  changes,
  freezes,
  serviceName,
  locale,
}: {
  days: Date[];
  changes: CalendarChange[];
  freezes: FreezeOccurrence[];
  serviceName: Map<number, string>;
  locale: string;
}) {
  const { t } = useTranslation();
  const todayKey = ymdInZone(new Date(), CALENDAR_ZONE);
  return (
    <div className="space-y-2" data-testid="change-calendar-week">
      {days.map((day) => {
        const key = dayKey(day);
        const rows = changes
          .filter((c) => c.planned_start && c.planned_end && spanDayKeys(c.planned_start, c.planned_end).includes(key))
          .sort((a, b) => (a.planned_start ?? "").localeCompare(b.planned_start ?? ""));
        const dayFreezes = freezes.filter((f) => overlapsDay(f.start, f.end, key));
        return (
          <section
            key={key}
            data-testid={`change-week-day-${key}`}
            className={cn("rounded-lg border border-hairline bg-surface", key === todayKey && "border-accent/60")}
          >
            <h3 className="flex flex-wrap items-center gap-2 border-b border-hairline px-3 py-1.5 text-xs font-semibold text-muted">
              {formatCalendarDay(key, locale, { weekday: "long", day: "numeric", month: "long" })}
              {dayFreezes.map((f) => (
                <Badge
                  key={`${f.freeze_id}-${f.start}`}
                  tone={f.enforcement === "block" ? "danger" : "warn"}
                  data-testid={`change-week-freeze-${f.freeze_id}`}
                  data-enforcement={f.enforcement}
                >
                  {t(`itsm.change.calendar.freeze.${f.enforcement}`)}: {f.name}
                </Badge>
              ))}
            </h3>
            {rows.length === 0 ? (
              <p className="px-3 py-2 text-xs text-muted">{t("itsm.change.calendar.noChanges")}</p>
            ) : (
              <ul className="divide-y divide-hairline">
                {rows.map((c) => (
                  <li key={c.ticket_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm" data-testid={`change-week-row-${c.ticket_id}`}>
                    <span className={cn("h-3 w-1 shrink-0 self-stretch rounded", PHASE_BAR[c.phase])} aria-hidden />
                    <CalendarConflictMark severity={c.conflict_severity} className="mr-0" />
                    <RouterAnchor href={changeDetailHref(c.ticket_id)} className="min-w-0 break-words font-medium text-accent hover:underline">
                      {c.title}
                    </RouterAnchor>
                    <span className="font-mono text-xs text-muted">{c.tn}</span>
                    <ChangeTypeBadge type={c.change_type} />
                    <Badge tone={PHASE_TONE[c.phase]}>{t(`itsm.change.phases.${c.phase}`)}</Badge>
                    <span className="text-xs text-muted">
                      {utcText(c.planned_start)} – {utcText(c.planned_end)}
                    </span>
                    {c.service_ids.length > 0 && (
                      <span className="min-w-0 break-words text-xs text-muted">
                        {c.service_ids.map((id) => serviceName.get(id) ?? `#${id}`).join(", ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
