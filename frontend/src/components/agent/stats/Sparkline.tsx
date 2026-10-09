export type SparklineProps = {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
  testId?: string;
};

/** Axis-free 2 px trend line. */
export function Sparkline({
  values,
  color = "var(--color-series-1)",
  width = 80,
  height = 24,
  testId,
}: SparklineProps) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 2;
  const pts = values
    .map((v, i) => {
      const x = values.length > 1 ? pad + (i * (width - 2 * pad)) / (values.length - 1) : width / 2;
      const y = max === min ? height / 2 : height - pad - ((v - min) / range) * (height - 2 * pad);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-hidden
      data-testid={testId}
    >
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
