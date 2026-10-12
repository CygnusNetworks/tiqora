import { useChartFormat } from "./chartLocale";
import { EmptyChart } from "./chartKit";
import { markButtonProps } from "./markButton";

export type HorizontalBarDatum = {
  label: string;
  /** `null` = no data: no bar, "—" as text. */
  value: number | null;
  /** With `onSelect`: false = this row is not a button (e.g. the folded "other" bucket). */
  selectable?: boolean;
};

export type HorizontalBarChartProps = {
  data: HorizontalBarDatum[];
  color?: string;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
  /** Additive: rows become buttons (keyboard reachable) and report their index. */
  onSelect?: (index: number) => void;
};

/**
 * Ranking bars: one row per entry with its label on the left (full text in a
 * title), the bar in proportion to the largest value and the number at the
 * end. Long labels and top-25 lists stay readable at phone width, which the
 * vertical bars cannot do. Rows are labelled images; above 12 rows they leave
 * the tab order (the table toggle is the keyboard route).
 */
export function HorizontalBarChart({
  data,
  color = "var(--color-series-1)",
  emptyLabel,
  testId,
  formatValue,
  onSelect,
}: HorizontalBarChartProps) {
  const { t, number } = useChartFormat();
  const fmt = formatValue ?? number;
  if (data.length === 0) {
    return <EmptyChart height={120} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }
  const max = Math.max(1, ...data.map((d) => Math.max(0, d.value ?? 0)));
  const text = (v: number | null) => (v === null ? "—" : fmt(v));

  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-3"
      role="group"
      aria-label={data.map((d) => `${d.label}: ${text(d.value)}`).join(", ")}
      data-testid={testId}
    >
      <div className="space-y-1">
        {data.map((d, i) => (
          <div
            key={`${i}-${d.label}`}
            {...(onSelect && d.selectable !== false
              ? markButtonProps(() => onSelect(i))
              : { tabIndex: data.length > 12 ? -1 : 0, role: "img" })}
            aria-label={`${d.label}: ${text(d.value)}`}
            className="grid grid-cols-[minmax(5rem,10rem)_1fr_auto] items-center gap-2 rounded text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            data-testid={testId ? `${testId}-row-${i}` : undefined}
          >
            <span className="truncate text-muted" title={d.label}>
              {d.label}
            </span>
            <span className="h-4 min-w-0" aria-hidden>
              {d.value !== null && (
                <span
                  className="block h-4 rounded-r-sm"
                  style={{ width: `${(Math.max(0, d.value) / max) * 100}%`, minWidth: d.value > 0 ? 2 : 0, backgroundColor: color }}
                  data-testid={testId ? `${testId}-bar-${i}` : undefined}
                />
              )}
            </span>
            <span className="min-w-[3ch] text-right font-mono tabular-nums text-ink">{text(d.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
