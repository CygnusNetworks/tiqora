import { useMemo } from "react";
import type { DynamicFieldDef } from "@/lib/phoneApi";
import { displayTimeZone } from "@/lib/timeZone";
import { dfToFieldValue, dynamicFieldToDef, fieldValueToDf, type DynamicFieldValues } from "./dynamicFields";
import { FieldsForm } from "./FieldsForm";
import type { FieldValue, FieldValues } from "./types";

/**
 * Editors for ticket dynamic fields as `/reference/dynamic-fields` describes
 * them, rendered by the field engine. Values stay Znuny's string lists
 * (`{name: [values]}`) — exactly what the ticket create / phone-call APIs take;
 * DateTime values are UTC in that list and shown in the agent's zone.
 */
export function DynamicFieldsForm({
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
  const defs = useMemo(() => fields.map(dynamicFieldToDef), [fields]);
  const zone = displayTimeZone();
  const engineValues = useMemo(() => {
    const out: FieldValues = {};
    for (const d of defs) out[d.key] = dfToFieldValue(d.kind, values[d.key] ?? [], zone);
    return out;
  }, [defs, values, zone]);
  const kindOf = (name: string) => defs.find((d) => d.key === name)?.kind ?? "text";
  return (
    <FieldsForm
      fields={defs}
      values={engineValues}
      columns={2}
      testId={testId}
      onChange={(key: string, v: FieldValue) => onChange(key, fieldValueToDf(kindOf(key), v, zone))}
    />
  );
}
