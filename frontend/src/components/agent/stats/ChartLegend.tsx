export type LegendItem = { key: string; label: string; color: string; detail?: string };

export type ChartLegendProps = {
  items: LegendItem[];
  testId?: string;
};

/** Legend with swatches; text stays ink/muted, never the series colour. */
export function ChartLegend({ items, testId }: ChartLegendProps) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]" data-testid={testId}>
      {items.map((it) => (
        <li key={it.key} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 shrink-0 rounded-sm"
            style={{ backgroundColor: it.color }}
            aria-hidden
          />
          <span className="text-muted">{it.label}</span>
          {it.detail && <span className="font-mono tabular-nums text-ink">{it.detail}</span>}
        </li>
      ))}
    </ul>
  );
}
