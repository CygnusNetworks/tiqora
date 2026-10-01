import { cn } from "@/lib/cn";

/** On/off switch (a styled checkbox): keyboard and screen readers see a checkbox. */
export function Switch({
  checked,
  onChange,
  disabled,
  size = "md",
  testId,
  id,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  size?: "md" | "lg";
  testId?: string;
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
}) {
  const lg = size === "lg";
  return (
    <label className={cn("relative inline-flex shrink-0 items-center", disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer")}>
      <input
        type="checkbox"
        role="switch"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        id={id}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        data-testid={testId}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span
        aria-hidden="true"
        className={cn(
          "rounded-full bg-hairline transition-colors peer-checked:bg-accent peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent motion-reduce:transition-none",
          lg ? "h-7 w-12" : "h-5 w-9",
        )}
      />
      <span
        aria-hidden="true"
        className={cn(
          "absolute rounded-full bg-white shadow transition-transform motion-reduce:transition-none",
          lg ? "left-1 top-1 h-5 w-5" : "left-0.5 top-0.5 h-4 w-4",
          checked && (lg ? "translate-x-5" : "translate-x-4"),
        )}
      />
    </label>
  );
}
