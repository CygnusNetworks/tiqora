import type { ReactNode } from "react";

export function EmptyChart({ height, label, testId }: { height: number; label: string; testId?: string }) {
  return (
    <div
      className="flex items-center justify-center rounded-lg border border-hairline bg-surface text-xs text-muted"
      style={{ height }}
      data-testid={testId}
    >
      {label}
    </div>
  );
}

/** Recessive horizontal gridlines inside an SVG (viewBox width 100). */
export function GridLines({ ticks, yOf }: { ticks: number[]; yOf: (v: number) => number }) {
  return (
    <>
      {ticks.map((v) => (
        <line
          key={v}
          x1={0}
          x2={100}
          y1={yOf(v)}
          y2={yOf(v)}
          stroke="var(--color-hairline)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </>
  );
}

/** Mono tabular y-axis labels in a left gutter next to the plot. */
export function YAxisLabels({
  ticks,
  yOf,
  format,
}: {
  ticks: number[];
  yOf: (v: number) => number;
  format: (v: number) => string;
}) {
  return (
    <div className="pointer-events-none absolute left-0 top-0 w-8" aria-hidden>
      {ticks.map((v) => (
        <span
          key={v}
          className="absolute right-0 -translate-y-1/2 font-mono text-[10px] tabular-nums text-muted"
          style={{ top: yOf(v) }}
        >
          {format(v)}
        </span>
      ))}
    </div>
  );
}

/** Framed chart: card, y-axis gutter, plot area (relative, for the tooltip), footer. */
export function ChartFrame({
  testId,
  gutter = true,
  children,
  axis,
  footer,
  onLeave,
}: {
  testId?: string;
  gutter?: boolean;
  children: ReactNode;
  axis?: ReactNode;
  footer?: ReactNode;
  onLeave?: () => void;
}) {
  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-3"
      data-testid={testId}
      onMouseLeave={onLeave}
    >
      <div className="relative">
        {axis}
        <div className={gutter ? "relative ml-9" : "relative"}>{children}</div>
      </div>
      {footer}
    </div>
  );
}
