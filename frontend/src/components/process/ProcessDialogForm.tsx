import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FieldsForm } from "@/components/fields";
import type { FieldErrors, FieldOption, FieldValue, FieldValues } from "@/components/fields";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { ARTICLE_BODY_KEY, processFieldDefs, type ProcessScope } from "./processForm";

/** The server-built fields of a process dialog on the field engine. The submit button belongs to the caller. */
export function ProcessDialogForm({
  form,
  values,
  onChange,
  errors,
  disabled,
  testId = "process-form",
  scope = "agent",
  optionsByKey,
  uploads,
}: {
  form: ProcessDialogFormData;
  values: FieldValues;
  onChange: (key: string, value: FieldValue) => void;
  errors?: FieldErrors;
  disabled?: boolean;
  testId?: string;
  /** The portal never shows the customer search or the ticket's attachments. */
  scope?: ProcessScope;
  /** Refreshed options per field name (`useProcessFormOptions`). */
  optionsByKey?: Record<string, FieldOption[]>;
  /** Upload area, shown under the article text. */
  uploads?: ReactNode;
}) {
  const { t } = useTranslation();
  const defs = useMemo(() => processFieldDefs(form, t, scope), [form, t, scope]);
  return (
    <div className="space-y-3" data-testid={`${testId}-root`}>
      {form.description_short && <p className="text-sm text-muted">{form.description_short}</p>}
      <FieldsForm
        fields={defs}
        values={values}
        onChange={onChange}
        errors={errors}
        optionsByKey={optionsByKey}
        extras={uploads ? { [ARTICLE_BODY_KEY]: uploads } : undefined}
        disabled={disabled}
        columns={1}
        testId={testId}
      />
    </div>
  );
}
