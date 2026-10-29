import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { MonthGrid } from "@/components/agent/calendar/MonthGrid";
import { CalendarConflictMark } from "@/components/itsm/change/CalendarConflictMark";
import { ChangeServiceTable } from "@/components/itsm/change/ChangeServiceTable";
import { ChangeWeekList } from "@/components/itsm/change/ChangeWeekList";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { toBcp47 } from "@/i18n";
import { api, type OccurrenceOut } from "@/lib/api";
import { cn } from "@/lib/cn";
import { changeApi, changeErrorCode, changeKeys, type CalendarChange } from "@/lib/changeApi";
import {
  CALENDAR_ZONE,
  PHASE_BAR,
  dayFreezeLevel,
  spanDayKeys,
  stepAnchor,
  viewRange,
  type CalendarView,
} from "@/lib/changeCalendar";
import { formatCalendarDay } from "@/lib/format";
import { changeDetailHref } from "@/lib/itsmRoutes";
import { addDaysYmd, wallClockDate, zonedDayStartIso } from "@/lib/timeZone";

const VIEWS: CalendarView[] = ["month", "week", "service"];

/** One day-sized pseudo occurrence per day a change spans, so the shared month grid can show it. */
function changeOccurrences(changes: CalendarChange[]): OccurrenceOut[] {
  const out: OccurrenceOut[] = [];
  for (const c of changes) {
    if (!c.planned_start || !c.planned_end) continue;
    for (const key of spanDayKeys(c.planned_start, c.planned_end)) {
      out.push({
        all_day: false,
        appointment_id: c.ticket_id,
        calendar_id: 0,
        description: null,
        end_time: c.planned_end,
        is_recurring: false,
        location: null,
        start_time: zonedDayStartIso(key, CALENDAR_ZONE) ?? c.planned_start,
        title: c.title,
      });
    }
  }
  return out;
}

/**
 * `/agent/itsm/changes/calendar`: the planned changes as a month grid or a week list, with the
 * freeze windows as shaded days (red: blocking, amber: warning) and a conflict mark, plus the
 * "Nach Service" table of projected service availability. All times are UTC; a view never asks
 * for more than 92 days.
 */
export function ChangeCalendarPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const locale = toBcp47(i18n.language);
  const [view, setView] = useState<CalendarView>("month");
  const [anchor, setAnchor] = useState(() => wallClockDate(new Date(), CALENDAR_ZONE));
  const [serviceId, setServiceId] = useState<number | "">("");

  const { start, end, days } = useMemo(() => viewRange(view, anchor), [view, anchor]);
  const serviceFilter = serviceId === "" ? null : serviceId;

  const servicesQ = useQuery({ queryKey: ["reference", "services"], queryFn: () => api.listReferenceServices() });
  const services = useMemo(() => servicesQ.data ?? [], [servicesQ.data]);
  const serviceName = useMemo(() => new Map(services.map((s) => [s.id, s.name])), [services]);

  const calQuery = { start, end, service_id: serviceFilter === null ? undefined : [serviceFilter], include_tasks: false };
  const calQ = useQuery({
    queryKey: [...changeKeys.calendar(calQuery), view],
    queryFn: ({ signal }) => changeApi.calendar(calQuery, signal),
    enabled: view !== "service",
    // Stepping keeps the old grid until the new one arrives; a view switch shows the loading state.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey.at(-1) === view ? previous : undefined),
    retry: false,
  });
  const svcQ = useQuery({
    queryKey: changeKeys.calendarServices(start, end),
    queryFn: ({ signal }) => changeApi.calendarServices(start, end, signal),
    enabled: view === "service",
    placeholderData: keepPreviousData,
    retry: false,
  });

  const active = view === "service" ? svcQ : calQ;
  const errorCode = active.isError ? changeErrorCode(active.error) : null;
  const changes = useMemo(() => calQ.data?.changes ?? [], [calQ.data]);
  const freezes = useMemo(() => calQ.data?.freezes ?? [], [calQ.data]);
  const occurrences = useMemo(() => changeOccurrences(changes), [changes]);
  const byId = useMemo(() => new Map(changes.map((c) => [c.ticket_id, c])), [changes]);
  const tableServices = useMemo(
    () => (svcQ.data ?? []).filter((s) => serviceFilter === null || s.service_id === serviceFilter),
    [svcQ.data, serviceFilter],
  );

  /** A day of the month grid opens as the week list around it. */
  const openDay = (day: Date) => {
    setAnchor(day);
    setView("week");
  };

  const label =
    view === "month"
      ? new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: "UTC" }).format(Date.UTC(anchor.getFullYear(), anchor.getMonth(), 1, 12))
      : `${formatCalendarDay(start.slice(0, 10), locale)} – ${formatCalendarDay(addDaysYmd(end.slice(0, 10), -1), locale)}`;

  const hasData = view === "service" ? svcQ.data !== undefined : calQ.data !== undefined;

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl space-y-3 px-4 py-6" data-testid="changeCalendarPage">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.change.pages.calendar")}</h1>
        <RouterAnchor href="/agent/itsm/changes" className="text-sm text-accent hover:underline" data-testid="change-calendar-list-link">
          {t("itsm.change.pages.list")}
        </RouterAnchor>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          ariaLabel={t("itsm.change.pages.calendar")}
          value={view}
          onChange={(id) => setView(id as CalendarView)}
          items={VIEWS.map((v) => ({ id: v, label: t(`itsm.change.calendar.views.${v}`) }))}
        />
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => setAnchor((a) => stepAnchor(view, a, -1))} aria-label={t("itsm.change.calendar.prev")} data-testid="change-calendar-prev">
            ‹
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setAnchor(wallClockDate(new Date(), CALENDAR_ZONE))} data-testid="change-calendar-today">
            {t("calendar.today")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAnchor((a) => stepAnchor(view, a, 1))} aria-label={t("itsm.change.calendar.next")} data-testid="change-calendar-next">
            ›
          </Button>
          <span className="ml-1 text-sm font-medium text-ink" data-testid="change-calendar-label">
            {label}
          </span>
        </div>
        <div className="w-full sm:ml-auto sm:w-56">
          <SelectField
            items={[{ value: "" as number | "", label: t("itsm.change.list.allServices") }, ...services.map((s) => ({ value: s.id as number | "", label: s.name }))]}
            value={serviceId}
            onChange={setServiceId}
            testId="change-calendar-service"
          />
        </div>
      </div>

      <p className="text-xs text-muted">{t("itsm.change.calendar.utcNote")}</p>
      <Legend />

      {calQ.data?.truncated && view !== "service" && (
        <p className="rounded-md border border-escalation/40 bg-escalation/10 px-3 py-2 text-xs text-ink" role="status" data-testid="change-calendar-truncated">
          {t("itsm.change.calendar.truncated")}
        </p>
      )}
      {calQ.data?.freezes_truncated && view !== "service" && (
        <p className="rounded-md border border-escalation/40 bg-escalation/10 px-3 py-2 text-xs text-ink" role="status" data-testid="change-calendar-freezes-truncated">
          {t("itsm.change.calendar.freezesTruncated")}
        </p>
      )}

      {active.isError && !hasData ? (
        <div className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-danger" role="alert" data-testid="change-calendar-error" data-code={errorCode ?? undefined}>
          <p>{t([`itsm.change.errors.${errorCode}`, "itsm.change.errors.generic"])}</p>
          {errorCode === "change_mode_a" && (
            <RouterAnchor href="/agent/itsm/legacy-changes" className="mt-2 inline-block text-accent hover:underline">
              {t("itsm.change.pages.legacyList")}
            </RouterAnchor>
          )}
        </div>
      ) : active.isLoading || !hasData ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : view === "month" ? (
        <div className="min-w-0 overflow-hidden rounded-lg border border-hairline bg-surface">
          <MonthGrid
            anchor={anchor}
            zone={CALENDAR_ZONE}
            occurrences={occurrences}
            onSelectDay={openDay}
            onSelectMore={openDay}
            onSelectOccurrence={(occ) => void navigate({ to: changeDetailHref(occ.appointment_id) })}
            dayClassName={(key) => {
              const level = dayFreezeLevel(freezes, key);
              return level === "block" ? "bg-danger/10" : level === "warn" ? "bg-escalation/10" : undefined;
            }}
            dayAdornment={(key) => {
              const level = dayFreezeLevel(freezes, key);
              return level ? (
                <span
                  data-testid={`change-freeze-day-${key}`}
                  data-enforcement={level}
                  className={cn("truncate text-[10px] font-medium", level === "block" ? "text-danger" : "text-escalation")}
                >
                  {t(`itsm.change.calendar.freeze.${level}`)}
                </span>
              ) : null;
            }}
            occurrenceClassName={(occ) => {
              const c = byId.get(occ.appointment_id);
              return cn(c ? PHASE_BAR[c.phase] : "bg-accent-dim", c?.change_type === "emergency" && "border-l-2 border-danger");
            }}
            occurrenceAdornment={(occ) => <CalendarConflictMark severity={byId.get(occ.appointment_id)?.conflict_severity} className="mr-0.5" testId={`change-conflict-${occ.appointment_id}`} />}
          />
        </div>
      ) : view === "week" ? (
        <ChangeWeekList days={days} changes={changes} freezes={freezes} serviceName={serviceName} locale={locale} />
      ) : (
        <ChangeServiceTable services={tableServices} days={days} locale={locale} />
      )}
    </div>
  );
}

function Legend() {
  const { t } = useTranslation();
  const swatch = (cls: string, text: string, testId: string) => (
    <span className="inline-flex items-center gap-1" data-testid={testId}>
      <span className={cn("h-3 w-3 rounded-sm border border-hairline", cls)} aria-hidden />
      {text}
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted" data-testid="change-calendar-legend">
      {swatch(PHASE_BAR.approval, t("itsm.change.phases.approval"), "legend-approval")}
      {swatch(PHASE_BAR.scheduled, t("itsm.change.phases.scheduled"), "legend-scheduled")}
      {swatch(PHASE_BAR.implementing, t("itsm.change.phases.implementing"), "legend-implementing")}
      {swatch("bg-danger/20", t("itsm.change.calendar.freeze.block"), "legend-freeze-block")}
      {swatch("bg-escalation/20", t("itsm.change.calendar.freeze.warn"), "legend-freeze-warn")}
      <span className="inline-flex items-center">
        <CalendarConflictMark severity="warn" testId="legend-conflict" />
        {t("itsm.change.calendar.conflict")}
      </span>
    </div>
  );
}
