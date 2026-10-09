export type SparklineProps = {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
  testId?: string;
};

/** Axis-free 2 px trend line; decorative (aria-hidden), the tile carries the numbers. Empty input draws nothing. */
export function Sparkline({
  values,
  color = "var(--color-series-1)",
  width = 80,
  height = 24,
  testId,
}: SparklineProps) {
  const clean = values.filter((v) => Number.isFinite(v));
  const min = clean.length ? Math.min(...clean) : 0;
  const max = clean.length ? Math.max(...clean) : 0;
  const range = max - min || 1;
  const pad = 2;
  const pts = clean
    .map((v, i) => {
      const x = clean.length > 1 ? pad + (i * (width - 2 * pad)) / (clean.length - 1) : width / 2;
      const y = max === min ? height / 2 : height - pad - ((v - min) / range) * (height - 2 * pad);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      aria-hidden="true"
      focusable="false"
      data-testid={testId}
    >
      {clean.length > 0 && (
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      )}
    </svg>
  );
}
