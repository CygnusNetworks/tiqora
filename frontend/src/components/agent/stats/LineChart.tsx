export type LineSeries = {
  name: string;
  color: string;
  values: number[];
};

export type LineChartProps = {
  /** Shared x-axis labels (buckets), same length as each series' `values`. */
  labels: string[];
  series: LineSeries[];
  height?: number;
  emptyLabel?: string;
  testId?: string;
};

/**
 * Minimal hand-rolled multi-series line chart (no charting dependency).
 *
 * Used for time-bucketed reports (ticket volume, backlog trend). Scales
 * each series against the combined min/max across all series so created
 * vs. closed (or backlog) stay visually comparable.
 */
export function LineChart({
  labels,
  series,
  height = 160,
  emptyLabel = "No data",
  testId,
}: LineChartProps) {
  const points = labels.length;
  if (points === 0 || series.every((s) => s.values.length === 0)) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-hairline bg-surface text-xs text-muted"
        style={{ height }}
        data-testid={testId}
      >
        {emptyLabel}
      </div>
    );
  }

  const allValues = series.flatMap((s) => s.values);
  const max = Math.max(1, ...allValues);
  const min = Math.min(0, ...allValues);
  const range = max - min || 1;
  const chartHeight = height - 40;
  const stepX = points > 1 ? 100 / (points - 1) : 0;

  const toPoints = (values: number[]) =>
    values
      .map((v, i) => {
        const x = points > 1 ? i * stepX : 50;
        const y = chartHeight - ((v - min) / range) * (chartHeight - 4);
        return `${x},${y}`;
      })
      .join(" ");

  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-3"
      data-testid={testId}
    >
      <svg
        viewBox={`0 0 100 ${chartHeight}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: chartHeight }}
        role="img"
        aria-label="line chart"
      >
        <line
          x1={0}
          y1={chartHeight - 1}
          x2={100}
          y2={chartHeight - 1}
          stroke="var(--color-hairline)"
          strokeWidth={0.3}
        />
        {series.map((s) => (
          <polyline
            key={s.name}
            points={toPoints(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={1.2}
            vectorEffect="non-scaling-stroke"
            data-testid={testId ? `${testId}-series-${s.name}` : undefined}
          />
        ))}
      </svg>
      <div className="mt-2 flex items-center justify-between text-[10px] text-muted">
        <span>{labels[0]}</span>
        {labels.length > 1 && <span>{labels[labels.length - 1]}</span>}
      </div>
      <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
        {series.map((s) => (
          <span key={s.name} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: s.color }}
              aria-hidden
            />
            <span className="text-muted">{s.name}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
