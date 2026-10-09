import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChartLegend } from "./ChartLegend";
import { ChartTooltip } from "./ChartTooltip";
import { ChartFrame, EmptyChart, GridLines, YAxisLabels } from "./chartKit";
import { barPath, niceTicks } from "./chartMath";

export type StackedSeries = {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
};

export type StackedBarChartProps = {
  categories: { key: string; label: string }[];
  /** Stack order = array order (bottom to top), kept stable across categories. */
  series: StackedSeries[];
  height?: number;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
};

/** Stacked vertical bars with a 2 px surface gap between neighbouring segments. */
export function StackedBarChart({
  categories,
  series,
  height = 200,
  emptyLabel,
  testId,
  formatValue = String,
}: StackedBarChartProps) {
  const { t } = useTranslation();
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
      axis={<YAxisLabels ticks={ticks} yOf={yOf} format={formatValue} />}
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
        role="img"
        aria-label="stacked bar chart"
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
            onMouseEnter={() => setHover(ci)}
            onMouseMove={() => setHover(ci)}
            data-testid={testId ? `${testId}-hit-${ci}` : undefined}
          />
        ))}
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
              .map((s) => ({ label: s.label, value: formatValue(s.values[hover] as number), color: s.color })),
            { label: "Σ", value: formatValue(totals[hover]) },
          ]}
        />
      )}
    </ChartFrame>
  );
}
