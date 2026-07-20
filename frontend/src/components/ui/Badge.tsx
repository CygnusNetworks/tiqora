import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

type Tone = "default" | "accent" | "warn" | "danger" | "success" | "muted";

const tones: Record<Tone, string> = {
  default: "bg-surface-subtle text-ink border-hairline",
  accent: "bg-accent-dim text-accent border-accent/30",
  warn: "bg-escalation/15 text-escalation border-escalation/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  success: "bg-green/15 text-green border-green/30",
  muted: "bg-surface-subtle text-muted border-hairline",
};

export function Badge({
  children,
  tone = "default",
  className,
  ...rest
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
} & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tones[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
