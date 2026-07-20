import { cn } from "@/lib/cn";

export type StatTileProps = {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "danger" | "warn";
  testId?: string;
};

const toneClasses: Record<NonNullable<StatTileProps["tone"]>, string> = {
  default: "text-ink",
  danger: "text-danger",
  warn: "text-warn",
};

/** Mono-numeral stat tile used across the Reports page (Cobalt Compact tokens). */
export function StatTile({ label, value, hint, tone = "default", testId }: StatTileProps) {
  return (
    <div
      className="rounded-lg border border-hairline bg-surface p-4"
      data-testid={testId}
    >
      <p className="truncate text-xs uppercase tracking-wide text-muted">{label}</p>
      <p
        className={cn(
          "mt-2 font-mono text-2xl font-semibold tabular-nums",
          toneClasses[tone],
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
