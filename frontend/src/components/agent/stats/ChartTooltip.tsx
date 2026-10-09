export type TooltipRow = { label: string; value: string; color?: string };

export type ChartTooltipProps = {
  /** Horizontal anchor in percent of the (relative) container width. */
  x: number;
  /** Vertical anchor in px from the container top. */
  y: number;
  /** Horizontal anchor in px from the container left; wins over `x` (used where the plot scrolls). */
  xPx?: number;
  title: string;
  rows: TooltipRow[];
  testId?: string;
};

/** Hover tooltip: title plus rows with a colour swatch next to ink-coloured text. */
export function ChartTooltip({ x, y, xPx, title, rows, testId }: ChartTooltipProps) {
  return (
    <div
      role="tooltip"
      data-testid={testId}
      className="pointer-events-none absolute z-10 min-w-[8rem] -translate-x-1/2 -translate-y-full rounded-md border border-hairline bg-surface-elevated px-2.5 py-1.5 text-xs text-ink shadow-md"
      style={{ left: xPx != null ? `${xPx}px` : `${Math.min(92, Math.max(8, x))}%`, top: Math.max(0, y) }}
    >
      <p className="mb-1 truncate font-medium">{title}</p>
      {rows.map((r, i) => (
        <p key={`${r.label}-${i}`} className="flex items-center gap-1.5">
          {r.color && (
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-sm"
              style={{ backgroundColor: r.color }}
              aria-hidden
            />
          )}
          <span className="min-w-0 flex-1 truncate text-muted">{r.label}</span>
          <span className="font-mono tabular-nums">{r.value}</span>
        </p>
      ))}
    </div>
  );
}
