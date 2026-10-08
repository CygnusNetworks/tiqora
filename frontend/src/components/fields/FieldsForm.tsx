import { cn } from "@/lib/cn";
import { FieldInput } from "./FieldInput";
import type { FieldDef, FieldErrors, FieldOption, FieldValue, FieldValues } from "./types";

/** Renders labelled controls for a list of `FieldDef`s. Values are controlled by the caller. */
export function FieldsForm({
  fields,
  values,
  onChange,
  errors,
  optionsByKey,
  disabled,
  columns = 1,
  testId = "fields",
}: {
  fields: FieldDef[];
  values: FieldValues;
  onChange: (key: string, value: FieldValue) => void;
  errors?: FieldErrors;
  optionsByKey?: Record<string, FieldOption[]>;
  disabled?: boolean;
  columns?: 1 | 2;
  testId?: string;
}) {
  if (fields.length === 0) return null;
  return (
    <div className={cn("grid gap-3", columns === 2 ? "sm:grid-cols-2" : "grid-cols-1")} data-testid={testId}>
      {fields.map((def) => {
        const controlId = `${testId}-${def.key}-control`;
        const error = errors?.[def.key];
        const loaded = def.optionsKey ? optionsByKey?.[def.optionsKey] : undefined;
        return (
          <div key={def.key} className="min-w-0">
            <label
              htmlFor={controlId}
              data-testid={`${testId}-${def.key}-label`}
              className="mb-1 block text-xs font-medium text-muted"
            >
              {def.label}
              {def.required && (
                <span aria-hidden="true" className="ml-0.5 text-danger">
                  *
                </span>
              )}
            </label>
            <FieldInput
              def={def}
              id={controlId}
              value={values[def.key] ?? null}
              onChange={(v) => onChange(def.key, v)}
              testId={`${testId}-${def.key}`}
              options={loaded ?? def.options}
              disabled={disabled}
              invalid={!!error}
            />
            {def.help && !error && <p className="mt-1 text-xs text-muted">{def.help}</p>}
            {error && (
              <p data-testid={`${testId}-${def.key}-error`} role="alert" className="mt-1 text-xs text-danger">
                {error}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
