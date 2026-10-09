import { cn } from "@/lib/cn";
import { Sparkline } from "./Sparkline";
import { useChartFormat } from "./chartLocale";

export type KpiDelta = {
  /** Relative change, 0.12 = 12 %; may be negative (-0.08 = down 8 %). */
  value: number;
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
  // A negative number always reads as a decrease; otherwise the direction decides.
  const sign = !delta
    ? ""
    : delta.value < 0 || (delta.value > 0 && delta.direction === "down")
      ? "−"
      : delta.value > 0 && delta.direction === "up"
        ? "+"
        : "";
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
          <Arrow direction={delta.direction} />
          <span className="sr-only">
            {t(
              delta.direction === "up"
                ? "reports.chart.deltaUp"
                : delta.direction === "down"
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
