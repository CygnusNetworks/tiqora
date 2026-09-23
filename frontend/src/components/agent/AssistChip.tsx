import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/**
 * Compact trigger in the ticket header's assist row (summary, drafts,
 * similar tickets). Highlighted while its card is open or, via
 * `aria-pressed`, while its content is pinned below the row.
 */
export const AssistChip = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(
  function AssistChip({ className, children, ...props }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border border-hairline bg-surface-subtle px-2.5 py-1 text-xs font-medium text-ink transition-colors",
          "hover:border-purple/60 aria-expanded:border-purple aria-pressed:border-purple aria-pressed:bg-purple/10",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          className,
        )}
        {...props}
      >
        {children}
      </button>
    );
  },
);

/** Small count pill inside an `AssistChip`. */
export function ChipCount({ value, highlight }: { value: number; highlight?: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 font-mono text-[10px] leading-4 tabular-nums",
        highlight ? "bg-accent text-accent-ink" : "bg-surface text-muted",
      )}
    >
      {value}
    </span>
  );
}
