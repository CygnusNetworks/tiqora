import { cn } from "@/lib/cn";
import { Sparkline } from "./Sparkline";
import { useChartFormat } from "./chartLocale";

export type KpiDelta = {
  /** Relative change, 0.12 = 12 %; may be negative (-0.08 = down 8 %). The sign drives arrow, text and screen-reader wording. */
  value: number;
  /**
   * Caller contract: only meaningful for colour. `direction === goodWhen` is green, the opposite is red,
   * "flat" is muted. Pass the direction of the change (the sign of `value`); the tile never lets it
   * contradict the printed sign.
   */
  direction: "up" | "down" | "flat";
  /** Which direction is an improvement; decides green vs. red. */
  goodWhen: "up" | "down";
};

export type KpiTileProps = {
  label: string;
  value: string;
  delta?: KpiDelta;
  sparkline?: number[];
  hint?: string;
  testId?: string;
};

function Arrow({ direction }: { direction: KpiDelta["direction"] }) {
  const d =
    direction === "up" ? "M6 2 L10 8 H2 Z" : direction === "down" ? "M6 10 L10 4 H2 Z" : "M2 6 H10";
  return (
    <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden>
      {direction === "flat" ? (
        <path d={d} stroke="currentColor" strokeWidth={2} fill="none" />
      ) : (
        <path d={d} fill="currentColor" />
      )}
    </svg>
  );
}

/** Stat tile with a headline value, a change indicator (icon + text, never colour alone) and an optional sparkline. */
export function KpiTile({ label, value, delta, sparkline, hint, testId }: KpiTileProps) {
  const { t, percent: formatPercent } = useChartFormat();
  const tone = !delta || delta.direction === "flat"
    ? "text-muted"
    : delta.direction === delta.goodWhen
      ? "text-green"
      : "text-danger";
  // Arrow, sign and screen-reader text all come from the sign of the number.
  const shown: KpiDelta["direction"] = !delta || delta.value === 0 ? "flat" : delta.value < 0 ? "down" : "up";
  const sign = shown === "down" ? "−" : shown === "up" ? "+" : "";
  const percent = delta ? `${sign}${formatPercent(Math.abs(delta.value))}` : "";

  return (
    <div className="rounded-lg border border-hairline bg-surface p-4" data-testid={testId}>
      <p className="truncate text-xs uppercase tracking-wide text-muted">{label}</p>
      <div className="mt-2 flex items-end justify-between gap-2">
        <p className="font-mono text-2xl font-semibold tabular-nums text-ink">{value}</p>
        {sparkline && sparkline.length > 0 && (
          <Sparkline values={sparkline} testId={testId ? `${testId}-sparkline` : undefined} />
        )}
      </div>
      {delta && (
        <p
          className={cn("mt-1 flex items-center gap-1 text-xs font-medium tabular-nums", tone)}
          data-testid={testId ? `${testId}-delta` : undefined}
        >
          <Arrow direction={shown} />
          <span className="sr-only">
            {t(
              shown === "up"
                ? "reports.chart.deltaUp"
                : shown === "down"
                  ? "reports.chart.deltaDown"
                  : "reports.chart.deltaFlat",
            )}
          </span>
          <span>{percent}</span>
        </p>
      )}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
