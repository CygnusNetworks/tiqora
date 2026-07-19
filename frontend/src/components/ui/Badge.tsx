import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

type Tone = "default" | "accent" | "warn" | "danger" | "success" | "muted";

const tones: Record<Tone, string> = {
  default: "bg-surface text-ink border-border",
  accent: "bg-accent/15 text-accent border-accent/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  success: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30",
  muted: "bg-surface text-muted border-border",
};

export function Badge({
  children,
  tone = "default",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
