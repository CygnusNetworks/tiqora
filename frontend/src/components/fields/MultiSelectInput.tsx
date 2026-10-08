import { useTranslation } from "react-i18next";
import { SelectMenu } from "@/components/ui/SelectMenu";
import type { FieldOption } from "./types";

/** Removable chips for the chosen values plus a menu offering the rest. */
export function MultiSelectInput({
  value,
  onChange,
  options,
  testId,
  disabled,
  invalid,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  options: FieldOption[];
  testId: string;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const { t } = useTranslation();
  const labelOf = (v: string) => options.find((o) => o.value === v)?.label ?? v;
  const rest = options.filter((o) => !value.includes(o.value));
  return (
    <div
      data-testid={testId}
      aria-invalid={invalid || undefined}
      className={`flex min-h-[30px] w-full flex-wrap items-center gap-1.5 rounded border bg-surface px-2 py-1 ${
        invalid ? "border-danger" : "border-hairline"
      }`}
    >
      {value.map((v) => (
        <span
          key={v}
          data-testid={`${testId}-chip-${v}`}
          className="inline-flex items-center gap-1 rounded-full bg-accent-dim px-2 py-0.5 text-[12px] font-medium text-accent"
        >
          {labelOf(v)}
          {!disabled && (
            <button
              type="button"
              aria-label={t("fields.removeValue", { label: labelOf(v) })}
              data-testid={`${testId}-remove-${v}`}
              onClick={() => onChange(value.filter((x) => x !== v))}
              className="text-accent/70 hover:text-accent"
            >
              ✕
            </button>
          )}
        </span>
      ))}
      {!disabled && rest.length > 0 && (
        <SelectMenu
          items={rest}
          onSelect={(v: string) => onChange([...value, v])}
          panelTestId={`${testId}-add-menu`}
          trigger={({ ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid={`${testId}-add`}
              {...toggleProps}
              className="rounded-full border border-dashed border-hairline px-2 py-0.5 text-[12px] text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              + {t("fields.addValue")}
            </button>
          )}
        />
      )}
    </div>
  );
}
