import { useRef, useState } from "react";
import { ChartTooltip } from "./ChartTooltip";
import { EmptyChart } from "./chartKit";
import { markButtonProps } from "./markButton";
import { sequentialColor, valueTextColor } from "./chartColors";
import { truncate } from "./chartMath";
import { useChartFormat } from "./chartLocale";

export type HeatmapChartProps = {
  rows: { key: string; label: string }[];
  columns: { key: string; label: string }[];
  /** values[row][column]; `null` = no data (drawn as an empty cell). */
  values: (number | null)[][];
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
  /** Additive: cells with data become buttons (keyboard reachable) and report their row and column keys. */
  onSelect?: (cell: { row: string; column: string }) => void;
};

const CELL_W = 32;
const CELL_H = 24;
const LABEL_W = 92;
const HEADER_H = 22;

/** Rows x columns grid (cells are out of the tab order but keep role="img" + aria-label); colour = sequential ramp of value / max, empty cells use the subtle surface. */
export function HeatmapChart({
  rows,
  columns,
  values,
  emptyLabel,
  testId,
  formatValue,
  onSelect,
}: HeatmapChartProps) {
  const { t, number } = useChartFormat();
  const fmt = formatValue ?? number;
  const [hover, setHover] = useState<{ r: number; c: number; xPx: number; y: number } | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  /** Anchor the tooltip on the hovered cell's real box (the grid may be scrolled or scaled). */
  const hoverCell = (r: number, c: number, cell: Element) => {
    const frame = frameRef.current?.getBoundingClientRect();
    const box = cell.getBoundingClientRect();
    if (!frame) return setHover({ r, c, xPx: 0, y: 0 });
    const half = 64; // the tooltip is at least 8rem wide and centred on the anchor
    const mid = box.left + box.width / 2 - frame.left;
    const xPx = Math.min(Math.max(mid, half), Math.max(half, frame.width - half));
    setHover({ r, c, xPx, y: box.top - frame.top - 2 });
  };
  const numbers = values.flat().filter((v): v is number => v !== null && v !== undefined);
  if (rows.length === 0 || columns.length === 0 || numbers.length === 0) {
    return <EmptyChart height={120} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }

  const max = Math.max(...numbers);
  const width = LABEL_W + columns.length * CELL_W;
  const height = HEADER_H + rows.length * CELL_H;
  const showValues = columns.length <= 14;
  const labelEvery = Math.ceil(columns.length / 14);
  const hoverValue = hover ? values[hover.r]?.[hover.c] ?? null : null;

  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-3"
      data-testid={testId}
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative" ref={frameRef}>
        <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full"
          style={{ minWidth: width }}
          role="group"
          aria-label={`${rows.map((r) => r.label).join(", ")} × ${columns.map((c) => c.label).join(", ")}`}
        >
          {columns.map((col, c) =>
            c % labelEvery === 0 ? (
              <text
                key={col.key}
                x={LABEL_W + c * CELL_W + CELL_W / 2}
                y={14}
                textAnchor="middle"
                className="fill-muted text-[10px]"
              >
<title>{col.label}</title>
                {truncate(col.label, 6)}
              </text>
            ) : null,
          )}
          {rows.map((row, r) => (
            <g key={row.key}>
              <text x={LABEL_W - 8} y={HEADER_H + r * CELL_H + CELL_H / 2 + 3} textAnchor="end" className="fill-muted text-[10px]">
                <title>{row.label}</title>
                {truncate(row.label, 14)}
              </text>
              {columns.map((col, c) => {
                const v = values[r]?.[c] ?? null;
                const tone = v === null ? 0 : max > 0 ? v / max : 0;
                return (
                  <g key={col.key}>
                    <rect
                      x={LABEL_W + c * CELL_W}
                      y={HEADER_H + r * CELL_H}
                      width={CELL_W}
                      height={CELL_H}
                      fill={v === null ? "var(--color-surface-subtle)" : sequentialColor(tone)}
                      stroke="var(--color-surface)"
                      strokeWidth={2}
                      {...(onSelect && v !== null
                        ? markButtonProps(() => onSelect({ row: row.key, column: col.key }))
                        : { tabIndex: -1, role: "img" })}
                      aria-label={`${row.label} · ${col.label}: ${v === null ? "—" : fmt(v)}`}
                      onFocus={(e) => hoverCell(r, c, e.currentTarget)}
                      onBlur={() => setHover(null)}
                      onMouseEnter={(e) => hoverCell(r, c, e.currentTarget)}
                      onMouseMove={(e) => hoverCell(r, c, e.currentTarget)}
                      data-testid={testId ? `${testId}-cell-${r}-${c}` : undefined}
                    />
                    {showValues && v !== null && (
                      <text
                        x={LABEL_W + c * CELL_W + CELL_W / 2}
                        y={HEADER_H + r * CELL_H + CELL_H / 2 + 3}
                        textAnchor="middle"
                        className="pointer-events-none font-mono text-[10px]"
                        style={{ fill: valueTextColor(tone) }}
                      >
                        {fmt(v)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
        </div>
        {hover && (
          <ChartTooltip
            testId={testId ? `${testId}-tooltip` : undefined}
            x={0}
            xPx={hover.xPx}
            y={hover.y}
            title={`${rows[hover.r].label} · ${columns[hover.c].label}`}
            rows={[{ label: rows[hover.r].label, value: hoverValue === null ? "—" : fmt(hoverValue) }]}
          />
        )}
      </div>
      <div className="mt-2 flex items-center justify-end gap-2 text-[10px] text-muted" data-testid={testId ? `${testId}-legend` : undefined}>
        <span className="font-mono tabular-nums">{fmt(0)}</span>
        <span className="flex h-2 overflow-hidden rounded-sm" aria-hidden>
          {Array.from({ length: 7 }, (_, i) => (
            <span key={i} className="h-2 w-5" style={{ backgroundColor: sequentialColor((i + 0.5) / 7) }} />
          ))}
        </span>
        <span className="font-mono tabular-nums">{fmt(max)}</span>
      </div>
    </div>
  );
}
