import { useState } from "react";
import { useChartFormat } from "./chartLocale";
import { ChartLegend } from "./ChartLegend";
import { ChartTooltip } from "./ChartTooltip";
import { EmptyChart } from "./chartKit";

export type DonutDatum = { key: string; label: string; value: number; color: string };

export type DonutChartProps = {
  data: DonutDatum[];
  /** Small caption under the total in the centre. */
  centerLabel?: string;
  size?: number;
  emptyLabel?: string;
  testId?: string;
  formatValue?: (v: number) => string;
};

function arcPath(cx: number, cy: number, rOut: number, rIn: number, a0: number, a1: number): string {
  const pt = (r: number, a: number) => `${cx + r * Math.sin(a)},${cy - r * Math.cos(a)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return (
    `M${pt(rOut, a0)}A${rOut},${rOut} 0 ${large} 1 ${pt(rOut, a1)}` +
    `L${pt(rIn, a1)}A${rIn},${rIn} 0 ${large} 0 ${pt(rIn, a0)}Z`
  );
}

/** Donut with 2 px surface gaps between arcs, the total in the centre and a legend with values and shares. */
export function DonutChart({
  data,
  centerLabel,
  size = 160,
  emptyLabel,
  testId,
  formatValue,
}: DonutChartProps) {
  const { t, number, percent } = useChartFormat();
  const fmt = formatValue ?? number;
  const [hover, setHover] = useState<string | null>(null);
  const slices = data.filter((d) => d.value > 0);
  const total = slices.reduce((s, d) => s + d.value, 0);
  if (total === 0) {
    return <EmptyChart height={size} label={emptyLabel ?? t("reports.chart.noData")} testId={testId} />;
  }

  const c = size / 2;
  const rOut = c - 2;
  const rIn = rOut * 0.62;
  const percentOf = (v: number) => percent(v / total);
  let angle = 0;
  const arcs = slices.map((d) => {
    const a0 = angle;
    // A single full-circle slice cannot be one SVG arc; stop just short of 2*PI.
    const a1 = angle + Math.min((d.value / total) * 2 * Math.PI, 2 * Math.PI - 0.0001);
    angle += (d.value / total) * 2 * Math.PI;
    return { d, a0, a1 };
  });
  const active = arcs.find((a) => a.d.key === hover);
  const mid = active ? (active.a0 + active.a1) / 2 : 0;

  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-3"
      data-testid={testId}
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative mx-auto" style={{ width: size, height: size }}>
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="group"
          aria-label={slices.map((d) => `${d.label}: ${fmt(d.value)}`).join(", ")}
        >
          {arcs.map(({ d, a0, a1 }) => (
            <path
              key={d.key}
              d={arcPath(c, c, rOut, rIn, a0, a1)}
              fill={d.color}
              stroke="var(--color-surface)"
              strokeWidth={2}
              tabIndex={0}
              role="img"
              aria-label={`${d.label}: ${fmt(d.value)} (${percentOf(d.value)})`}
              onFocus={() => setHover(d.key)}
              onBlur={() => setHover(null)}
              onMouseEnter={() => setHover(d.key)}
              onMouseMove={() => setHover(d.key)}
              data-testid={testId ? `${testId}-slice-${d.key}` : undefined}
            />
          ))}
          <text
            x={c}
            y={centerLabel ? c : c + 5}
            textAnchor="middle"
            className="fill-ink font-mono text-base font-semibold"
            data-testid={testId ? `${testId}-center` : undefined}
          >
            {fmt(total)}
          </text>
          {centerLabel && (
            <text x={c} y={c + 14} textAnchor="middle" className="fill-muted text-[10px]">
              {centerLabel}
            </text>
          )}
        </svg>
        {active && (
          <ChartTooltip
            testId={testId ? `${testId}-tooltip` : undefined}
            x={((c + (rOut + rIn) / 2 * Math.sin(mid)) / size) * 100}
            y={c - ((rOut + rIn) / 2) * Math.cos(mid)}
            title={active.d.label}
            rows={[
              { label: t("reports.chart.ofTotal", { percent: percentOf(active.d.value) }), value: fmt(active.d.value), color: active.d.color },
            ]}
          />
        )}
      </div>
      <ChartLegend
        testId={testId ? `${testId}-legend` : undefined}
        items={slices.map((d) => ({
          key: d.key,
          label: d.label,
          color: d.color,
          detail: `${fmt(d.value)} · ${percentOf(d.value)}`,
        }))}
      />
    </div>
  );
}
