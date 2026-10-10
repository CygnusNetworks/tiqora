import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { fieldsFromSource } from "@/components/filters/filterFields";
import type { FilterFieldDef } from "@/components/filters/filterModel";
import { useFilterOptions } from "@/components/filters/useFilterOptions";
import { SelectField } from "@/components/ui/SelectField";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { Switch } from "@/components/ui/Switch";
import { TagInput } from "@/components/ui/TagInput";
import type { ReportDefinition, ReportParameter, ReportSource } from "@/lib/reportsApi";
import { missingRequired, runtimeParameters, type ReportParamValue, type ReportParamValues } from "./reportFilter";

type Value = string | number;

const isBool = (p: ReportParameter) => p.op === "is_true" || p.op === "is_false";
const isNumeric = (f: FilterFieldDef | undefined) => f?.type === "id" || f?.type === "int";

/** Typed value of a menu entry: ids stay numbers. */
function typed(raw: string, field: FilterFieldDef | undefined): Value {
  return isNumeric(field) && /^-?\d+$/.test(raw) ? Number(raw) : raw;
}

const inputCls =
  "min-w-0 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

function ParameterControl({
  parameter,
  field,
  value,
  onChange,
  testId,
  label,
  labelId,
  missing,
  hintId,
}: {
  parameter: ReportParameter;
  field: FilterFieldDef | undefined;
  value: ReportParamValue | undefined;
  onChange: (next: ReportParamValue) => void;
  testId: string;
  label: string;
  labelId: string;
  missing: boolean;
  hintId: string;
}) {
  const { t } = useTranslation();
  const { options: loaded, isLoading } = useFilterOptions(field?.optionsSource ?? null);
  const known = field && field.enumValues.length > 0 ? field.enumValues : loaded;
  const labelOf = (v: Value) => known.find((o) => o.value === String(v))?.label ?? String(v);

  if (isBool(parameter)) {
    const always = parameter.default != null;
    return (
      <span className="inline-flex items-center gap-2">
        <Switch
          testId={testId}
          aria-labelledby={labelId}
          aria-describedby={always || missing ? hintId : undefined}
          checked={always || value === true}
          disabled={always}
          onChange={(on) => onChange(on)}
        />
        {always && <span className="text-xs text-muted">{t("reports.saved.alwaysOn")}</span>}
      </span>
    );
  }

  // The choices: the report's own list when it limits them, else everything the field offers.
  const choices: SelectMenuItem<string>[] = parameter.allowed_values
    ? parameter.allowed_values.map((v) => ({ value: String(v), label: labelOf(v) }))
    : known.map((o) => ({ value: o.value, label: o.label }));
  const chosen: Value[] = Array.isArray(value) ? value : [];
  const describedBy = missing ? hintId : undefined;

  if (choices.length === 0 && !isLoading) {
    // No list to pick from: free input.
    if (parameter.op === "eq") {
      return (
        <input
          type={isNumeric(field) ? "number" : "text"}
          data-testid={testId}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-invalid={missing || undefined}
          className={`${inputCls} w-full sm:w-56`}
          value={chosen[0] === undefined ? "" : String(chosen[0])}
          onChange={(e) => onChange(e.target.value === "" ? [] : [typed(e.target.value.trim(), field)])}
        />
      );
    }
    return (
      <TagInput
        testId={testId}
        placeholder={t("filters.chooseValues")}
        invalid={missing}
        describedBy={describedBy}
        value={chosen.map(String)}
        onChange={(next) => onChange(next.map((s) => typed(s, field)))}
      />
    );
  }

  if (parameter.op === "eq") {
    return (
      <span className="inline-flex items-center gap-1.5">
        <SelectField
          testId={testId}
          aria-label={label}
          aria-describedby={describedBy}
          invalid={missing}
          placeholder={t("reports.saved.choose")}
          items={choices}
          value={chosen[0] === undefined ? null : String(chosen[0])}
          onChange={(v) => onChange([typed(v, field)])}
        />
        {chosen.length > 0 && !parameter.required && (
          <button
            type="button"
            data-testid={`${testId}-clear`}
            aria-label={t("reports.saved.clearChoice", { label })}
            onClick={() => onChange([])}
            className="rounded px-1 text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            ✕
          </button>
        )}
      </span>
    );
  }

  const rest = choices.filter((c) => !chosen.map(String).includes(c.value));
  return (
    <div
      role="group"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-invalid={missing || undefined}
      data-testid={`${testId}-group`}
      className={`flex min-h-[30px] w-full flex-wrap items-center gap-1.5 rounded border bg-surface px-2 py-1 sm:w-auto sm:min-w-56 ${
        missing ? "border-danger" : "border-hairline"
      }`}
    >
      {chosen.map((v) => (
        <span
          key={String(v)}
          data-testid={`${testId}-chip-${v}`}
          className="inline-flex items-center gap-1 rounded-full bg-accent-dim px-2 py-0.5 text-[12px] font-medium text-accent"
        >
          {labelOf(v)}
          <button
            type="button"
            aria-label={t("fields.removeValue", { label: labelOf(v) })}
            onClick={() => onChange(chosen.filter((x) => x !== v))}
            className="text-accent/70 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            ✕
          </button>
        </span>
      ))}
      {(rest.length > 0 || isLoading) && (
        <SelectMenu
          items={rest}
          loading={isLoading}
          onSelect={(v: string) => onChange([...chosen, typed(v, field)])}
          panelTestId={`${testId}-menu`}
          trigger={({ ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid={testId}
              aria-label={`${label}: ${t("reports.saved.choose")}`}
              {...toggleProps}
              className="rounded-full border border-dashed border-hairline px-2 py-0.5 text-[12px] text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              {chosen.length === 0 ? t("reports.saved.choose") : `+ ${t("fields.addValue")}`}
            </button>
          )}
        />
      )}
    </div>
  );
}

/**
 * The report's "changeable at runtime" parameters, one control each (the queues have the filter bar):
 * several choices for `in`, exactly one for `eq` (the server refuses more), a switch for yes/no parameters.
 * A required parameter without a value says so; the viewer does not run until it has one.
 * Values go to the run request through `toRunIn`.
 */
export function ReportParameterControls({
  definition,
  source,
  values,
  onChange,
  testId = "report-params",
}: {
  definition: ReportDefinition;
  source: ReportSource | undefined;
  values: ReportParamValues;
  onChange: (next: ReportParamValues) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const parameters = runtimeParameters(definition);
  const fields = useMemo(() => {
    if (!source) return new Map<string, FilterFieldDef>();
    return new Map(fieldsFromSource(source, t).map((f) => [f.key, f]));
  }, [source, t]);
  const missing = new Set(missingRequired(definition, values).map((p) => p.key));
  if (parameters.length === 0) return null;

  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-3" data-testid={testId}>
      {parameters.map((p) => {
        const field = fields.get(p.field);
        const label = p.label_key ? t(p.label_key) : (field?.label ?? p.key);
        const labelId = `${uid}-${p.key}-label`;
        const hintId = `${uid}-${p.key}-hint`;
        const id = `${testId}-${p.key}`;
        const defaults = (p.default ?? []).map((v) => field?.enumValues.find((o) => o.value === String(v))?.label ?? String(v));
        const isMissing = missing.has(p.key);
        return (
          <div key={p.key} className="flex min-w-0 flex-col gap-1" data-testid={`${id}-field`}>
            <span id={labelId} className="text-xs font-medium text-muted">
              {label}
            </span>
            <ParameterControl
              parameter={p}
              field={field}
              value={values[p.key]}
              onChange={(next) => onChange({ ...values, [p.key]: next })}
              testId={id}
              label={label}
              labelId={labelId}
              missing={isMissing}
              hintId={hintId}
            />
            {isMissing ? (
              <p id={hintId} className="text-xs text-danger" data-testid={`${id}-required`}>
                {t("reports.saved.required", { label })}
              </p>
            ) : (
              !isBool(p) &&
              defaults.length > 0 &&
              !(Array.isArray(values[p.key]) && (values[p.key] as Value[]).length > 0) && (
                <p id={hintId} className="text-xs text-muted">
                  {t("reports.saved.defaultHint", { values: defaults.join(", ") })}
                </p>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}
