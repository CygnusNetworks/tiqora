import { cn } from "@/lib/cn";
import { ChevronDownIcon } from "@/components/ui/icons";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";

/**
 * Entity/enum picker built on the shared {@link SelectMenu} — mirrors the
 * trigger-button markup `ApiKeysPage`/`GdprPage` use so every dropdown in the
 * admin area looks and behaves the same (portal panel, search-if-many,
 * keyboard nav), instead of a native `<select>`. Shared between the AI queue
 * policy list (usage filters) and its full-page editor (entity pickers).
 */
export function PickerField<T extends string | number>({
  testId,
  value,
  items,
  onSelect,
  placeholder,
  loading,
  disabled,
}: {
  testId: string;
  value: T | undefined;
  items: SelectMenuItem<T>[];
  onSelect: (value: T) => void;
  placeholder: string;
  loading?: boolean;
  disabled?: boolean;
}) {
  return (
    <SelectMenu
      items={items}
      value={value}
      onSelect={onSelect}
      loading={loading}
      placeholder={placeholder}
      panelTestId={`${testId}-panel`}
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid={testId}
          disabled={disabled}
          {...toggleProps}
          onClick={disabled ? undefined : toggleProps.onClick}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-left text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
            disabled && "cursor-not-allowed opacity-50",
          )}
        >
          <span className="min-w-0 flex-1 truncate">
            {items.find((i) => i.value === value)?.label ?? placeholder}
          </span>
          <ChevronDownIcon
            className={cn(
              "shrink-0 text-muted transition-transform duration-150",
              open && "rotate-180",
            )}
          />
        </button>
      )}
    />
  );
}
