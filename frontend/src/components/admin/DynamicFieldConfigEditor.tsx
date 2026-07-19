import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";

export type DynamicFieldConfig = Record<string, unknown>;

const SELECT_TYPES = new Set(["Dropdown", "Multiselect"]);
const DATETIME_TYPES = new Set(["Date", "DateTime"]);
const TEXT_TYPES = new Set(["Text", "TextArea"]);

/**
 * Type-specific config sub-form, switched on field_type — matches the key
 * names Znuny's DynamicField drivers expect (see
 * backend/src/tiqora/api/v1/admin/dynamic_fields.py):
 *   Text/TextArea: DefaultValue (+ TextArea: Rows/Cols, both optional — omitted here)
 *   Checkbox: DefaultValue
 *   Dropdown/Multiselect: PossibleValues (required, key -> label), PossibleNone, DefaultValue
 *   Date/DateTime: YearsInPast, YearsInFuture, DefaultValue
 */
export function DynamicFieldConfigEditor({
  fieldType,
  value,
  onChange,
}: {
  fieldType: string;
  value: DynamicFieldConfig;
  onChange: (next: DynamicFieldConfig) => void;
}) {
  const { t } = useTranslation();
  const set = (key: string, v: unknown) => {
    if (v === "" || v === undefined) {
      const { [key]: _omit, ...rest } = value;
      onChange(rest);
      return;
    }
    onChange({ ...value, [key]: v });
  };

  const inputClass =
    "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";
  const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

  if (SELECT_TYPES.has(fieldType)) {
    const possibleValues = (value.PossibleValues as Record<string, string> | undefined) ?? {};
    const entries = Object.entries(possibleValues);

    const updateEntry = (idx: number, key: string, label: string) => {
      const next = [...entries];
      next[idx] = [key, label];
      const nextMap: Record<string, string> = {};
      for (const [k, v] of next) if (k !== "") nextMap[k] = v;
      set("PossibleValues", nextMap);
    };
    const removeEntry = (idx: number) => {
      const next = entries.filter((_, i) => i !== idx);
      const nextMap: Record<string, string> = {};
      for (const [k, v] of next) nextMap[k] = v;
      set("PossibleValues", nextMap);
    };
    const addEntry = () => {
      set("PossibleValues", { ...possibleValues, "": "" });
    };

    return (
      <div className="space-y-3" data-testid="dynamic-field-config-select">
        <div>
          <span className={labelClass}>
            {t("admin.dynamicFields.possibleValues")}
            <span className="text-escalation"> *</span>
          </span>
          <div className="space-y-1.5">
            {entries.map(([key, label], idx) => (
              <div key={idx} className="flex items-center gap-1.5">
                <input
                  data-testid={`dynamic-field-option-key-${idx}`}
                  value={key}
                  placeholder={t("admin.dynamicFields.optionKey")}
                  onChange={(e) => updateEntry(idx, e.target.value, label)}
                  className={inputClass}
                />
                <input
                  data-testid={`dynamic-field-option-label-${idx}`}
                  value={label}
                  placeholder={t("admin.dynamicFields.optionLabel")}
                  onChange={(e) => updateEntry(idx, key, e.target.value)}
                  className={inputClass}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid={`dynamic-field-option-remove-${idx}`}
                  onClick={() => removeEntry(idx)}
                  aria-label={t("admin.dynamicFields.removeOption")}
                >
                  ✕
                </Button>
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-2"
            data-testid="dynamic-field-option-add"
            onClick={addEntry}
          >
            {t("admin.dynamicFields.addOption")}
          </Button>
          {entries.length === 0 && (
            <p className="mt-1 text-xs text-escalation">
              {t("admin.dynamicFields.possibleValuesRequired")}
            </p>
          )}
        </div>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            data-testid="dynamic-field-possible-none"
            checked={Boolean(value.PossibleNone)}
            onChange={(e) => set("PossibleNone", e.target.checked || undefined)}
            className="h-4 w-4 rounded border-hairline"
          />
          {t("admin.dynamicFields.possibleNone")}
        </label>
      </div>
    );
  }

  if (DATETIME_TYPES.has(fieldType)) {
    return (
      <div className="grid grid-cols-2 gap-3" data-testid="dynamic-field-config-datetime">
        <div>
          <span className={labelClass}>{t("admin.dynamicFields.yearsInPast")}</span>
          <input
            type="number"
            data-testid="dynamic-field-years-in-past"
            value={(value.YearsInPast as number | undefined) ?? ""}
            onChange={(e) => set("YearsInPast", e.target.value === "" ? "" : Number(e.target.value))}
            className={inputClass}
          />
        </div>
        <div>
          <span className={labelClass}>{t("admin.dynamicFields.yearsInFuture")}</span>
          <input
            type="number"
            data-testid="dynamic-field-years-in-future"
            value={(value.YearsInFuture as number | undefined) ?? ""}
            onChange={(e) =>
              set("YearsInFuture", e.target.value === "" ? "" : Number(e.target.value))
            }
            className={inputClass}
          />
        </div>
      </div>
    );
  }

  if (fieldType === "Checkbox") {
    return (
      <label className="flex items-center gap-2 text-sm text-ink" data-testid="dynamic-field-config-checkbox">
        <input
          type="checkbox"
          data-testid="dynamic-field-default-value"
          checked={Boolean(value.DefaultValue)}
          onChange={(e) => set("DefaultValue", e.target.checked || undefined)}
          className="h-4 w-4 rounded border-hairline"
        />
        {t("admin.dynamicFields.defaultValue")}
      </label>
    );
  }

  if (TEXT_TYPES.has(fieldType)) {
    return (
      <div data-testid="dynamic-field-config-text">
        <span className={labelClass}>{t("admin.dynamicFields.defaultValue")}</span>
        <input
          data-testid="dynamic-field-default-value"
          value={(value.DefaultValue as string | undefined) ?? ""}
          onChange={(e) => set("DefaultValue", e.target.value)}
          className={inputClass}
        />
      </div>
    );
  }

  return null;
}
