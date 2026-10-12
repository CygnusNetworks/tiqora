import { useState } from "react";
import { useChartFormat } from "./chartLocale";
import { ChartLegend } from "./ChartLegend";
import { ChartTooltip } from "./ChartTooltip";
import { ChartFrame, EmptyChart, GridLines, YAxisLabels } from "./chartKit";
import { markButtonProps } from "./markButton";
import { barPath, niceTicks } from "./chartMath";

export type StackedSeries = {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
  /** With `onSelect`: false = this series is not clickable (e.g. the folded "other" bucket). */
  selectable?: boolean;
};

export type StackedBarChartProps = {
  categories: { key: string; label: string; selectable?: boolean }[];
  /** Stack order = array order (bottom to top), kept stable across categories. */
  series: StackedSeries[];
  height?: number;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
  /** Additive: every segment becomes a button (keyboard reachable) and reports its category and series. */
  onSelect?: (point: { category: string; series: string }) => void;
};

/**
 * Stacked vertical bars with a 2 px surface gap between neighbouring segments.
 * Negative and null values draw no segment (clamped to 0); the tooltip shows the given numbers.
 */
export function StackedBarChart({
  categories,
  series,
  height = 200,
  emptyLabel,
  testId,
  formatValue,
  onSelect,
}: StackedBarChartProps) {
  const { t, number } = useChartFormat();
  const fmt = formatValue ?? number;
  const [hover, setHover] = useState<number | null>(null);
  const totals = categories.map((_, i) => series.reduce((sum, s) => sum + Math.max(0, s.values[i] ?? 0), 0));
  if (categories.length === 0 || series.length === 0 || totals.every((v) => v === 0)) {
    return <EmptyChart height={height} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }

  const ticks = niceTicks(Math.max(1, ...totals));
  const max = ticks[ticks.length - 1];
  const band = 100 / categories.length;
  const chartHeight = height - 40;
  const scale = (chartHeight - 4) / max;
  const yOf = (v: number) => chartHeight - v * scale;
  const barW = band * 0.7;

  return (
    <ChartFrame
      testId={testId}
      onLeave={() => setHover(null)}
      axis={<YAxisLabels ticks={ticks} yOf={yOf} format={fmt} />}
      footer={
        <>
          <div className="ml-9 mt-2 flex text-[10px] text-muted">
            {categories.map((c) => (
              <div key={c.key} className="flex-1 truncate px-0.5 text-center" title={c.label}>
                {c.label}
              </div>
            ))}
          </div>
          {series.length >= 2 && (
            <ChartLegend
              testId={testId ? `${testId}-legend` : undefined}
              items={series.map((s) => ({ key: s.key, label: s.label, color: s.color }))}
            />
          )}
        </>
      }
    >
      <svg
        viewBox={`0 0 100 ${chartHeight}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: chartHeight }}
        role="group"
        aria-label={`${categories.map((c) => c.label).join(", ")}: ${series.map((s) => s.label).join(", ")}`}
      >
        <GridLines ticks={ticks} yOf={yOf} />
        {categories.map((c, ci) => {
          let acc = 0;
          const topIndex = series.reduce((last, s, si) => ((s.values[ci] ?? 0) > 0 ? si : last), -1);
          return series.map((s, si) => {
            const v = Math.max(0, s.values[ci] ?? 0);
            if (v === 0) return null;
            const h = v * scale;
            const y = yOf(acc + v);
            acc += v;
            return (
              <path
                key={`${c.key}-${s.key}`}
                d={barPath(ci * band + (band - barW) / 2, y, barW, h, si === topIndex)}
                fill={s.color}
                stroke="var(--color-surface)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
                data-testid={testId ? `${testId}-seg-${c.key}-${s.key}` : undefined}
              />
            );
          });
        })}
        {categories.map((c, ci) => (
          <rect
            key={`h-${c.key}`}
            x={ci * band}
            y={0}
            width={band}
            height={chartHeight}
            fill="transparent"
            tabIndex={categories.length > 12 ? -1 : 0}
            role="img"
            aria-label={`${c.label}: ${series
              .filter((s) => s.values[ci] != null)
              .map((s) => `${s.label} ${fmt(s.values[ci] as number)}`)
              .join(", ")}`}
            onFocus={() => setHover(ci)}
            onBlur={() => setHover(null)}
            onMouseEnter={() => setHover(ci)}
            onMouseMove={() => setHover(ci)}
            data-testid={testId ? `${testId}-hit-${ci}` : undefined}
          />
        ))}
        {onSelect &&
          categories.map((c, ci) => {
            if (c.selectable === false) return null;
            let acc = 0;
            return series.map((s) => {
              const v = Math.max(0, s.values[ci] ?? 0);
              if (v === 0) return null;
              const y = yOf(acc + v);
              acc += v;
              if (s.selectable === false) return null;
              return (
                <rect
                  key={`b-${c.key}-${s.key}`}
                  x={ci * band + (band - barW) / 2}
                  y={y}
                  width={barW}
                  height={v * scale}
                  fill="transparent"
                  {...markButtonProps(() => onSelect({ category: c.key, series: s.key }))}
                  aria-label={`${c.label} · ${s.label}: ${fmt(s.values[ci] as number)}`}
                  onFocus={() => setHover(ci)}
                  onBlur={() => setHover(null)}
                  onMouseEnter={() => setHover(ci)}
                  onMouseMove={() => setHover(ci)}
                  data-testid={testId ? `${testId}-select-${c.key}-${s.key}` : undefined}
                />
              );
            });
          })}
      </svg>
      {hover !== null && (
        <ChartTooltip
          testId={testId ? `${testId}-tooltip` : undefined}
          x={hover * band + band / 2}
          y={yOf(totals[hover]) - 4}
          title={categories[hover].label}
          rows={[
            ...series
              .filter((s) => s.values[hover] != null)
              .map((s) => ({ label: s.label, value: fmt(s.values[hover] as number), color: s.color })),
            { label: t("reports.chart.total"), value: fmt(totals[hover]) },
          ]}
        />
      )}
    </ChartFrame>
  );
}
