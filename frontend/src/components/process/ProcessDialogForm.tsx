import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { FieldsForm } from "@/components/fields";
import type { FieldErrors, FieldValue, FieldValues } from "@/components/fields";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { processFieldDefs } from "./processForm";

/** The server-built fields of a process dialog on the field engine. The submit button belongs to the caller. */
export function ProcessDialogForm({
  form,
  values,
  onChange,
  errors,
  disabled,
  testId = "process-form",
}: {
  form: ProcessDialogFormData;
  values: FieldValues;
  onChange: (key: string, value: FieldValue) => void;
  errors?: FieldErrors;
  disabled?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const defs = useMemo(() => processFieldDefs(form, t), [form, t]);
  return (
    <div className="space-y-3" data-testid={`${testId}-root`}>
      {form.description_short && <p className="text-sm text-muted">{form.description_short}</p>}
      <FieldsForm
        fields={defs}
        values={values}
        onChange={onChange}
        errors={errors}
        disabled={disabled}
        columns={1}
        testId={testId}
      />
    </div>
  );
}
