import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChartLegend } from "./ChartLegend";
import { ChartTooltip } from "./ChartTooltip";
import { ChartFrame, EmptyChart, GridLines, YAxisLabels } from "./chartKit";
import { niceTicks } from "./chartMath";

export type LineSeries = {
  name: string;
  color: string;
  values: number[];
};

export type LineChartProps = {
  /** Shared x-axis labels (buckets), same length as each series' `values`. */
  labels: string[];
  series: LineSeries[];
  /** Fill the area under each line. */
  area?: boolean;
  height?: number;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
};

/**
 * Hand-rolled multi-series line chart (no charting dependency).
 *
 * Used for time-bucketed reports (ticket volume, backlog trend). One y axis
 * starting at zero, 2 px lines, a crosshair + tooltip per bucket and a legend
 * for two or more series.
 */
export function LineChart({
  labels,
  series,
  area = false,
  height = 160,
  emptyLabel,
  testId,
  formatValue = String,
}: LineChartProps) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<number | null>(null);
  const points = labels.length;
  if (points === 0 || series.every((s) => s.values.length === 0)) {
    return <EmptyChart height={height} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }

  const allValues = series.flatMap((s) => s.values);
  const dataMin = Math.min(0, ...allValues);
  const ticks = niceTicks(Math.max(1, ...allValues));
  const max = ticks[ticks.length - 1];
  const range = max - dataMin || 1;
  const chartHeight = height - 40;
  const yOf = (v: number) => chartHeight - 2 - ((v - dataMin) / range) * (chartHeight - 6);
  const stepX = points > 1 ? 100 / (points - 1) : 0;
  const xOf = (i: number) => (points > 1 ? i * stepX : 50);

  const linePoints = (values: number[]) => values.map((v, i) => `${xOf(i)},${yOf(v)}`).join(" ");
  const areaPath = (values: number[]) =>
    `M${xOf(0)},${yOf(0)}L${linePoints(values).replace(/ /g, "L")}L${xOf(values.length - 1)},${yOf(0)}Z`;

  return (
    <ChartFrame
      testId={testId}
      onLeave={() => setHover(null)}
      axis={<YAxisLabels ticks={ticks} yOf={yOf} format={formatValue} />}
      footer={
        <>
          <div className="ml-9 mt-2 flex items-center justify-between text-[10px] text-muted">
            <span>{labels[0]}</span>
            {labels.length > 1 && <span>{labels[labels.length - 1]}</span>}
          </div>
          {series.length >= 2 && (
            <ChartLegend
              testId={testId ? `${testId}-legend` : undefined}
              items={series.map((s) => ({ key: s.name, label: s.name, color: s.color }))}
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
        aria-label="line chart"
      >
        <GridLines ticks={ticks} yOf={yOf} />
        {area &&
          series.map((s) =>
            s.values.length > 0 ? (
              <path
                key={`a-${s.name}`}
                d={areaPath(s.values)}
                fill={s.color}
                fillOpacity={0.15}
                stroke="none"
                data-testid={testId ? `${testId}-area-${s.name}` : undefined}
              />
            ) : null,
          )}
        {series.map((s) => (
          <polyline
            key={s.name}
            points={linePoints(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            data-testid={testId ? `${testId}-series-${s.name}` : undefined}
          />
        ))}
        {hover !== null && (
          <line
            x1={xOf(hover)}
            x2={xOf(hover)}
            y1={0}
            y2={chartHeight}
            stroke="var(--color-muted)"
            strokeWidth={1}
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
            data-testid={testId ? `${testId}-crosshair` : undefined}
          />
        )}
        {labels.map((label, i) => {
          const w = points > 1 ? stepX : 100;
          const x = points > 1 ? Math.max(0, xOf(i) - w / 2) : 0;
          const edge = points > 1 && (i === 0 || i === points - 1);
          return (
            <rect
              key={`h-${i}`}
              x={x}
              y={0}
              width={edge ? w / 2 : w}
              height={chartHeight}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseMove={() => setHover(i)}
              data-testid={testId ? `${testId}-hit-${i}` : undefined}
            >
              <title>{label}</title>
            </rect>
          );
        })}
      </svg>
      {hover !== null &&
        series.map((s) =>
          s.values[hover] === undefined ? null : (
            <span
              key={`m-${s.name}`}
              className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface"
              style={{ left: `${xOf(hover)}%`, top: yOf(s.values[hover]), backgroundColor: s.color }}
              aria-hidden
            />
          ),
        )}
      {hover !== null && (
        <ChartTooltip
          testId={testId ? `${testId}-tooltip` : undefined}
          x={xOf(hover)}
          y={Math.min(...series.map((s) => (s.values[hover] === undefined ? chartHeight : yOf(s.values[hover])))) - 8}
          title={labels[hover]}
          rows={series.map((s) => ({
            label: s.name,
            value: s.values[hover] === undefined ? "—" : formatValue(s.values[hover]),
            color: s.color,
          }))}
        />
      )}
    </ChartFrame>
  );
}
