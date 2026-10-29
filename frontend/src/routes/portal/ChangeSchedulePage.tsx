import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { toBcp47 } from "@/i18n";
import { ApiError } from "@/lib/api";
import { formatCalendarDay, formatDateTime } from "@/lib/format";
import { portalChangeApi, portalChangeKeys, type PortalChange } from "@/lib/portalChangeApi";
import { wallClockDate, ymdInZone, zonedDayStartIso } from "@/lib/timeZone";

const PHASE_TONE = { scheduled: "accent", implementing: "warn", closed: "muted" } as const;
const TYPE_TONE = { standard: "muted", normal: "default", emergency: "danger" } as const;

const pad = (n: number) => String(n).padStart(2, "0");

/** `[first of the month, first of the next month)` of the month holding `anchor`, in the display zone. */
function monthRange(anchor: Date): { start: string; end: string } {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const next = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1);
  const iso = (d: Date) => zonedDayStartIso(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-01`) ?? d.toISOString();
  return { start: iso(first), end: iso(next) };
}

/**
 * `/portal/change-schedule`: the planned maintenance windows that affect the customer's services,
 * month by month (never more than 31 days per request, the API allows 92). A 404 means the
 * schedule is switched off.
 */
export function ChangeSchedulePage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const [anchor, setAnchor] = useState(() => wallClockDate());
  const { start, end } = useMemo(() => monthRange(anchor), [anchor]);
  const list = useQuery({
    queryKey: portalChangeKeys.list(start, end),
    queryFn: ({ signal }) => portalChangeApi.list(start, end, signal),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const unavailable = list.error instanceof ApiError && list.error.status === 404;

  const groups = useMemo(() => {
    const byDay = new Map<string, PortalChange[]>();
    for (const c of list.data?.items ?? []) {
      const key = c.planned_start ? ymdInZone(c.planned_start) : "";
      byDay.set(key, [...(byDay.get(key) ?? []), c]);
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [list.data]);

  const step = (delta: number) => setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1));
  const label = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(anchor);

  return (
    <div className="space-y-4" data-testid="portal-change-schedule-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("portal.changes.title")}</h1>
      <p className="text-sm text-muted">{t("portal.changes.intro")}</p>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" onClick={() => step(-1)} aria-label={t("portal.changes.prev")} data-testid="portal-changes-prev">
          ‹
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setAnchor(wallClockDate())} data-testid="portal-changes-today">
          {t("calendar.today")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => step(1)} aria-label={t("portal.changes.next")} data-testid="portal-changes-next">
          ›
        </Button>
        <span className="ml-1 text-sm font-medium text-ink" data-testid="portal-changes-label">
          {label}
        </span>
      </div>

      {list.data?.truncated && (
        <p className="rounded-md border border-escalation/40 bg-escalation/10 px-3 py-2 text-xs text-ink" role="status" data-testid="portal-changes-truncated">
          {t("portal.changes.truncated")}
        </p>
      )}

      {unavailable ? (
        <p className="text-sm text-muted" role="alert" data-testid="portal-changes-unavailable">
          {t("portal.changes.unavailable")}
        </p>
      ) : list.isError ? (
        <p className="text-sm text-danger" role="alert" data-testid="portal-changes-error">
          {t("portal.changes.loadError")}
        </p>
      ) : list.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : groups.length === 0 ? (
        <div className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center" data-testid="portal-changes-empty">
          <p className="text-sm text-muted">{t("portal.changes.empty")}</p>
        </div>
      ) : (
        <div className="space-y-4" data-testid="portal-changes-list">
          {groups.map(([day, items]) => (
            <section key={day || "none"}>
              <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                {day ? formatCalendarDay(day, locale, { weekday: "long", day: "numeric", month: "long" }) : t("portal.changes.noWindow")}
              </h2>
              <ul className="space-y-2">
                {items.map((c) => (
                  <li key={c.tn} className="rounded-lg border border-hairline bg-surface px-4 py-3" data-testid={`portal-change-${c.tn}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="min-w-0 break-words text-sm font-medium text-ink">{c.title}</p>
                      <Badge tone={PHASE_TONE[c.phase]} data-phase={c.phase}>
                        {t(`portal.changes.phases.${c.phase}`)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      <span className="font-mono">{c.tn}</span>
                      {" · "}
                      {c.planned_start && c.planned_end
                        ? `${formatDateTime(c.planned_start, locale)} – ${formatDateTime(c.planned_end, locale)}`
                        : t("portal.changes.noWindow")}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Badge tone={TYPE_TONE[c.change_type]}>{t(`itsm.change.types.${c.change_type}`)}</Badge>
                      {c.services.map((s) => (
                        <Badge key={s} tone="muted">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
