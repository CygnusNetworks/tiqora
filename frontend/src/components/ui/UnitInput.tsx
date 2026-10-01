import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/**
 * Number input with a unit suffix ("Min.", "%") drawn inside the field, so
 * the unit stays out of the label and the value column lines up.
 * Everything except `unit`/`invalid`/`wrapperClassName` goes to the <input>.
 */
export const UnitInput = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
    unit: string;
    invalid?: boolean;
    wrapperClassName?: string;
  }
>(function UnitInput({ unit, invalid, wrapperClassName, className, ...rest }, ref) {
  return (
    <div className={cn("relative min-w-0", wrapperClassName)}>
      <input
        ref={ref}
        type="number"
        aria-invalid={invalid || undefined}
        {...rest}
        className={cn(
          "w-full rounded-md border bg-surface-subtle py-1.5 pl-3 pr-12 font-sans text-sm tabular-nums text-ink placeholder:text-muted",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          "disabled:cursor-not-allowed disabled:opacity-45",
          invalid ? "border-escalation" : "border-hairline focus:border-accent",
          className,
        )}
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-muted"
      >
        {unit}
      </span>
    </div>
  );
});
