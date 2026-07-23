import { cn } from "@/lib/cn";
import { SelectMenu, type SelectMenuItem } from "./SelectMenu";

function ChevronDownIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={cn("h-3.5 w-3.5", className)}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

/**
 * Form-field flavour of `SelectMenu`: an input-styled trigger button that
 * replaces native `<select>` elements in dialogs and toolbars, so every
 * dropdown in the app shares the same portal panel (search, keyboard
 * navigation, no clipping inside overflow containers).
 *
 * The trigger carries `data-testid={testId}`, the panel `${testId}-menu`,
 * options `${testId}-menu-option-<value>` — same convention as CrudDrawer's
 * select fields.
 */
export function SelectField<T extends string | number>({
  items,
  value,
  onChange,
  testId,
  placeholder,
  className,
  disabled,
  "aria-label": ariaLabel,
}: {
  items: SelectMenuItem<T>[];
  value: T | null | undefined;
  onChange: (value: T) => void;
  testId?: string;
  /** Shown on the trigger when nothing is selected and as panel search hint. */
  placeholder?: string;
  /** Extra classes for the trigger button (sizing/width overrides). */
  className?: string;
  disabled?: boolean;
  "aria-label"?: string;
}) {
  const selected = items.find((i) => i.value === value);
  return (
    <SelectMenu
      items={items}
      value={value ?? null}
      onSelect={onChange}
      placeholder={placeholder}
      panelTestId={testId ? `${testId}-menu` : undefined}
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid={testId}
          aria-label={ariaLabel}
          disabled={disabled}
          {...toggleProps}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded border border-hairline bg-surface px-2 py-1.5 text-left text-sm text-ink",
            "focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60",
            className,
          )}
        >
          <span className={cn("truncate", !selected && "text-muted")}>
            {selected?.label ?? placeholder ?? ""}
          </span>
          <ChevronDownIcon
            className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
          />
        </button>
      )}
    />
  );
}
