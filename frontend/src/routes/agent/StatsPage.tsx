import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { StatsDimension, StatsGranularity } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { StatTile } from "@/components/agent/stats/StatTile";
import { BarChart } from "@/components/agent/stats/BarChart";
import { LineChart } from "@/components/agent/stats/LineChart";
import { Tabs } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  DATE_RANGE_PRESETS,
  dateRangeForPreset,
  isPresetActive,
  type DateRangePreset,
} from "@/lib/dateRanges";

const GRANULARITIES: StatsGranularity[] = ["day", "week", "month"];
const DIMENSIONS: StatsDimension[] = ["queue", "state", "priority", "owner"];

function average(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function formatMinutes(minutes: number | null): string {
  if (minutes == null) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 60 * 24) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / (60 * 24)).toFixed(1)}d`;
}

export function StatsPage() {
  const { t } = useTranslation();
  const [queueId, setQueueId] = useState<number | undefined>(undefined);
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [granularity, setGranularity] = useState<StatsGranularity>("day");
  const [dimension, setDimension] = useState<StatsDimension>("queue");

  const filterParams = useMemo(
    () => ({
      queue_id: queueId,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }),
    [queueId, dateFrom, dateTo],
  );

  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const flatQueues = flattenQueues(queuesQ.data ?? []);
  const queueItems: SelectMenuItem<number>[] = flatQueues.map((q) => ({
    value: q.id,
    label: q.name,
  }));
  const granularityItems: SelectMenuItem<StatsGranularity>[] = GRANULARITIES.map((g) => ({
    value: g,
    label: t(`stats.filters.granularityOptions.${g}`),
  }));

  const volumeQ = useQuery({
    queryKey: ["stats", "volume", filterParams, granularity],
    queryFn: () => api.statsVolume({ ...filterParams, granularity }),
  });

  const backlogQ = useQuery({
    queryKey: ["stats", "backlog", filterParams, granularity],
    queryFn: () => api.statsBacklog({ ...filterParams, granularity }),
  });

  const openSnapshotQ = useQuery({
    queryKey: ["stats", "open-snapshot", filterParams, dimension],
    queryFn: () => api.statsOpenSnapshot({ ...filterParams, dimension }),
  });

  const slaQ = useQuery({
    queryKey: ["stats", "sla", filterParams],
    queryFn: () => api.statsSla(filterParams),
  });

  const workloadQ = useQuery({
    queryKey: ["stats", "agent-workload", filterParams],
    queryFn: () => api.statsAgentWorkload(filterParams),
  });

  const avgFirstResponse = average(slaQ.data?.first_response_minutes ?? []);
  const avgSolution = average(slaQ.data?.solution_minutes ?? []);

  function applyPreset(preset: DateRangePreset) {
    const range = dateRangeForPreset(preset);
    setDateFrom(range.from);
    setDateTo(range.to);
  }

  // Match Cc/Bcc toggle styling in ReplyDialog (muted outline / accent fill when active).
  const presetChipCls =
    "inline-flex items-center rounded border border-hairline px-2 py-0.5 text-xs text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
  const presetChipActiveCls = "border-accent/50 bg-accent-dim text-accent hover:text-accent";

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6" data-testid="stats-page">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">{t("stats.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("stats.subtitle")}</p>
      </div>

      {/* Filter bar */}
      <div className="space-y-2 rounded-lg border border-hairline bg-surface p-3">
        <div
          className="flex flex-wrap items-center gap-1.5"
          data-testid="stats-date-presets"
          role="group"
          aria-label={t("stats.filters.dateFrom")}
        >
          {DATE_RANGE_PRESETS.map((key) => {
            const active = isPresetActive(key, dateFrom, dateTo);
            return (
              <button
                key={key}
                type="button"
                className={cn(presetChipCls, active && presetChipActiveCls)}
                aria-pressed={active}
                onClick={() => applyPreset(key)}
                data-testid={`stats-preset-${key}`}
              >
                {t(`stats.presets.${key}`)}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("stats.filters.queue")}
            <SelectMenu
              items={queueItems}
              value={queueId}
              onSelect={setQueueId}
              placeholder={t("queue.allQueues")}
              panelTestId="stats-filter-queue-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="stats-filter-queue"
                  {...toggleProps}
                  className="flex min-w-[10rem] items-center justify-between gap-2 rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <span className="min-w-0 flex-1 truncate">
                    {queueItems.find((i) => i.value === queueId)?.label ?? t("queue.allQueues")}
                  </span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("stats.filters.dateFrom")}
            <input
              type="date"
              className="rounded-md border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              data-testid="stats-filter-date-from"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("stats.filters.dateTo")}
            <input
              type="date"
              className="rounded-md border border-hairline bg-bg px-2 py-1.5 text-sm text-ink"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              data-testid="stats-filter-date-to"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            {t("stats.filters.granularity")}
            <SelectMenu
              items={granularityItems}
              value={granularity}
              onSelect={setGranularity}
              panelTestId="stats-filter-granularity-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="stats-filter-granularity"
                  {...toggleProps}
                  className="flex min-w-[8rem] items-center justify-between gap-2 rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <span>{t(`stats.filters.granularityOptions.${granularity}`)}</span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </label>
        </div>
      </div>

      {/* Stat tiles */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile
          label={t("stats.tiles.total")}
          value={slaQ.data?.total ?? "—"}
          testId="stats-tile-total"
        />
        <StatTile
          label={t("stats.tiles.escalated")}
          value={slaQ.data?.escalated ?? "—"}
          tone="danger"
          testId="stats-tile-escalated"
        />
        <StatTile
          label={t("stats.tiles.firstResponseBreached")}
          value={slaQ.data?.first_response_breached ?? "—"}
          tone="warn"
          testId="stats-tile-fr-breached"
        />
        <StatTile
          label={t("stats.tiles.avgFirstResponse")}
          value={formatMinutes(avgFirstResponse)}
          testId="stats-tile-avg-fr"
        />
        <StatTile
          label={t("stats.tiles.avgSolution")}
          value={formatMinutes(avgSolution)}
          testId="stats-tile-avg-solution"
        />
      </div>

      {/* Volume + backlog charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("stats.sections.volume")}
          </h2>
          {volumeQ.isLoading ? (
            <Spinner />
          ) : (
            <LineChart
              testId="stats-chart-volume"
              labels={(volumeQ.data?.points ?? []).map((p) => p.bucket)}
              series={[
                {
                  name: t("stats.table.created"),
                  color: "var(--color-accent)",
                  values: (volumeQ.data?.points ?? []).map((p) => p.created),
                },
                {
                  name: t("stats.table.closed"),
                  color: "var(--color-green)",
                  values: (volumeQ.data?.points ?? []).map((p) => p.closed),
                },
              ]}
              emptyLabel={t("stats.empty")}
            />
          )}
        </section>
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {t("stats.sections.backlog")}
          </h2>
          {backlogQ.isLoading ? (
            <Spinner />
          ) : (
            <LineChart
              testId="stats-chart-backlog"
              labels={(backlogQ.data?.points ?? []).map((p) => p.bucket)}
              series={[
                {
                  name: t("stats.sections.backlog"),
                  color: "var(--color-purple)",
                  values: (backlogQ.data?.points ?? []).map((p) => p.open_count),
                },
              ]}
              emptyLabel={t("stats.empty")}
            />
          )}
        </section>
      </div>

      {/* Open snapshot */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            {t("stats.sections.openSnapshot")}
          </h2>
          <div className="flex items-center gap-2">
            <Tabs
              value={dimension}
              onChange={(id) => setDimension(id as StatsDimension)}
              items={DIMENSIONS.map((d) => ({ id: d, label: t(`stats.dimension.${d}`) }))}
            />
            <Button
              variant="secondary"
              size="sm"
              data-testid="stats-open-snapshot-export"
              onClick={() => {
                window.location.href = api.statsOpenSnapshotCsvUrl({ ...filterParams, dimension });
              }}
            >
              {t("stats.exportCsv")}
            </Button>
          </div>
        </div>
        {openSnapshotQ.isLoading ? (
          <Spinner />
        ) : (
          <BarChart
            testId="stats-chart-open-snapshot"
            data={(openSnapshotQ.data?.items ?? []).slice(0, 12).map((i) => ({
              label: i.label,
              value: i.count,
            }))}
            emptyLabel={t("stats.empty")}
          />
        )}
      </section>

      {/* Agent workload table */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            {t("stats.sections.agentWorkload")}
          </h2>
          <Button
            variant="secondary"
            size="sm"
            data-testid="stats-workload-export"
            onClick={() => {
              window.location.href = api.statsAgentWorkloadCsvUrl(filterParams);
            }}
          >
            {t("stats.exportCsv")}
          </Button>
        </div>
        <div className="overflow-hidden rounded-lg border border-hairline bg-surface">
          {workloadQ.isLoading ? (
            <div className="p-4">
              <Spinner />
            </div>
          ) : (workloadQ.data ?? []).length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted">{t("stats.empty")}</p>
          ) : (
            <table className="w-full text-sm" data-testid="stats-workload-table">
              <thead>
                <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-2 font-medium">{t("stats.table.login")}</th>
                  <th className="px-4 py-2 font-medium">{t("stats.table.name")}</th>
                  <th className="px-4 py-2 font-medium text-right">{t("stats.table.ownedOpen")}</th>
                  <th className="px-4 py-2 font-medium text-right">
                    {t("stats.table.closedInPeriod")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {(workloadQ.data ?? []).map((a) => (
                  <tr key={a.user_id}>
                    <td className="px-4 py-2 font-mono text-xs text-accent">{a.login}</td>
                    <td className="px-4 py-2">{a.name}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">{a.owned_open}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">
                      {a.closed_in_period}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
