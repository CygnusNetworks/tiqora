import { cn } from "@/lib/cn";

const W = 96;
const H = 30;
const PAD = 2;

export type SparklineTone = "neutral" | "danger" | "ok";

const STROKE: Record<SparklineTone, string> = {
  neutral: "stroke-accent",
  danger: "stroke-danger",
  ok: "stroke-green",
};

const r1 = (n: number) => Math.round(n * 10) / 10;

/** `x,y` pairs of `points` scaled into the 96×30 box (oldest left); a flat series sits mid-height. */
function sparklinePoints(points: number[]): string {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const step = (W - 2 * PAD) / (points.length - 1);
  return points
    .map((v, i) => {
      const y = max === min ? H / 2 : PAD + ((max - v) / (max - min)) * (H - 2 * PAD);
      return `${r1(PAD + i * step)},${r1(y)}`;
    })
    .join(" ");
}

/** 96×30 trend line of a counter; nothing below two points (no invented history). */
export function Sparkline({ points, tone }: { points: number[]; tone: SparklineTone }) {
  const finite = points.filter((p) => Number.isFinite(p));
  if (finite.length < 2) return null;
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      <polyline
        points={sparklinePoints(finite)}
        fill="none"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cn(STROKE[tone])}
      />
    </svg>
  );
}
