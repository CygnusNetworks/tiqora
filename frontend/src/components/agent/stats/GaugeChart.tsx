import { useChartFormat } from "./chartLocale";
import { gaugeTone, type GaugeTone } from "./gaugeTone";

export type GaugeChartProps = {
  /** 0..100 (a percentage); `null` = nothing to measure. */
  value: number | null;
  /** Goal on the same 0..100 scale: drawn as a tick, decides the colour. */
  target?: number;
  /** Name of the measure, first part of the accessible label ("SLA-Erfüllung"). */
  label: string;
  formatValue?: (v: number) => string;
  emptyLabel?: string;
  testId?: string;
};

const TONE_COLOR: Record<GaugeTone, string> = {
  success: "var(--color-green)",
  warn: "var(--color-warn)",
  danger: "var(--color-danger)",
  neutral: "var(--color-series-1)",
};
const TONE_TEXT: Record<GaugeTone, string> = {
  success: "text-green",
  warn: "text-warn",
  danger: "text-danger",
  neutral: "text-ink",
};

const CX = 60;
const CY = 58;
const R = 46;
const STROKE = 11;

/** Point on the arc at `fraction` (0 = left end, 1 = right end) at radius `r`. */
function point(fraction: number, r: number): [number, number] {
  const angle = Math.PI * (1 - fraction);
  return [CX + r * Math.cos(angle), CY - r * Math.sin(angle)];
}

function arc(from: number, to: number): string {
  const [x0, y0] = point(from, R);
  const [x1, y1] = point(to, R);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v / 100));

/**
 * Semicircle gauge for a percentage with a target tick. The colour is never the only signal: the
 * accessible label and the visible numbers carry value and target.
 */
export function GaugeChart({ value, target, label, formatValue, emptyLabel, testId }: GaugeChartProps) {
  const { t, number } = useChartFormat();
  const fmt = formatValue ?? ((v: number) => `${number(v)} %`);
  if (value === null || !Number.isFinite(value)) {
    return (
      <div
        className="flex h-full min-h-[96px] items-center justify-center text-center text-sm text-muted"
        data-testid={testId}
      >
        {emptyLabel ?? t("reports.chart.gaugeNoData")}
      </div>
    );
  }
  const tone = gaugeTone(value, target);
  const fraction = clamp01(value);
  const aria =
    target === undefined
      ? `${label} ${fmt(value)}`
      : `${label} ${fmt(value)}, ${t("reports.chart.gaugeTarget", { value: fmt(target) })}`;
  const tick = target === undefined ? null : clamp01(target);
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center" data-testid={testId}>
      <svg
        viewBox="0 0 120 72"
        className="max-h-full w-full max-w-[16rem]"
        role="img"
        aria-label={aria}
        data-tone={tone}
        data-testid={testId ? `${testId}-svg` : undefined}
      >
        <path d={arc(0, 1)} fill="none" stroke="var(--color-surface-subtle)" strokeWidth={STROKE} strokeLinecap="butt" />
        {fraction > 0 && (
          <path
            d={arc(0, Math.max(fraction, 0.001))}
            fill="none"
            stroke={TONE_COLOR[tone]}
            strokeWidth={STROKE}
            strokeLinecap="butt"
            data-testid={testId ? `${testId}-arc` : undefined}
          />
        )}
        {tick !== null && (
          <line
            x1={point(tick, R - STROKE / 2 - 3)[0]}
            y1={point(tick, R - STROKE / 2 - 3)[1]}
            x2={point(tick, R + STROKE / 2 + 3)[0]}
            y2={point(tick, R + STROKE / 2 + 3)[1]}
            stroke="var(--color-ink)"
            strokeWidth={2}
            strokeLinecap="round"
            data-testid={testId ? `${testId}-target` : undefined}
          />
        )}
        <text
          x={CX}
          y={CY - 6}
          textAnchor="middle"
          className={`font-mono text-[20px] font-semibold ${TONE_TEXT[tone]}`}
          style={{ fill: "currentColor" }}
          aria-hidden
        >
          {fmt(value)}
        </text>
      </svg>
      {target !== undefined && (
        <p className="mt-1 text-xs text-muted" data-testid={testId ? `${testId}-target-label` : undefined}>
          {t("reports.chart.gaugeTarget", { value: fmt(target) })}
        </p>
      )}
    </div>
  );
}
