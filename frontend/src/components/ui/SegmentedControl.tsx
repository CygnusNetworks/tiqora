import { useRef, type KeyboardEvent } from "react";
import { cn } from "@/lib/cn";

export type SegmentedItem<T extends string | number> = { value: T; label: string };

/**
 * Single-choice segmented control for two to four short options. Screen
 * readers see a radiogroup: one tab stop (the checked option, else the
 * first), arrow keys move and select, Home/End jump to the ends.
 *
 * `data-testid={testId}` sits on the group, `${testId}-${value}` on each
 * option.
 */
export function SegmentedControl<T extends string | number>({
  items,
  value,
  onChange,
  testId,
  id,
  disabled,
  invalid,
  fullWidth,
  className,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
}: {
  items: SegmentedItem<T>[];
  value: T | null | undefined;
  onChange: (value: T) => void;
  testId?: string;
  id?: string;
  disabled?: boolean;
  invalid?: boolean;
  /** Stretch the options to fill the row (equal widths). */
  fullWidth?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const checkedIdx = items.findIndex((i) => String(i.value) === String(value ?? ""));
  const tabStop = checkedIdx >= 0 ? checkedIdx : 0;

  const move = (to: number) => {
    const n = items.length;
    if (n === 0) return;
    const idx = ((to % n) + n) % n;
    onChange(items[idx].value);
    refs.current[idx]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (step !== undefined) {
      e.preventDefault();
      move(idx + step);
    } else if (e.key === "Home") {
      e.preventDefault();
      move(0);
    } else if (e.key === "End") {
      e.preventDefault();
      move(items.length - 1);
    }
  };

  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-describedby={ariaDescribedBy}
      aria-invalid={invalid || undefined}
      aria-disabled={disabled || undefined}
      data-testid={testId}
      className={cn(
        "gap-0.5 rounded-md border bg-surface-subtle p-0.5",
        fullWidth ? "flex w-full" : "inline-flex w-fit flex-wrap",
        invalid ? "border-escalation" : "border-hairline",
        disabled && "opacity-60",
        className,
      )}
    >
      {items.map((item, idx) => {
        const on = idx === checkedIdx;
        return (
          <button
            key={String(item.value)}
            ref={(el) => {
              refs.current[idx] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={idx === tabStop ? 0 : -1}
            disabled={disabled}
            data-testid={testId ? `${testId}-${item.value}` : undefined}
            onClick={() => onChange(item.value)}
            onKeyDown={(e) => onKeyDown(e, idx)}
            className={cn(
              "whitespace-nowrap rounded px-2.5 py-1 text-[12.5px] font-medium transition-colors duration-100 motion-reduce:transition-none",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              // flex-auto: long labels ("Wiedereröffnen") get the room they need.
              fullWidth && "min-w-0 flex-auto",
              on
                ? "bg-surface text-ink shadow-sm ring-1 ring-hairline"
                : "text-muted hover:text-ink disabled:hover:text-muted",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
