import { useId, type ReactNode } from "react";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { cn } from "@/lib/cn";

export const inputCls =
  "min-w-0 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";

/** A titled block of one builder column. */
export function Section({ title, hint, children, testId }: { title: string; hint?: string; children: ReactNode; testId?: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2" data-testid={testId}>
      <div>
        <h2 id={id} className="text-xs font-semibold uppercase tracking-wide text-muted">
          {title}
        </h2>
        {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

/** A small text-only icon button (move, remove) named for screen readers. */
export function IconButton({
  label,
  onClick,
  disabled,
  testId,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      data-testid={testId}
      className="inline-flex h-7 w-7 items-center justify-center rounded text-sm text-muted transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-40"
    >
      <span aria-hidden>{children}</span>
    </button>
  );
}

/** "+ Hinzufügen" button opening a menu of `items`; picking one calls `onPick`. */
export function AddMenu({
  label,
  items,
  onPick,
  disabled,
  testId,
}: {
  label: string;
  items: { value: string; label: string }[];
  onPick: (value: string) => void;
  disabled?: boolean;
  testId: string;
}) {
  const off = disabled || items.length === 0;
  return (
    <Menu
      align="left"
      panelTestId={`${testId}-menu`}
      trigger={({ ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          disabled={off}
          data-testid={testId}
          {...toggleProps}
          className={cn(
            "inline-flex items-center rounded-md border border-dashed border-hairline px-2 py-1 text-xs text-muted transition-colors",
            "hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50",
          )}
        >
          + {label}
        </button>
      )}
    >
      {items.map((item) => (
        <MenuItem key={item.value} onSelect={() => onPick(item.value)} testId={`${testId}-item-${item.value}`}>
          {item.label}
        </MenuItem>
      ))}
    </Menu>
  );
}
