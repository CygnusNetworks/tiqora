import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { SelectField } from "@/components/ui/SelectField";
import { TagInput } from "@/components/ui/TagInput";
import { cn } from "@/lib/cn";
import { RELATIVE_UNITS, valueShape, type FilterFieldDef } from "./filterModel";
import { useFilterOptions } from "./useFilterOptions";

const inputCls =
  "min-w-0 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60";
const invalidCls = "border-danger";

type Props = {
  field: FilterFieldDef | undefined;
  op: string;
  value: unknown;
  onChange: (next: unknown) => void;
  testId: string;
  /** Accessible name prefix, normally the field label. */
  label: string;
  invalid?: boolean;
  errorId?: string;
  disabled?: boolean;
};

const isNumeric = (type: FilterFieldDef["type"] | undefined) => type === "id" || type === "int";

/** Typed value of a text the user entered: numbers for id/int fields while they parse. */
function typed(raw: string, type: FilterFieldDef["type"] | undefined): unknown {
  if (!isNumeric(type)) return raw;
  return /^-?\d+$/.test(raw.trim()) ? Number(raw.trim()) : raw;
}

const shown = (v: unknown) => (v === undefined || v === null ? "" : String(v));

/** The value editor of one condition; which control shows depends on the field type and operator. */
export function FilterValueInput({
  field,
  op,
  value,
  onChange,
  testId,
  label,
  invalid,
  errorId,
  disabled,
}: Props) {
  const { t } = useTranslation();
  const { options: loaded, isLoading } = useFilterOptions(field?.optionsSource ?? null);
  const shape = valueShape(op);
  const type = field?.type;
  const options = field && field.enumValues.length > 0 ? field.enumValues : loaded;
  const hasOptions = options.length > 0 || isLoading;
  const aria = `${label}: ${t("filters.value")}`;
  const common = {
    disabled,
    "aria-invalid": invalid || undefined,
    "aria-describedby": invalid ? errorId : undefined,
  } as const;

  if (shape === "none") return null;

  if (shape === "relative") {
    const v = (value ?? {}) as { amount?: unknown; unit?: string };
    const set = (patch: object) => onChange({ amount: v.amount ?? 1, unit: v.unit ?? "day", ...patch });
    return (
      <span className="flex flex-wrap items-center gap-2" data-testid={testId}>
        <input
          {...common}
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          aria-label={`${label}: ${t("filters.amount")}`}
          data-testid={`${testId}-amount`}
          value={shown(v.amount)}
          onChange={(e) => set({ amount: e.target.value === "" ? "" : Number(e.target.value) })}
          className={cn(inputCls, "w-20", invalid && invalidCls)}
        />
        <select
          {...common}
          aria-label={`${label}: ${t("filters.unit")}`}
          data-testid={`${testId}-unit`}
          value={v.unit ?? "day"}
          onChange={(e) => set({ unit: e.target.value })}
          className={cn(inputCls, "w-28")}
        >
          {RELATIVE_UNITS.map((u) => (
            <option key={u} value={u}>
              {t(`filters.units.${u}`)}
            </option>
          ))}
        </select>
      </span>
    );
  }

  if (shape === "range") {
    const v = (value ?? {}) as { from?: unknown; to?: unknown };
    const date = type === "datetime";
    const part = (key: "from" | "to") => (
      <input
        {...common}
        type={date ? "date" : "number"}
        inputMode={date ? undefined : "numeric"}
        aria-label={`${label}: ${t(`filters.${key}`)}`}
        data-testid={`${testId}-${key}`}
        value={shown(v[key])}
        onChange={(e) => onChange({ from: v.from ?? "", to: v.to ?? "", [key]: date ? e.target.value : typed(e.target.value, type) })}
        className={cn(inputCls, date ? "w-40" : "w-28", invalid && invalidCls)}
      />
    );
    return (
      <span className="flex flex-wrap items-center gap-2" data-testid={testId}>
        {part("from")}
        <span className="text-sm text-muted" aria-hidden>
          –
        </span>
        {part("to")}
      </span>
    );
  }

  if (shape === "list") {
    const list = Array.isArray(value) ? value : [];
    if (hasOptions) {
      return (
        <MultiSelectInput
          testId={testId}
          label={aria}
          disabled={disabled}
          invalid={invalid}
          describedBy={invalid ? errorId : undefined}
          options={options}
          value={list.map(String)}
          onChange={(next) => onChange(next.map((s) => typed(s, type)))}
        />
      );
    }
    return (
      <TagInput
        testId={testId}
        placeholder={t("filters.chooseValues")}
        invalid={invalid}
        describedBy={invalid ? errorId : undefined}
        value={list.map(String)}
        onChange={(next) => onChange(next.map((s) => typed(s, type)))}
      />
    );
  }

  // scalar
  if (hasOptions && (op === "eq" || op === "neq") && (type === "id" || type === "enum" || type === "string")) {
    return (
      <SelectField
        testId={testId}
        aria-label={aria}
        aria-describedby={invalid ? errorId : undefined}
        invalid={invalid}
        disabled={disabled}
        placeholder={t("filters.chooseValues")}
        items={options}
        value={value === "" || value === undefined ? null : String(value)}
        onChange={(v) => onChange(typed(v, type))}
      />
    );
  }
  if (type === "datetime") {
    return (
      <input
        {...common}
        type="date"
        aria-label={aria}
        data-testid={testId}
        value={shown(value)}
        onChange={(e) => onChange(e.target.value)}
        className={cn(inputCls, "w-40", invalid && invalidCls)}
      />
    );
  }
  if (isNumeric(type)) {
    return (
      <input
        {...common}
        type="number"
        inputMode="numeric"
        aria-label={aria}
        data-testid={testId}
        value={shown(value)}
        onChange={(e) => onChange(typed(e.target.value, type))}
        className={cn(inputCls, "w-32", invalid && invalidCls)}
      />
    );
  }
  return (
    <input
      {...common}
      type="text"
      aria-label={aria}
      data-testid={testId}
      value={shown(value)}
      onChange={(e) => onChange(e.target.value)}
      className={cn(inputCls, "w-full sm:w-64", invalid && invalidCls)}
    />
  );
}
