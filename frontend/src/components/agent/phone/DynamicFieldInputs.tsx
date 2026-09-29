import { useTranslation } from "react-i18next";
import type { DynamicFieldDef } from "@/lib/phoneApi";

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";

export type DynamicFieldValues = Record<string, string[]>;

/**
 * Editors for ticket dynamic fields as `/reference/dynamic-fields` describes
 * them — text, textarea, dropdown, multiselect, checkbox, date, date-time.
 * Values are Znuny's string lists (`{name: [values]}`), exactly what the
 * ticket create / phone-call APIs take.
 */
export function DynamicFieldInputs({
  fields,
  values,
  onChange,
  testId = "df",
}: {
  fields: DynamicFieldDef[];
  values: DynamicFieldValues;
  onChange: (name: string, values: string[]) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  if (fields.length === 0) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-2" data-testid={testId}>
      {fields.map((f) => {
        const current = values[f.name] ?? [];
        const first = current[0] ?? "";
        const id = `${testId}-${f.name}`;
        const set = (v: string) => onChange(f.name, v === "" ? [] : [v]);
        let editor;
        switch (f.field_type) {
          case "TextArea":
            editor = (
              <textarea id={id} data-testid={id} rows={2} className={inputCls} value={first} onChange={(e) => set(e.target.value)} />
            );
            break;
          case "Dropdown":
            editor = (
              <select id={id} data-testid={id} className={inputCls} value={first} onChange={(e) => set(e.target.value)}>
                <option value="">{t("phone.dfNone")}</option>
                {Object.entries(f.possible_values ?? {}).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            );
            break;
          case "Multiselect":
            editor = (
              <select
                id={id}
                data-testid={id}
                multiple
                className={inputCls}
                value={current}
                onChange={(e) => onChange(f.name, Array.from(e.target.selectedOptions, (o) => o.value))}
              >
                {Object.entries(f.possible_values ?? {}).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            );
            break;
          case "Checkbox":
            editor = (
              <input
                id={id}
                data-testid={id}
                type="checkbox"
                className="h-4 w-4 accent-[var(--color-accent)]"
                checked={first === "1"}
                onChange={(e) => onChange(f.name, [e.target.checked ? "1" : "0"])}
              />
            );
            break;
          case "Date":
            editor = (
              <input id={id} data-testid={id} type="date" className={inputCls} value={first.slice(0, 10)} onChange={(e) => set(e.target.value ? `${e.target.value} 00:00:00` : "")} />
            );
            break;
          case "DateTime":
            editor = (
              <input
                id={id}
                data-testid={id}
                type="datetime-local"
                className={inputCls}
                value={first ? first.slice(0, 16).replace(" ", "T") : ""}
                onChange={(e) => set(e.target.value ? `${e.target.value.replace("T", " ")}:00` : "")}
              />
            );
            break;
          default:
            editor = <input id={id} data-testid={id} className={inputCls} value={first} onChange={(e) => set(e.target.value)} />;
        }
        return (
          <label key={f.name} htmlFor={id} className="block text-xs text-muted">
            <span>
              {f.label}
              {f.required && <span className="text-danger"> *</span>}
            </span>
            <span className="mt-0.5 block">{editor}</span>
          </label>
        );
      })}
    </div>
  );
}
