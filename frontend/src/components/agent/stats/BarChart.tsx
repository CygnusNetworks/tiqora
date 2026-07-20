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
};

/**
 * Minimal hand-rolled vertical bar chart (no charting dependency).
 *
 * Renders a fixed-height SVG with bars scaled to the max value in `data`
 * and a label/value pair beneath each bar. Designed for small report
 * datasets (queues/states/priorities/owners), not dense time series.
 */
export function BarChart({
  data,
  color = "var(--color-accent)",
  height = 160,
  emptyLabel = "No data",
  testId,
}: BarChartProps) {
  if (data.length === 0) {
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

  const max = Math.max(1, ...data.map((d) => d.value));
  const barWidth = 100 / data.length;
  const chartHeight = height - 36; // reserve space for labels

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
        aria-label="bar chart"
      >
        {data.map((d, i) => {
          const barHeight = (d.value / max) * (chartHeight - 4);
          const x = i * barWidth + barWidth * 0.15;
          const w = barWidth * 0.7;
          return (
            <g key={d.label}>
              <rect
                x={x}
                y={chartHeight - barHeight}
                width={w}
                height={barHeight}
                rx={0.6}
                fill={color}
                data-testid={testId ? `${testId}-bar-${i}` : undefined}
              >
                <title>{`${d.label}: ${d.value}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex text-[10px] text-muted">
        {data.map((d) => (
          <div
            key={d.label}
            className="flex-1 truncate px-0.5 text-center"
            title={`${d.label}: ${d.value}`}
          >
            <div className="truncate font-medium text-ink">{d.value}</div>
            <div className="truncate">{d.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
