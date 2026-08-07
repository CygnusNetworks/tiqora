import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { TimeAccountingReportEntry } from "@tiqora/api-client";
import { toBcp47 } from "@/i18n";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { Spinner } from "@/components/ui/Spinner";
import { StatTile } from "@/components/agent/stats/StatTile";
import { cn } from "@/lib/cn";
import { formatDateOnly } from "@/lib/format";
import { formatYmd } from "@/lib/dateRanges";
import {
  TIME_RANGE_PRESETS,
  presetForRange,
  rangeForPreset,
  type TimeRangePreset,
} from "@/lib/dateRange";

export type TimeAccountingSearch = {
  create_by?: number;
  ticket_id?: number;
  created_from?: string;
  created_to?: string;
  offset?: number;
};

/** One calendar day of bookings, in the order the API returned them. */
type DayGroup = {
  /** Local YYYY-MM-DD, or "" for rows without a create_time. */
  key: string;
  rows: TimeAccountingReportEntry[];
  units: number;
};

/** Group rows by local calendar day, preserving the server-side ordering. */
function groupByDay(rows: TimeAccountingReportEntry[]): DayGroup[] {
  const groups = new Map<string, DayGroup>();
  for (const row of rows) {
    const parsed = row.create_time ? new Date(row.create_time) : null;
    const key =
      parsed && !Number.isNaN(parsed.getTime()) ? formatYmd(parsed) : "";
    let group = groups.get(key);
    if (!group) {
      group = { key, rows: [], units: 0 };
      groups.set(key, group);
    }
    group.rows.push(row);
    group.units += row.time_unit;
  }
  return [...groups.values()];
}

/**
 * Chronological units-per-day series for the mini chart. Calendar gaps are
 * filled for short spans so idle days stay visible; beyond that the bars would
 * be sub-pixel, so only days with bookings are plotted.
 */
function unitsPerDay(groups: DayGroup[]): { day: string; units: number }[] {
  const dated = groups.filter((g) => g.key !== "");
  if (dated.length === 0) return [];
  const byDay = new Map(dated.map((g) => [g.key, g.units]));
  const days = [...byDay.keys()].sort();
  const first = new Date(`${days[0]}T00:00:00`);
  const last = new Date(`${days[days.length - 1]}T00:00:00`);
  const span = Math.round((last.getTime() - first.getTime()) / 86_400_000) + 1;
  if (span > 62) {
    return days.map((day) => ({ day, units: byDay.get(day) ?? 0 }));
  }
  const out: { day: string; units: number }[] = [];
  for (let i = 0; i < span; i += 1) {
    const d = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
    const day = formatYmd(d);
    out.push({ day, units: byDay.get(day) ?? 0 });
  }
  return out;
}

// Matches the preset chips on the Reports page (muted outline / accent fill).
const chipCls =
  "inline-flex items-center rounded border border-hairline px-2 py-0.5 text-xs text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const chipActiveCls = "border-accent/50 bg-accent-dim text-accent hover:text-accent";
const dateInputCls =
  "rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

/**
 * Agent time-accounting report: list booked time units with filters for
 * user, date range, and ticket — backed by GET /api/v1/tickets/time-accounting.
 */
export function TimeAccountingReportPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const navigate = useNavigate({ from: "/agent/time-accounting" });
  const search = useSearch({ from: "/agent/time-accounting" }) as TimeAccountingSearch;
  const offset = search.offset ?? 0;
  const limit = 100;

  const setSearch = (patch: Partial<TimeAccountingSearch>) => {
    void navigate({
      search: (prev) => {
        const next = { ...(prev as TimeAccountingSearch), ...patch };
        if (patch.offset === undefined && Object.keys(patch).some((k) => k !== "offset")) {
          next.offset = 0;
        }
        if (!next.create_by) delete next.create_by;
        if (!next.ticket_id) delete next.ticket_id;
        if (!next.created_from) delete next.created_from;
        if (!next.created_to) delete next.created_to;
        if (!next.offset) delete next.offset;
        return next;
      },
      replace: true,
    });
  };

  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
  });

  const reportQ = useQuery({
    queryKey: [
      "time-accounting-report",
      {
        create_by: search.create_by,
        ticket_id: search.ticket_id,
        created_from: search.created_from,
        created_to: search.created_to,
        offset,
        limit,
      },
    ],
    queryFn: () =>
      api.listTimeAccountingReport({
        create_by: search.create_by,
        ticket_id: search.ticket_id,
        created_from: search.created_from
          ? `${search.created_from}T00:00:00`
          : undefined,
        created_to: search.created_to ? `${search.created_to}T23:59:59` : undefined,
        offset,
        limit,
      }),
  });

  const agentItems: SelectMenuItem<number>[] = useMemo(() => {
    const base = (agentsQ.data ?? []).map((a) => ({
      value: a.id,
      label: a.full_name,
      hint: a.login,
    }));
    // Sentinel 0 clears the user filter via SelectField (no native clear).
    return [{ value: 0, label: t("timeAccounting.allUsers") }, ...base];
  }, [agentsQ.data, t]);

  // Memoised so the derived groups/series below keep a stable identity.
  const items = useMemo(() => reportQ.data?.items ?? [], [reportQ.data]);
  const totalUnits = reportQ.data?.total_units ?? 0;

  const activePreset = presetForRange(search.created_from, search.created_to);
  const hasFilters = Boolean(
    search.create_by || search.ticket_id || search.created_from || search.created_to,
  );

  const timeFormatter = useMemo(
    () => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }),
    [locale],
  );

  const groups = useMemo(() => groupByDay(items), [items]);
  const series = useMemo(() => unitsPerDay(groups), [groups]);
  const maxUnits = series.reduce((max, p) => Math.max(max, p.units), 0);

  const ticketCount = new Set(items.map((r) => r.ticket_id)).size;
  const agentCount = new Set(items.map((r) => r.create_by)).size;

  /** Human-readable label for the active period, or null when unbounded. */
  const rangeLabel = (() => {
    const { created_from: from, created_to: to } = search;
    if (from && to) {
      return t("timeAccounting.rangeBetween", {
        from: formatDateOnly(from, locale),
        to: formatDateOnly(to, locale),
      });
    }
    if (from) return t("timeAccounting.rangeFrom", { from: formatDateOnly(from, locale) });
    if (to) return t("timeAccounting.rangeTo", { to: formatDateOnly(to, locale) });
    return null;
  })();

  const applyPreset = (preset: TimeRangePreset) => {
    const range = rangeForPreset(preset);
    setSearch({ created_from: range.from, created_to: range.to });
  };

  const clearFilters = () =>
    setSearch({
      create_by: undefined,
      ticket_id: undefined,
      created_from: undefined,
      created_to: undefined,
      offset: 0,
    });

  const rangeStart = items.length > 0 ? offset + 1 : 0;
  const rangeEnd = offset + items.length;

  // `total_units` is summed server-side over every matching booking, while the
  // count tiles can only see the page that was fetched. Say so, but only once
  // there is actually more than one page — otherwise the hint is noise.
  const paged = offset > 0 || items.length >= limit;
  const pageScopeHint = paged ? t("timeAccounting.pageScope") : undefined;

  return (
    <div
      className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6"
      data-testid="time-accounting-report"
    >
      <div>
        <h1 className="font-display text-xl font-semibold tracking-tight text-ink">
          {t("timeAccounting.title")}
        </h1>
        <p className="mt-0.5 text-[12.5px] text-muted">{t("timeAccounting.hint")}</p>
      </div>

      {/* Filters: presets first, then the free-form fields they write into. */}
      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-3">
        <div
          className="flex flex-wrap items-center gap-1.5"
          role="group"
          aria-label={t("timeAccounting.presetsLabel")}
          data-testid="ta-presets"
        >
          {TIME_RANGE_PRESETS.map((preset) => {
            const active = activePreset === preset;
            return (
              <button
                key={preset}
                type="button"
                className={cn(chipCls, active && chipActiveCls)}
                aria-pressed={active}
                onClick={() => applyPreset(preset)}
                data-testid={`ta-preset-${preset}`}
              >
                {t(`timeAccounting.presets.${preset}`)}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-end gap-3 border-t border-hairline pt-3">
          <div className="min-w-[12rem]">
            <label className="mb-1 block text-xs font-medium text-muted">
              {t("timeAccounting.filterUser")}
            </label>
            <SelectField
              items={agentItems}
              value={search.create_by ?? 0}
              onChange={(id) => setSearch({ create_by: id === 0 ? undefined : id })}
              placeholder={t("timeAccounting.allUsers")}
              testId="ta-filter-user"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              {t("timeAccounting.filterFrom")}
            </label>
            <input
              type="date"
              data-testid="ta-filter-from"
              value={search.created_from ?? ""}
              onChange={(e) => setSearch({ created_from: e.target.value || undefined })}
              className={dateInputCls}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              {t("timeAccounting.filterTo")}
            </label>
            <input
              type="date"
              data-testid="ta-filter-to"
              value={search.created_to ?? ""}
              onChange={(e) => setSearch({ created_to: e.target.value || undefined })}
              className={dateInputCls}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              {t("timeAccounting.filterTicket")}
            </label>
            <input
              type="number"
              data-testid="ta-filter-ticket"
              placeholder="ID"
              value={search.ticket_id ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                setSearch({ ticket_id: v ? Number(v) : undefined });
              }}
              className={cn(dateInputCls, "w-28 font-mono")}
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            data-testid="ta-filter-clear"
            disabled={!hasFilters}
            onClick={clearFilters}
          >
            {t("search.filters.clear")}
          </Button>
        </div>
      </div>

      {/* Key figures for the rows currently loaded. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label={t("timeAccounting.kpiUnits")}
          value={totalUnits.toFixed(2)}
          hint={rangeLabel ?? undefined}
          testId="ta-total-units"
        />
        <StatTile
          label={t("timeAccounting.kpiEntries")}
          value={items.length}
          hint={pageScopeHint}
          testId="ta-kpi-entries"
        />
        <StatTile
          label={t("timeAccounting.kpiTickets")}
          value={ticketCount}
          hint={pageScopeHint}
          testId="ta-kpi-tickets"
        />
        <StatTile
          label={t("timeAccounting.kpiAgents")}
          value={agentCount}
          hint={pageScopeHint}
          testId="ta-kpi-agents"
        />
      </div>

      {series.length > 1 && (
        <section
          className="rounded-lg border border-hairline bg-surface p-3"
          data-testid="ta-chart"
        >
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("timeAccounting.chartTitle")}
            </h2>
            {/* The tallest bar is the only scale the reader gets — name it. */}
            <span className="font-mono text-[11px] tabular-nums text-muted">
              {t("timeAccounting.chartPeak", { units: maxUnits.toFixed(2) })}
            </span>
          </div>
          <div className="flex h-24 items-end gap-px overflow-x-auto border-b border-hairline">
            {series.map((point) => {
              const label = t("timeAccounting.chartBar", {
                date: formatDateOnly(`${point.day}T00:00:00`, locale),
                units: point.units.toFixed(2),
              });
              const pct = maxUnits > 0 ? (point.units / maxUnits) * 100 : 0;
              const empty = point.units === 0;
              return (
                <div
                  key={point.day}
                  role="img"
                  aria-label={label}
                  title={label}
                  className={cn(
                    "min-w-[3px] flex-1 rounded-t-sm transition-[height] duration-150 motion-reduce:transition-none",
                    // A day without bookings keeps a hairline stub so the gap
                    // reads as "nothing booked", not as missing data.
                    empty ? "bg-hairline" : "bg-accent/70",
                  )}
                  style={{ height: empty ? "2px" : `${Math.max(pct, 4)}%` }}
                />
              );
            })}
          </div>
        </section>
      )}

      {reportQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-hairline bg-surface px-4 py-10 text-center">
          <p className="text-sm font-medium text-ink">{t("timeAccounting.empty")}</p>
          <p className="mx-auto mt-1 max-w-md text-[12.5px] text-muted">
            {t("timeAccounting.emptyHint")}
          </p>
          {hasFilters && (
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              data-testid="ta-empty-clear"
              onClick={clearFilters}
            >
              {t("timeAccounting.clearFilters")}
            </Button>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full min-w-[36rem] text-left text-sm" data-testid="ta-table">
            <thead className="sticky top-0 z-10 bg-surface-subtle text-xs uppercase tracking-wide text-muted">
              <tr>
                <th scope="col" className="w-20 px-3 py-2 font-semibold">
                  {t("timeAccounting.colWhen")}
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  {t("timeAccounting.colTicket")}
                </th>
                <th scope="col" className="w-40 px-3 py-2 font-semibold">
                  {t("timeAccounting.colUser")}
                </th>
                <th scope="col" className="w-24 px-3 py-2 text-right font-semibold">
                  {t("timeAccounting.colUnits")}
                </th>
              </tr>
            </thead>
            {groups.map((group) => (
              <tbody key={group.key || "unknown"} className="divide-y divide-hairline">
                <tr className="bg-surface-subtle/50">
                  <th
                    scope="colgroup"
                    colSpan={3}
                    className="px-3 py-1.5 text-left text-xs font-semibold text-muted"
                  >
                    {group.key
                      ? formatDateOnly(`${group.key}T00:00:00`, locale)
                      : t("timeAccounting.dayUnknown")}
                  </th>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-muted">
                    {group.units.toFixed(2)}
                  </td>
                </tr>
                {group.rows.map((row) => (
                  <tr key={row.id} className="hover:bg-surface-subtle/60">
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs tabular-nums text-muted">
                      {row.create_time
                        ? timeFormatter.format(new Date(row.create_time))
                        : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex min-w-0 items-baseline gap-2">
                        <Link
                          to="/agent/tickets/$ticketId"
                          params={{ ticketId: String(row.ticket_id) }}
                          className="shrink-0 font-mono text-xs font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                        >
                          {row.ticket_tn || `#${row.ticket_id}`}
                        </Link>
                        {row.ticket_title && (
                          <span
                            className="min-w-0 truncate text-muted"
                            title={row.ticket_title}
                          >
                            {row.ticket_title}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="truncate px-3 py-2 font-mono text-xs text-muted">
                      {row.create_by_login || row.create_by}
                    </td>
                    <td className="px-3 py-2 text-right font-mono font-medium tabular-nums text-ink">
                      {row.time_unit.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <Button
          variant="secondary"
          size="sm"
          disabled={offset <= 0}
          data-testid="ta-page-prev"
          onClick={() => setSearch({ offset: Math.max(0, offset - limit) })}
        >
          {t("common.prev")}
        </Button>
        <span
          className="font-mono text-xs tabular-nums text-muted"
          data-testid="ta-page-range"
        >
          {t("timeAccounting.pageRange", { from: rangeStart, to: rangeEnd })}
        </span>
        <Button
          variant="secondary"
          size="sm"
          disabled={items.length < limit}
          data-testid="ta-page-next"
          onClick={() => setSearch({ offset: offset + limit })}
        >
          {t("common.next")}
        </Button>
      </div>
    </div>
  );
}
