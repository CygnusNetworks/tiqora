import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FactsBanner } from "@/components/reports/FactsBanner";
import { ReportFilterBar } from "@/components/reports/ReportFilterBar";
import { filterRange, rangeInvalid, toRunIn, type ReportFilterState } from "@/components/reports/reportFilter";
import { ReportsAccessGate } from "@/components/reports/ReportsAccessGate";
import { RunState } from "@/components/reports/RunState";
import { ReportView } from "@/components/reports/ReportView";
import { cellLabel, formatMeasure, OTHER_KEY, toSeries } from "@/components/reports/reportFormat";
import { useReportRun } from "@/components/reports/useReportRun";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { cn } from "@/lib/cn";
import { dateRangeForPreset } from "@/lib/dateRanges";
import {
  reportErrorMessage,
  reportsApi,
  type BuiltinReport,
  type Grain,
  type ReportResult,
  type ReportRunIn,
} from "@/lib/reportsApi";
import { searchFromFilter } from "./reportViewSearch";

export type ReportsSearch = { category?: string };

const REPORT_CATEGORIES = [
  "overview",
  "volume",
  "backlog",
  "sla",
  "quality",
  "agents",
  "time",
  "customers",
  "patterns",
] as const;

const KPIS = "builtin.ticket_kpis";
const VOLUME = "builtin.volume";
const BACKLOG = "builtin.backlog_trend";
const OPEN_NOW = "builtin.open_now";
const ACTIVITY = "builtin.agent_activity";
const OPEN_NOW_TABS = ["queue", "state", "priority", "owner"] as const;
type OpenNowTab = (typeof OPEN_NOW_TABS)[number];

/** Time buckets that keep the overview charts readable for the chosen range. */
function overviewGrain(state: ReportFilterState): Grain | null {
  const range = filterRange(state);
  if (!range) return null;
  const days = (Date.parse(range.to) - Date.parse(range.from)) / 86_400_000 + 1;
  if (days <= 45) return "day";
  if (days <= 200) return "week";
  return "month";
}

function initialFilter(): ReportFilterState {
  const range = dateRangeForPreset("last30Days");
  return { preset: "last30Days", from: range.from, to: range.to, grain: null, choice: null, queues: [] };
}

/** Runs one overview report once its definition is known (body kept stable by value). */
function useOverviewRun(
  report: BuiltinReport | undefined,
  make: (r: BuiltinReport) => ReportRunIn,
  blocked: boolean,
) {
  const raw = report && !blocked ? make(report) : null;
  const key = JSON.stringify(raw);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the body changes exactly when its JSON does
  const body = useMemo(() => raw, [key]);
  return useReportRun(body);
}

function OverviewCard({
  report,
  title,
  run,
  testId,
  search,
  actions,
  blocked = false,
  children,
}: {
  report: BuiltinReport | undefined;
  /** The chosen range is invalid: nothing runs. */
  blocked?: boolean;
  title?: string;
  run: ReturnType<typeof useReportRun>;
  testId: string;
  search: ReturnType<typeof searchFromFilter>;
  actions?: ReactNode;
  children?: (result: ReportResult) => ReactNode;
}) {
  const { t } = useTranslation();
  if (!report) return null;
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-lg border border-hairline bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">{title ?? t(report.title_key)}</h2>
          <Link
            to="/agent/reports/$reportKey"
            params={{ reportKey: report.key }}
            search={search}
            className="text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            data-testid={`${testId}-open`}
          >
            {t("reports.openReport")}
          </Link>
        </div>
        {actions}
      </div>
      {blocked ? (
        <p className="py-6 text-center text-sm text-muted">{t("reports.rangeInvalid")}</p>
      ) : run.error ? (
        <RunState run={run} testId={testId} />
      ) : run.result ? (
        children ? (
          children(run.result)
        ) : (
          <ReportView report={{ key: report.key, definition: report.definition }} result={run.result} testId={testId} compact />
        )
      ) : (
        <RunState run={run} testId={testId} />
      )}
    </section>
  );
}

type AgentRow = {
  key: string;
  name: string;
  /** `null` = not in that report's top list ("—"), never a made-up zero. */
  open: number | null;
  reply: number | null;
  note: number | null;
  close: number | null;
  /** The folded "Weitere" tail of one report; it only fills that report's columns. */
  tail?: "open" | "activity";
};

/**
 * Agents: own open tickets now (open_now by owner, top 15) next to replies, notes and closings in the
 * range (agent_activity, top 25). The two reports rank different people, so an agent missing from one
 * shows "—" there, and each report's "Weitere" tail stays a row of its own.
 */
function agentRows(owned: ReportResult | undefined, activity: ReportResult | undefined, t: ReturnType<typeof useTranslation>["t"]): AgentRow[] {
  const rows = new Map<string, AgentRow>();
  const row = (key: string, name: string, tail?: AgentRow["tail"]) => {
    const id = tail ? `${OTHER_KEY}:${tail}` : key;
    let r = rows.get(id);
    if (!r) {
      r = { key: id, name, open: null, reply: null, note: null, close: null, tail };
      rows.set(id, r);
    }
    return r;
  };
  if (owned) {
    const dim = owned.columns.findIndex((c) => c.role === "dimension");
    const m = owned.columns.findIndex((c) => c.role === "measure");
    for (const cells of owned.rows) {
      const key = String(cells[dim]);
      const v = cells[m];
      row(key, cellLabel(owned, owned.columns[dim].key, key, t), key === OTHER_KEY ? "open" : undefined).open =
        typeof v === "number" ? v : null;
    }
  }
  if (activity) {
    const [actor, kind] = activity.columns.flatMap((c, i) => (c.role === "dimension" ? [i] : []));
    const m = activity.columns.findIndex((c) => c.role === "measure");
    for (const cells of activity.rows) {
      const key = String(cells[actor]);
      const k = String(cells[kind]);
      const v = typeof cells[m] === "number" ? (cells[m] as number) : 0;
      const r = row(key, cellLabel(activity, activity.columns[actor].key, key, t), key === OTHER_KEY ? "activity" : undefined);
      // First sight in the activity list: the agent is ranked there, so a missing kind really is 0.
      r.reply ??= 0;
      r.note ??= 0;
      r.close ??= 0;
      if (k === "reply" || k === "note" || k === "close") r[k] = (r[k] ?? 0) + v;
    }
  }
  return [...rows.values()].sort((a, b) => {
    if (a.tail || b.tail) return a.tail ? (b.tail ? (a.tail === "open" ? -1 : 1) : 1) : -1;
    return (b.open ?? 0) - (a.open ?? 0) || (b.reply ?? 0) - (a.reply ?? 0) || a.name.localeCompare(b.name);
  });
}

/** `/agent/reports`: the overview (key figures, trends, open tickets, agents) and the report catalogue. */
function ReportsPageContent() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  const search = useSearch({ from: "/agent/reports" }) as ReportsSearch;
  const [filter, setFilter] = useState<ReportFilterState>(initialFilter);
  const [openTab, setOpenTab] = useState<OpenNowTab>("queue");

  const catalogueQ = useQuery({
    queryKey: ["reports", "catalogue"],
    queryFn: ({ signal }) => reportsApi.catalogue(signal),
  });
  const reports = useMemo(() => catalogueQ.data?.reports ?? [], [catalogueQ.data]);
  const byKey = useMemo(() => new Map(reports.map((r) => [r.key, r])), [reports]);
  const blocked = rangeInvalid(filter);
  const grain = overviewGrain(filter);
  const viewSearch = searchFromFilter(filter);
  // "Bericht öffnen" on a trend chart opens it with the buckets the overview shows.
  const trendSearch = { ...viewSearch, grain: grain ?? undefined };

  const kpis = useOverviewRun(byKey.get(KPIS), (r) => ({ ...toRunIn(r.key, filter, r.definition), compare_previous: true }), blocked);
  const volume = useOverviewRun(byKey.get(VOLUME), (r) => toRunIn(r.key, { ...filter, grain }, r.definition), blocked);
  const backlog = useOverviewRun(byKey.get(BACKLOG), (r) => toRunIn(r.key, { ...filter, grain }, r.definition), blocked);
  const openNow = useOverviewRun(
    byKey.get(OPEN_NOW),
    (r) => toRunIn(r.key, { ...filter, choice: openTab === "queue" ? null : openTab }, r.definition),
    // "open now" ignores the date range, so a wrong range does not stop it
    false,
  );
  const owned = useOverviewRun(byKey.get(OPEN_NOW), (r) => toRunIn(r.key, { ...filter, choice: "owner" }, r.definition), false);
  const activity = useOverviewRun(byKey.get(ACTIVITY), (r) => toRunIn(r.key, filter, r.definition), blocked);

  const sparklines = useMemo(() => {
    const def = byKey.get(VOLUME)?.definition;
    if (!volume.result || !def) return undefined;
    const created = toSeries(volume.result, def.visualization, t).series.find((s) => s.key === "create");
    return created ? { tickets: created.values.map((v) => v ?? 0) } : undefined;
  }, [volume.result, byKey, t]);

  const agents = agentRows(owned.result, activity.result, t);

  const groupRefs = useRef(new Map<string, HTMLElement>());
  useEffect(() => {
    if (!search.category || reports.length === 0) return;
    groupRefs.current.get(search.category)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [search.category, reports.length]);

  const kpiReport = byKey.get(KPIS);
  const filterDefinition = kpiReport?.definition ?? reports[0]?.definition;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6" data-testid="reports-page">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">{t("reports.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("reports.subtitle")}</p>
      </div>

      <FactsBanner facts={catalogueQ.data?.facts} />

      {catalogueQ.isPending ? (
        <Spinner />
      ) : catalogueQ.error ? (
        <p className="text-sm text-danger" role="alert" data-testid="reports-error">
          {reportErrorMessage(catalogueQ.error, t("reports.error"))}
        </p>
      ) : (
        <>
          <section className="space-y-4" aria-labelledby="reports-overview-heading">
            <h2 id="reports-overview-heading" className="sr-only">
              {t("reports.overview")}
            </h2>
            {filterDefinition && (
              <ReportFilterBar definition={filterDefinition} value={filter} onChange={setFilter} testId="reports-filter" />
            )}

            {kpiReport && (
              <div>
                {blocked ? null : kpis.result ? (
                  <ReportView
                    report={{ key: KPIS, definition: kpiReport.definition }}
                    result={kpis.result}
                    testId="stats-kpis"
                    kpiSparklines={sparklines}
                  />
                ) : (
                  <div className="rounded-lg border border-hairline bg-surface p-3">
                    <RunState run={kpis} testId="stats-kpis" minHeight="min-h-[5rem]" />
                  </div>
                )}
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <OverviewCard report={byKey.get(VOLUME)} run={volume} testId="stats-chart-volume" search={trendSearch} blocked={blocked} />
              <OverviewCard report={byKey.get(BACKLOG)} run={backlog} testId="stats-chart-backlog" search={trendSearch} blocked={blocked} />
            </div>

            <OverviewCard
              report={byKey.get(OPEN_NOW)}
              run={openNow}
              testId="stats-chart-open-snapshot"
              search={{ choice: openTab === "queue" ? undefined : openTab, queues: viewSearch.queues }}
              actions={
                <Tabs
                  value={openTab}
                  onChange={(id) => setOpenTab(id as OpenNowTab)}
                  items={OPEN_NOW_TABS.map((d) => ({ id: d, label: t(`reports.dimensions.${d}`) }))}
                />
              }
            />

            <OverviewCard
              report={byKey.get(ACTIVITY)}
              title={t("reports.categories.agents")}
              run={owned.error ? owned : activity}
              testId="stats-workload"
              search={viewSearch}
              blocked={blocked}
            >
              {() =>
                agents.length === 0 ? (
                  <p className="py-6 text-center text-sm text-muted">{t("reports.empty")}</p>
                ) : (
                  <div className="max-h-[24rem] overflow-auto">
                    <table className="w-full border-separate border-spacing-0 text-sm" data-testid="stats-workload-table">
                      <thead>
                        <tr className="text-xs text-muted">
                          <th scope="col" className="sticky top-0 border-b border-hairline bg-surface px-3 py-2 text-left font-medium">
                            {t("reports.agentTable.agent")}
                          </th>
                          {(["open", "replies", "notes", "closed"] as const).map((k) => (
                            <th
                              key={k}
                              scope="col"
                              className="sticky top-0 border-b border-hairline bg-surface px-3 py-2 text-right font-medium"
                            >
                              {t(`reports.agentTable.${k}`)}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {agents.map((a) => (
                          <tr key={a.key} className="hover:bg-surface-subtle" data-testid={`stats-workload-row-${a.key}`}>
                            <th scope="row" className="border-b border-hairline px-3 py-1.5 text-left font-normal text-ink">
                              {a.name}
                            </th>
                            {[a.open, a.reply, a.note, a.close].map((v, i) => (
                              <td key={i} className="border-b border-hairline px-3 py-1.5 text-right font-mono tabular-nums text-ink">
                                {formatMeasure(v, "count", lang)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              }
            </OverviewCard>
          </section>

          <section className="space-y-5" aria-labelledby="reports-catalogue-heading" data-testid="reports-catalogue">
            <h2 id="reports-catalogue-heading" className="font-display text-lg font-semibold text-ink">
              {t("reports.allReports")}
            </h2>
            {REPORT_CATEGORIES.map((cat) => {
              const items = reports.filter((r) => r.category === cat);
              if (items.length === 0) return null;
              const highlighted = search.category === cat;
              return (
                <section
                  key={cat}
                  ref={(el) => {
                    if (el) groupRefs.current.set(cat, el);
                    else groupRefs.current.delete(cat);
                  }}
                  className={cn(
                    "scroll-mt-4 space-y-2 rounded-lg",
                    highlighted && "bg-accent-dim/40 p-3 ring-2 ring-accent/60",
                  )}
                  data-testid={`reports-category-${cat}`}
                  data-highlighted={highlighted ? "true" : undefined}
                  aria-labelledby={`reports-category-${cat}-heading`}
                >
                  <h3 id={`reports-category-${cat}-heading`} className="text-xs font-semibold uppercase tracking-wide text-muted">
                    {t(`reports.categories.${cat}`)}
                  </h3>
                  <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {items.map((r) => (
                      <li key={r.key} className="min-w-0">
                        <Link
                          to="/agent/reports/$reportKey"
                          params={{ reportKey: r.key }}
                          className="flex h-full flex-col gap-1 rounded-lg border border-hairline bg-surface p-3 transition-colors hover:border-accent/60 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                          data-testid={`reports-card-${r.key}`}
                          title={t(r.description_key)}
                        >
                          <span className="text-sm font-medium text-ink">{t(r.title_key)}</span>
                          <span className="line-clamp-3 text-xs text-muted">{t(r.description_key)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </section>
        </>
      )}
    </div>
  );
}

export function ReportsPage() {
  return (
    <ReportsAccessGate>
      <ReportsPageContent />
    </ReportsAccessGate>
  );
}
