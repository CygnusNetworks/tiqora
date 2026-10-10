import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { FilterFieldDef } from "@/components/filters/filterModel";
import { FilterValueInput } from "@/components/filters/FilterValueInput";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { MAX_PARAMETERS, type ReportParameter } from "./builderState";

type ParamOp = NonNullable<ReportParameter["op"]>;
const PARAM_OPS: readonly ParamOp[] = ["in", "eq", "is_true", "is_false"];
const NO_VALUE_OPS: ReadonlySet<string> = new Set(["is_true", "is_false"]);

/** Parameter key from a field key: lower-case letters, digits and underscores, unique among `taken`. */
function keyFor(field: string, taken: Set<string>): string {
  const base = (field.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^[^a-z]+/, "") || "p").slice(0, 36);
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}_${n}`;
  return key;
}

function opsOf(field: FilterFieldDef | undefined): ParamOp[] {
  return PARAM_OPS.filter((op) => field?.ops.includes(op));
}

const toList = (v: unknown): (string | number)[] => (Array.isArray(v) ? (v as (string | number)[]) : []);

/**
 * "Beim Aufruf änderbar": fields the viewer can set when opening the report. Per field: comparison,
 * allowed values (empty = all), default values and whether a value is required. The parameter name
 * is derived from the field.
 */
export function BuilderParametersEditor({
  parameters,
  fields,
  onChange,
  issues = [],
  testId = "builder-parameters",
}: {
  parameters: ReportParameter[];
  fields: FilterFieldDef[];
  onChange: (next: ReportParameter[]) => void;
  /** Issue keys by parameter index (from `validate`, path `parameters.<i>.…`). */
  issues?: { path: string; key: string }[];
  testId?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const fieldItems = fields.map((f) => ({ value: f.key, label: f.label }));
  const patch = (i: number, p: Partial<ReportParameter>) =>
    onChange(parameters.map((x, j) => (j === i ? { ...x, ...p } : x)));

  const add = () => {
    const used = new Set(parameters.map((p) => p.field));
    const field = fields.find((f) => !used.has(f.key) && opsOf(f).length > 0) ?? fields.find((f) => opsOf(f).length > 0);
    if (!field) return;
    const op = opsOf(field)[0];
    onChange([
      ...parameters,
      { key: keyFor(field.key, new Set(parameters.map((p) => p.key))), field: field.key, op, required: false },
    ]);
  };

  const changeField = (i: number, key: string) => {
    const field = fields.find((f) => f.key === key);
    const others = new Set(parameters.filter((_, j) => j !== i).map((p) => p.key));
    patch(i, {
      field: key,
      key: keyFor(key, others),
      op: opsOf(field)[0],
      allowed_values: null,
      default: null,
    });
  };

  return (
    <div className="space-y-3" data-testid={testId}>
      <p className="text-xs text-muted">{t("reports.builder.parametersHint")}</p>
      {parameters.map((p, i) => {
        const field = fields.find((f) => f.key === p.field);
        const op = p.op ?? "in";
        const own = issues.filter((x) => x.path.startsWith(`parameters.${i}.`));
        const label = field?.label ?? p.field;
        return (
          <fieldset
            key={`${p.key}-${i}`}
            className="space-y-2 rounded-md border border-hairline p-2"
            data-testid={`${testId}-item-${i}`}
          >
            <legend className="px-1 text-xs font-medium text-ink">{label}</legend>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <span id={`${id}-${i}-field`} className="block text-xs text-muted">
                  {t("reports.builder.parameterField")}
                </span>
                <SelectField
                  items={fieldItems}
                  value={p.field}
                  onChange={(v) => changeField(i, v)}
                  testId={`${testId}-field-${i}`}
                  aria-label={`${label}: ${t("reports.builder.parameterField")}`}
                />
              </div>
              <div className="space-y-1">
                <span className="block text-xs text-muted">{t("reports.builder.parameterOp")}</span>
                <SelectField
                  items={opsOf(field).map((o) => ({ value: o, label: t(`filters.ops.${o}`) }))}
                  value={op}
                  onChange={(v) => patch(i, { op: v, ...(NO_VALUE_OPS.has(v) ? { allowed_values: null, default: null } : {}) })}
                  testId={`${testId}-op-${i}`}
                  aria-label={`${label}: ${t("reports.builder.parameterOp")}`}
                />
              </div>
            </div>
            {!NO_VALUE_OPS.has(op) && (
              <>
                <div className="space-y-1">
                  <span className="block text-xs text-muted">{t("reports.builder.parameterAllowed")}</span>
                  <FilterValueInput
                    field={field}
                    op="in"
                    value={toList(p.allowed_values)}
                    onChange={(v) => patch(i, { allowed_values: toList(v).length > 0 ? toList(v) : null })}
                    testId={`${testId}-allowed-${i}`}
                    label={`${label}: ${t("reports.builder.parameterAllowed")}`}
                  />
                </div>
                <div className="space-y-1">
                  <span className="block text-xs text-muted">{t("reports.builder.parameterDefault")}</span>
                  <FilterValueInput
                    field={field}
                    op="in"
                    value={toList(p.default)}
                    onChange={(v) => patch(i, { default: toList(v).length > 0 ? toList(v) : null })}
                    testId={`${testId}-default-${i}`}
                    label={`${label}: ${t("reports.builder.parameterDefault")}`}
                  />
                </div>
              </>
            )}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs text-ink">
                <Switch
                  checked={p.required ?? false}
                  onChange={(required) => patch(i, { required })}
                  testId={`${testId}-required-${i}`}
                  aria-labelledby={`${id}-${i}-required`}
                />
                <span id={`${id}-${i}-required`}>{t("reports.builder.parameterRequired")}</span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onChange(parameters.filter((_, j) => j !== i))}
                aria-label={`${label}: ${t("reports.builder.remove")}`}
                data-testid={`${testId}-remove-${i}`}
              >
                {t("reports.builder.remove")}
              </Button>
            </div>
            {own.length > 0 && (
              <ul className="space-y-0.5 text-xs text-danger">
                {own.map((x) => (
                  <li key={`${x.path}-${x.key}`}>{t(`reports.builder.issues.${x.key}`)}</li>
                ))}
              </ul>
            )}
          </fieldset>
        );
      })}
      <Button
        size="sm"
        onClick={add}
        disabled={parameters.length >= MAX_PARAMETERS || !fields.some((f) => opsOf(f).length > 0)}
        data-testid={`${testId}-add`}
      >
        + {t("reports.builder.addParameter")}
      </Button>
    </div>
  );
}
