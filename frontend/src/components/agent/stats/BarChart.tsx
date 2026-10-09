import { useState } from "react";
import { useChartFormat } from "./chartLocale";
import { ChartTooltip } from "./ChartTooltip";
import { ChartFrame, EmptyChart, GridLines, YAxisLabels } from "./chartKit";
import { barPath, niceTicks } from "./chartMath";

export type BarDatum = {
  label: string;
  value: number;
};

export type BarChartProps = {
  data: BarDatum[];
  /** CSS color (var(--color-accent) etc.) — kept as a prop so callers can vary series color. */
  color?: string;
  height?: number;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
};

/**
 * Hand-rolled vertical bar chart (no charting dependency).
 *
 * Bars use at most 70 % of their band, only the value end is rounded, one
 * y axis with recessive gridlines, a tooltip per bar and a label/value pair
 * beneath each bar. Negative values are clamped to 0 (report measures are non-negative); the
 * tooltip and label still show the real number. Designed for small report datasets, not dense time series.
 */
export function BarChart({
  data,
  color = "var(--color-accent)",
  height = 160,
  emptyLabel,
  testId,
  formatValue,
}: BarChartProps) {
  const { t, number } = useChartFormat();
  const fmt = formatValue ?? number;
  const [hover, setHover] = useState<number | null>(null);
  if (data.length === 0) {
    return <EmptyChart height={height} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }

  const ticks = niceTicks(Math.max(1, ...data.map((d) => Math.max(0, d.value))));
  const max = ticks[ticks.length - 1];
  const barWidth = 100 / data.length;
  const chartHeight = height - 36; // reserve space for labels
  const yOf = (v: number) => chartHeight - (v / max) * (chartHeight - 4);

  return (
    <ChartFrame
      testId={testId}
      onLeave={() => setHover(null)}
      axis={<YAxisLabels ticks={ticks} yOf={yOf} format={fmt} />}
      footer={
        <div className="ml-9 mt-2 flex text-[10px] text-muted">
          {data.map((d, i) => (
            <div key={`${i}-${d.label}`} className="flex-1 truncate px-0.5 text-center" title={`${d.label}: ${d.value}`}>
              <div className="truncate font-medium text-ink">{fmt(d.value)}</div>
              <div className="truncate">{d.label}</div>
            </div>
          ))}
        </div>
      }
    >
      <svg
        viewBox={`0 0 100 ${chartHeight}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: chartHeight }}
        role="group"
        aria-label={data.map((d) => `${d.label}: ${fmt(d.value)}`).join(", ")}
      >
        <GridLines ticks={ticks} yOf={yOf} />
        {data.map((d, i) => {
          const h = (Math.max(0, d.value) / max) * (chartHeight - 4);
          return (
            <path
              key={`${i}-${d.label}`}
              d={barPath(i * barWidth + barWidth * 0.15, chartHeight - h, barWidth * 0.7, h, true)}
              fill={color}
              data-testid={testId ? `${testId}-bar-${i}` : undefined}
            />
          );
        })}
        {data.map((d, i) => (
          <rect
            key={`h-${i}`}
            x={i * barWidth}
            y={0}
            width={barWidth}
            height={chartHeight}
            fill="transparent"
            tabIndex={0}
            role="img"
            aria-label={`${d.label}: ${fmt(d.value)}`}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            onMouseEnter={() => setHover(i)}
            onMouseMove={() => setHover(i)}
            data-testid={testId ? `${testId}-hit-${i}` : undefined}
          />
        ))}
      </svg>
      {hover !== null && (
        <ChartTooltip
          testId={testId ? `${testId}-tooltip` : undefined}
          x={hover * barWidth + barWidth / 2}
          y={yOf(Math.max(0, data[hover].value)) - 4}
          title={data[hover].label}
          rows={[{ label: "", value: fmt(data[hover].value), color }]}
        />
      )}
    </ChartFrame>
  );
}
