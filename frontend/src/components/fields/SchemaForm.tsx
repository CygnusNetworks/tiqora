import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { FieldsForm } from "./FieldsForm";
import { schemaToFieldDefs, type JsonSchema } from "./jsonSchema";
import { registerCoreFieldKinds } from "./kinds";
import type { FieldErrors, FieldValues } from "./types";

registerCoreFieldKinds();

/** A form generated from a JSON schema; unsupported properties are shown, never dropped. */
export function SchemaForm({
  schema,
  values,
  onChange,
  errors,
  i18nPrefix,
  secretsSet,
  disabled,
  columns,
  testId = "schema-form",
}: {
  schema: JsonSchema;
  values: FieldValues;
  onChange: (next: FieldValues, changedKey: string) => void;
  errors?: FieldErrors;
  i18nPrefix?: string;
  secretsSet?: string[];
  disabled?: boolean;
  columns?: 1 | 2;
  testId?: string;
}) {
  const { t } = useTranslation();
  const { fields, unsupported } = useMemo(
    () => schemaToFieldDefs(schema, { t, i18nPrefix, secretsSet }),
    [schema, t, i18nPrefix, secretsSet],
  );
  return (
    <div className="space-y-3">
      <FieldsForm
        fields={fields}
        values={values}
        onChange={(key, value) => onChange({ ...values, [key]: value }, key)}
        errors={errors}
        disabled={disabled}
        columns={columns}
        testId={testId}
      />
      {unsupported.map((key) => (
        <p key={key} data-testid={`${testId}-unsupported-${key}`} className="text-xs text-muted">
          <span className="font-medium">{schema.properties?.[key]?.title ?? key}</span>: {t("fields.unsupported")}
        </p>
      ))}
    </div>
  );
}
