import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
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
  extras,
}: {
  fields: FieldDef[];
  values: FieldValues;
  onChange: (key: string, value: FieldValue) => void;
  errors?: FieldErrors;
  optionsByKey?: Record<string, FieldOption[]>;
  disabled?: boolean;
  columns?: 1 | 2;
  testId?: string;
  /** Extra content rendered right under the field with that key (for example an upload area under a body). */
  extras?: Record<string, ReactNode>;
}) {
  const { t } = useTranslation();
  if (fields.length === 0) return null;
  return (
    <div className={cn("grid gap-3", columns === 2 ? "sm:grid-cols-2" : "grid-cols-1")} data-testid={testId}>
      {fields.map((def) => {
        const controlId = `${testId}-${def.key}-control`;
        const error = errors?.[def.key];
        const labelId = `${testId}-${def.key}-label`;
        const errorId = `${testId}-${def.key}-error`;
        const helpId = `${testId}-${def.key}-help`;
        const describedBy = error ? errorId : def.help ? helpId : undefined;
        const loaded = def.optionsKey ? optionsByKey?.[def.optionsKey] : undefined;
        return (
          <div key={def.key} className="min-w-0">
            <label
              htmlFor={controlId}
              id={labelId}
              data-testid={labelId}
              className="mb-1 block text-xs font-medium text-muted"
            >
              {def.label}
              {def.required && (
                <span className="ml-0.5 text-danger">
                  <span aria-hidden="true">*</span>
                  <span className="sr-only"> ({t("fields.required")})</span>
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
              labelId={labelId}
              describedBy={describedBy}
            />
            {def.help && !error && <p id={helpId} className="mt-1 text-xs text-muted">{def.help}</p>}
            {error && (
              <p id={errorId} data-testid={errorId} role="alert" className="mt-1 text-xs text-danger">
                {error}
              </p>
            )}
            {extras?.[def.key]}
          </div>
        );
      })}
    </div>
  );
}
