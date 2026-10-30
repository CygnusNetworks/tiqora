import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { cn } from "@/lib/cn";
import { dayKey } from "@/lib/calendarMath";
import type { ServiceAvailability } from "@/lib/changeApi";
import { overlapsDay } from "@/lib/changeCalendar";
import { formatCalendarDay } from "@/lib/format";
import { changeDetailHref } from "@/lib/itsmRoutes";
import { CalendarConflictMark } from "./CalendarConflictMark";
import { utcText } from "./changeUi";

type Window = ServiceAvailability["windows"][number];

/** `1 h 30 min`, `45 min`. */
function minutesText(minutes: number, t: (k: string, o?: Record<string, unknown>) => string): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? t("itsm.change.calendar.hoursMinutes", { hours: h, minutes: m }) : t("itsm.change.calendar.minutesOnly", { minutes: m });
}

/**
 * Projected service availability (PSA): services as rows, days as columns. A cell counts the change
 * windows of the service on that day and is shaded by the freeze windows on it; a change inside a
 * freeze carries the conflict mark. A click lists the windows of the cell.
 */
export function ChangeServiceTable({ services, days, locale }: { services: ServiceAvailability[]; days: Date[]; locale: string }) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<{ serviceId: number; key: string } | null>(null);
  const keys = days.map(dayKey);

  if (services.length === 0) {
    return (
      <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted" data-testid="change-service-empty">
        {t("itsm.change.calendar.serviceEmpty")}
      </p>
    );
  }

  const pickedService = picked ? services.find((s) => s.service_id === picked.serviceId) : undefined;
  const pickedWindows: Window[] =
    picked && pickedService ? pickedService.windows.filter((w) => overlapsDay(w.start, w.end, picked.key)).sort((a, b) => a.start.localeCompare(b.start)) : [];

  return (
    <div className="space-y-3" data-testid="change-calendar-services">
      <div className="max-w-full overflow-x-auto rounded-lg border border-hairline bg-surface">
        <table className="w-full border-collapse text-xs">
          <thead className="border-b border-hairline text-muted">
            <tr>
              <th className="sticky left-0 z-10 min-w-[8rem] bg-surface px-3 py-2 text-left font-medium">{t("itsm.change.list.service")}</th>
              <th className="px-2 py-2 text-right font-medium">{t("itsm.change.calendar.colChanges")}</th>
              <th className="px-2 py-2 text-right font-medium">{t("itsm.change.calendar.colMinutes")}</th>
              {keys.map((key) => (
                <th key={key} className="min-w-[2.5rem] px-1 py-2 text-center font-medium">
                  {formatCalendarDay(key, locale, { day: "numeric", month: "numeric" })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {services.map((s) => (
              <tr key={s.service_id} data-testid={`change-service-row-${s.service_id}`}>
                <th scope="row" className="sticky left-0 z-10 bg-surface px-3 py-2 text-left font-medium text-ink">
                  {s.service_name}
                </th>
                <td className="px-2 py-2 text-right tabular-nums text-ink">{s.change_count}</td>
                <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-muted">{minutesText(s.minutes_affected, t)}</td>
                {keys.map((key) => {
                  const here = s.windows.filter((w) => overlapsDay(w.start, w.end, key));
                  const changes = here.filter((w) => w.kind === "change");
                  const freezes = here.filter((w) => w.kind === "freeze");
                  const level = freezes.some((w) => w.enforcement === "block") ? "block" : freezes.length > 0 ? "warn" : null;
                  const selected = picked?.serviceId === s.service_id && picked.key === key;
                  return (
                    <td
                      key={key}
                      className={cn("p-0", level === "block" && "bg-danger/15", level === "warn" && "bg-escalation/15")}
                      data-freeze={level ?? undefined}
                    >
                      <button
                        type="button"
                        data-testid={`change-service-cell-${s.service_id}-${key}`}
                        data-changes={changes.length}
                        disabled={here.length === 0}
                        aria-pressed={selected}
                        onClick={() => setPicked(selected ? null : { serviceId: s.service_id, key })}
                        className={cn(
                          "flex h-9 w-full items-center justify-center gap-0.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
                          selected && "ring-2 ring-inset ring-accent",
                        )}
                      >
                        {changes.length > 0 && level && <CalendarConflictMark severity={level} className="mr-0" testId="change-service-conflict" />}
                        {changes.length > 0 && <span className="rounded bg-accent px-1.5 py-0.5 font-semibold tabular-nums text-accent-ink">{changes.length}</span>}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {picked && pickedService && (
        <section className="rounded-lg border border-hairline bg-surface p-3 text-sm" data-testid="change-service-detail">
          <h3 className="mb-2 text-xs font-semibold text-muted">
            {pickedService.service_name} · {formatCalendarDay(picked.key, locale, { dateStyle: "full" })}
          </h3>
          <ul className="space-y-1.5">
            {pickedWindows.map((w) => (
              <li key={`${w.kind}-${w.ref_id}-${w.start}`} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {w.kind === "change" ? (
                  <>
                    <RouterAnchor href={changeDetailHref(w.ref_id)} className="min-w-0 break-words text-accent hover:underline">
                      {w.title}
                    </RouterAnchor>
                    <span className="font-mono text-xs text-muted">{w.tn}</span>
                    {w.phase && <Badge tone="muted">{t(`itsm.change.phases.${w.phase}`)}</Badge>}
                    {w.direct === false && <Badge tone="muted">{t("itsm.change.calendar.indirect")}</Badge>}
                  </>
                ) : (
                  <>
                    <span className="min-w-0 break-words text-ink">{w.title}</span>
                    <Badge tone={w.enforcement === "block" ? "danger" : "warn"}>
                      {t(`itsm.change.calendar.freeze.${w.enforcement === "block" ? "block" : "warn"}`)}
                    </Badge>
                  </>
                )}
                <span className="text-xs text-muted">
                  {utcText(w.start)} – {utcText(w.end)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
