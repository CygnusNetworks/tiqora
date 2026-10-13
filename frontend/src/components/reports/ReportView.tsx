import { useId, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { BarChart } from "@/components/agent/stats/BarChart";
import { DonutChart } from "@/components/agent/stats/DonutChart";
import { HeatmapChart } from "@/components/agent/stats/HeatmapChart";
import { HorizontalBarChart } from "@/components/agent/stats/HorizontalBarChart";
import { KpiTile } from "@/components/agent/stats/KpiTile";
import { LineChart } from "@/components/agent/stats/LineChart";
import { StackedBarChart } from "@/components/agent/stats/StackedBarChart";
import {
  CHANNEL_ORDER,
  EVENT_KIND_ORDER,
  OTHER_SERIES_KEY,
  SERIES_SLOTS,
  STATE_TYPE_ORDER,
  displaySlots,
  keyColor,
} from "@/components/agent/stats/chartColors";
import { cn } from "@/lib/cn";
import type { ReportCell, ReportColumn, ReportDefinition, ReportResult } from "@/lib/reportsApi";
import { PivotTable } from "./PivotTable";
import { ReportTable } from "./ReportTable";
import { usePluginReportTexts } from "./usePluginReportTexts";
import { ADDITIVE_AGGS, useMeasureAggs } from "./useMeasureAggs";
import { cellLabel, formatMeasure, toSeries } from "./reportFormat";

/** Enum dimensions keep their colour whatever the filter leaves over. */
const FIXED_ORDER: Record<string, readonly string[]> = {
  channel: CHANNEL_ORDER,
  state_type: STATE_TYPE_ORDER,
  kind: EVENT_KIND_ORDER,
};

/** Measures where a decrease is the improvement (breaches, reopenings, waiting times). */
function goodWhen(measureKey: string): "up" | "down" {
  return /breach|reopen|pingpong|^first_response_|^solution_|^dwell_|^age_/.test(measureKey) ? "down" : "up";
}

type ColouredSeries = { key: string; label: string; color: string; values: (number | null)[] };

/**
 * Chart series with stable colours: enum values and measures by their fixed
 * order, ids by key. More than six series fold into one muted "Weitere".
 */
function colouredSeries(
  result: ReportResult,
  definition: ReportDefinition,
  t: ReturnType<typeof useTranslation>["t"],
): { categories: { key: string; label: string }[]; series: ColouredSeries[] } {
  const viz = definition.visualization;
  const { categories, series } = toSeries(result, viz, t);
  const dims = result.columns.filter((c) => c.role === "dimension");
  const seriesDim = viz?.series_dimension != null ? dims[viz.series_dimension] : undefined;
  const fixed = seriesDim ? FIXED_ORDER[seriesDim.key] : series.map((s) => s.key);
  const slots = displaySlots(
    series.map((s) => s.key),
    fixed,
  );
  const kept: ColouredSeries[] = [];
  let other: ColouredSeries | null = null;
  for (const s of series) {
    const slot = slots.get(s.key);
    if (s.key !== OTHER_SERIES_KEY && slot !== undefined && slot < SERIES_SLOTS) {
      kept.push({ ...s, color: keyColor(s.key, slots) });
      continue;
    }
    other ??= { key: OTHER_SERIES_KEY, label: t("reports.other"), color: "var(--color-muted)", values: categories.map(() => null) };
    s.values.forEach((v, i) => {
      if (v != null) other!.values[i] = (other!.values[i] ?? 0) + v;
    });
  }
  kept.sort((a, b) => (slots.get(a.key) ?? 0) - (slots.get(b.key) ?? 0));
  return { categories, series: other ? [...kept, other] : kept };
}

function plottedMeasure(result: ReportResult, definition: ReportDefinition): ReportColumn | undefined {
  const measures = result.columns.filter((c) => c.role === "measure");
  const first = definition.visualization?.chart_measures?.[0];
  return (first != null ? measures[first] : undefined) ?? measures[0];
}

function KpiRow({
  result,
  testId,
  sparklines,
}: {
  result: ReportResult;
  testId: string;
  sparklines?: Record<string, number[]>;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  const row = result.rows[0] ?? [];
  const previous = result.previous?.grand ?? null;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid={`${testId}-kpis`}>
      {result.columns.map((col, ci) => {
        if (col.role !== "measure") return null;
        const value = row[ci] ?? null;
        const prev = previous?.[ci] ?? null;
        let delta;
        // Rates change in percentage points; a relative change of a rate misleads.
        const asPoints = col.type === "percent";
        if (typeof value === "number" && typeof prev === "number" && (asPoints || prev !== 0)) {
          const change = asPoints ? value - prev : (value - prev) / Math.abs(prev);
          delta = {
            value: change,
            unit: asPoints ? "points" : "relative",
            direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
            goodWhen: goodWhen(col.key),
          } as const;
        }
        return (
          <KpiTile
            key={col.key}
            label={t(col.label_key, { defaultValue: col.key })}
            value={formatMeasure(value, col.type, lang)}
            delta={delta}
            hint={delta ? t("reports.previousPeriodValue", { value: formatMeasure(prev, col.type, lang) }) : undefined}
            sparkline={sparklines?.[col.key]}
            testId={`${testId}-kpi-${col.key}`}
          />
        );
      })}
    </div>
  );
}

/**
 * One report result, drawn as its definition asks: line/area, bar, stacked
 * bar, donut, heatmap, KPI tiles, cross table or plain table. Every chart has
 * a "Tabelle anzeigen" button below it: the exact numbers, and the keyboard
 * route for the dense charts whose marks are not tab stops.
 */
const NO_REPORTS: readonly never[] = [];

export function ReportView({
  report,
  result,
  testId = "report-view",
  compact = false,
  kpiSparklines,
  onSelectPoint,
  drillableDimensions,
}: {
  report: { key: string | null; definition: ReportDefinition };
  result: ReportResult;
  testId?: string;
  compact?: boolean;
  /** Small trend lines under the KPI tiles, by measure key. */
  kpiSparklines?: Record<string, number[]>;
  /**
   * Additive: chart marks (bars, segments, slices, cells) become buttons that report the clicked
   * point as dimension key -> raw value. Only where every dimension the mark stands for is in
   * `drillableDimensions`; without both props the charts render as before.
   */
  onSelectPoint?: (point: Record<string, ReportCell>) => void;
  drillableDimensions?: readonly string[];
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "de";
  // Column labels of a plugin source: load the plugin's texts and re-render once they are in.
  usePluginReportTexts(NO_REPORTS, useMemo(() => result.columns.map((c) => c.label_key), [result]));
  const [showTable, setShowTable] = useState(false);
  const regionId = useId();
  const { definition } = report;
  const aggs = useMeasureAggs(definition.source);
  const type = definition.visualization?.type ?? "table";
  const height = compact ? 200 : 280;
  const dims = result.columns.filter((c) => c.role === "dimension");
  const measures = result.columns.filter((c) => c.role === "measure");
  const stock = definition.source === "daily_snapshot";

  const truncated = result.truncated ? (
    <p className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-xs text-muted" data-testid={`${testId}-truncated`}>
      {t("reports.truncated")}
    </p>
  ) : null;

  if (result.rows.length === 0) {
    return (
      <div className="space-y-2" data-testid={testId}>
        {truncated}
        <p
          className="rounded-lg border border-dashed border-hairline px-4 py-8 text-center text-sm text-muted"
          data-testid={`${testId}-empty`}
        >
          {t("reports.empty")}
        </p>
      </div>
    );
  }

  if (type === "kpi") {
    return (
      <div className="space-y-2" data-testid={testId}>
        {truncated}
        <KpiRow result={result} testId={testId} sparklines={kpiSparklines} />
      </div>
    );
  }

  const measure = plottedMeasure(result, definition);
  const fmt = (v: number) => formatMeasure(v, measure?.type ?? "number", lang);
  const measureIdx = measure ? result.columns.indexOf(measure) : -1;

  /** Cross tables per measure, or the flat table. */
  const tableView = (id: string): ReactNode => {
    if (dims.length === 2 && type !== "table") {
      const shown = type === "pivot" ? measures : measure ? [measure] : [];
      return (
        <div className="space-y-3">
          {shown.map((m) => (
            <div key={m.key} className="space-y-1">
              {shown.length > 1 && (
                <h3 className="text-xs font-medium text-muted">{t(m.label_key, { defaultValue: m.key })}</h3>
              )}
              <PivotTable
                result={result}
                measureIndex={result.columns.indexOf(m)}
                summable={ADDITIVE_AGGS.has(aggs?.get(m.key) ?? "")}
                stock={stock}
                testId={shown.length > 1 ? `${id}-${m.key}` : id}
              />
            </div>
          ))}
        </div>
      );
    }
    return <ReportTable result={result} testId={`${testId}-rows`} />;
  };

  if (type === "pivot" || type === "table") {
    return (
      <div className="space-y-2" data-testid={testId}>
        {truncated}
        <div data-testid={`${testId}-table`}>{tableView(`${testId}-pivot`)}</div>
      </div>
    );
  }

  /** The raw cell value behind a chart key (keys are `String(cell)`; ids stay numbers, gaps stay null). */
  const rawValue = (col: ReportColumn, key: string): ReportCell => {
    const at = result.columns.indexOf(col);
    const hit = result.rows.find((row) => String(row[at]) === key);
    return hit ? hit[at] : key;
  };
  /** The point callback for marks standing for `cols`, or undefined when any of them is not drillable. */
  const selectable = (cols: (ReportColumn | undefined)[]) =>
    onSelectPoint && drillableDimensions && cols.length > 0 && cols.every((c) => c && drillableDimensions.includes(c.key))
      ? onSelectPoint
      : undefined;

  let chart: ReactNode;
  if (type === "heatmap" && dims.length >= 2 && measureIdx >= 0) {
    const [r, c] = [result.columns.indexOf(dims[0]), result.columns.indexOf(dims[1])];
    const order = (at: number) => {
      const keys = [...new Set(result.rows.map((row) => String(row[at])))];
      return keys.sort((a, b) => Number(a) - Number(b) || (a < b ? -1 : a > b ? 1 : 0));
    };
    const rowKeys = order(r);
    const colKeys = order(c);
    const cells = new Map(result.rows.map((row) => [`${String(row[r])}|${String(row[c])}`, row[measureIdx]]));
    const label = (col: ReportColumn, k: string) => cellLabel(result, col.key, k, t, lang);
    const pick = selectable([dims[0], dims[1]]);
    chart = (
      <HeatmapChart
        onSelect={
          pick && ((cell) => pick({ [dims[0].key]: rawValue(dims[0], cell.row), [dims[1].key]: rawValue(dims[1], cell.column) }))
        }
        testId={`${testId}-heatmap`}
        rows={rowKeys.map((k) => ({ key: k, label: label(dims[0], k) }))}
        columns={colKeys.map((k) => ({ key: k, label: label(dims[1], k) }))}
        values={rowKeys.map((rk) =>
          colKeys.map((ck) => {
            const v = cells.get(`${rk}|${ck}`);
            return typeof v === "number" ? v : null;
          }),
        )}
        formatValue={fmt}
      />
    );
  } else {
    const { categories, series } = colouredSeries(result, definition, t);
    const seriesIdx = definition.visualization?.series_dimension;
    const seriesCol = seriesIdx != null ? dims[seriesIdx] : undefined;
    const catCol = dims.find((c) => c !== seriesCol);
    const notOther = (key: string) => key !== OTHER_SERIES_KEY;
    const catPick = selectable([catCol]);
    const barPick =
      catPick && catCol ? (i: number) => catPick({ [catCol.key]: rawValue(catCol, categories[i].key) }) : undefined;
    if (type === "donut") {
      const values = series[0]?.values ?? [];
      const slots = displaySlots(
        categories.map((c) => c.key),
        dims[0] ? FIXED_ORDER[dims[0].key] : undefined,
      );
      const slices = categories.map((c, i) => ({
        key: c.key,
        label: c.label,
        value: values[i] ?? 0,
        color: (slots.get(c.key) ?? SERIES_SLOTS) < SERIES_SLOTS ? keyColor(c.key, slots) : "var(--color-muted)",
        selectable: notOther(c.key),
      }));
      const pick = selectable([catCol]);
      chart = (
        <DonutChart
          onSelect={pick && catCol && ((key) => pick({ [catCol.key]: rawValue(catCol, key) }))}
          testId={`${testId}-donut`}
          data={slices}
          centerLabel={measure ? t(measure.label_key, { defaultValue: measure.key }) : undefined}
          formatValue={fmt}
        />
      );
    } else if (type === "line" || type === "area") {
      chart = (
        <LineChart
          testId={`${testId}-line`}
          area={type === "area"}
          height={height}
          labels={categories.map((c) => c.label)}
          series={series.map((s) => ({ name: s.label, color: s.color, values: s.values }))}
          formatValue={fmt}
        />
      );
    } else if ((type === "bar" && series.length > 1) || type === "stacked_bar") {
      const pick = selectable(seriesCol ? [catCol, seriesCol] : [catCol]);
      chart = (
        <StackedBarChart
          testId={`${testId}-stacked`}
          height={height}
          categories={categories.map((c) => ({ ...c, selectable: notOther(c.key) }))}
          series={series.map((s) => ({ ...s, selectable: notOther(s.key) }))}
          formatValue={fmt}
          onSelect={
            pick &&
            catCol &&
            ((p) =>
              pick({
                [catCol.key]: rawValue(catCol, p.category),
                ...(seriesCol ? { [seriesCol.key]: rawValue(seriesCol, p.series) } : {}),
              }))
          }
        />
      );
    } else if (dims[0] != null && !["date", "hour", "weekday"].includes(dims[0].type)) {
      // Rankings (queues, customers, owners ...): horizontal bars keep long names and top-25 lists readable.
      chart = (
        <HorizontalBarChart
          testId={`${testId}-bar`}
          color="var(--color-series-1)"
          data={categories.map((c, i) => ({ label: c.label, value: series[0]?.values[i] ?? null, selectable: notOther(c.key) }))}
          formatValue={fmt}
          onSelect={barPick}
        />
      );
    } else {
      chart = (
        <BarChart
          testId={`${testId}-bar`}
          height={height}
          color="var(--color-series-1)"
          data={categories.map((c, i) => ({ label: c.label, value: series[0]?.values[i] ?? null, selectable: notOther(c.key) }))}
          formatValue={fmt}
          onSelect={barPick}
        />
      );
    }
  }

  return (
    <div className="space-y-2" data-testid={testId}>
      {truncated}
      <div id={regionId}>
        {showTable ? (
          <div data-testid={`${testId}-table`}>{tableView(`${testId}-pivot`)}</div>
        ) : (
          <div data-testid={`${testId}-chart`}>{chart}</div>
        )}
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          aria-pressed={showTable}
          aria-controls={regionId}
          onClick={() => setShowTable((v) => !v)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:bg-surface-subtle hover:text-ink",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          )}
          data-testid={`${testId}-toggle-table`}
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden>
            {showTable ? (
              <path d="M2 13V9m4 4V5m4 8V7m4 6V3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            ) : (
              <path d="M2 3.5h12M2 8h12M2 12.5h12M6 3.5v9" stroke="currentColor" strokeWidth="1.3" fill="none" />
            )}
          </svg>
          {t(showTable ? "reports.showChart" : "reports.showTable")}
        </button>
      </div>
    </div>
  );
}
