import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

type Tone = "default" | "accent" | "warn" | "danger" | "success" | "muted";

const tones: Record<Tone, string> = {
  default: "bg-surface-subtle text-ink border-hairline",
  accent: "bg-accent/15 text-accent border-accent/30",
  warn: "bg-escalation/15 text-escalation border-escalation/30",
  danger: "bg-escalation/15 text-escalation border-escalation/30",
  success: "bg-state-open/15 text-state-open border-state-open/30",
  muted: "bg-surface-subtle text-muted border-hairline",
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
